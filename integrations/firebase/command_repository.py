"""Firestore CommandRepository implementation for /users/{uid}/commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from core.cloud.models import CloudCommand, CommandStatus
from core.cloud.repositories import CommandConflictError, CommandRepository
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from core.models.tools import RiskLevel
from integrations.firebase.client import FirestoreClientProvider

logger = get_logger("jarvis.firebase.command")


class FirestoreCommandRepository(CommandRepository):
    """Remote command repository utilizing Firestore under /users/{uid}/commands."""

    def __init__(
        self,
        client_provider: FirestoreClientProvider | None = None,
        uid: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client_provider = client_provider or FirestoreClientProvider(settings=self._settings)
        self._uid = uid or self._settings.jarvis_uid

    def _get_collection(self) -> Any:
        client = self._client_provider.get_client()
        return client.collection("users").document(self._uid).collection("commands")

    def _cmd_to_doc(self, cmd: CloudCommand) -> dict[str, Any]:
        return {
            "id": cmd.id,
            "user_id": cmd.user_id or self._uid,
            "type": cmd.type,
            "name": cmd.name,
            "source_device": cmd.source_device,
            "target_device": cmd.target_device,
            "payload": cmd.payload,
            "risk_level": cmd.risk_level.value if cmd.risk_level else None,
            "status": cmd.status.value,
            "idempotency_key": cmd.idempotency_key,
            "created_at": cmd.created_at.isoformat(),
            "available_at": cmd.available_at.isoformat(),
            "expires_at": cmd.expires_at.isoformat() if cmd.expires_at else None,
            "lease_owner": cmd.lease_owner,
            "lease_expires_at": cmd.lease_expires_at.isoformat() if cmd.lease_expires_at else None,
            "attempts": cmd.attempts,
            "result": cmd.result,
            "error": cmd.error,
            "revision": cmd.revision,
        }

    def _doc_to_cmd(self, data: dict[str, Any]) -> CloudCommand:
        created_at = datetime.fromisoformat(data["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        available_at = datetime.fromisoformat(data["available_at"])
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=timezone.utc)

        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

        lease_expires = None
        if data.get("lease_expires_at"):
            lease_expires = datetime.fromisoformat(data["lease_expires_at"])
            if lease_expires.tzinfo is None:
                lease_expires = lease_expires.replace(tzinfo=timezone.utc)

        risk = RiskLevel(data["risk_level"]) if data.get("risk_level") is not None else None

        return CloudCommand(
            id=data["id"],
            user_id=data.get("user_id"),
            type=data.get("type", "tool_execution"),
            name=data["name"],
            source_device=data.get("source_device", "unknown"),
            target_device=data.get("target_device"),
            payload=data.get("payload", {}),
            risk_level=risk,
            status=CommandStatus(data.get("status", CommandStatus.QUEUED.value)),
            idempotency_key=data["idempotency_key"],
            created_at=created_at,
            available_at=available_at,
            expires_at=expires_at,
            lease_owner=data.get("lease_owner"),
            lease_expires_at=lease_expires,
            attempts=int(data.get("attempts", 0)),
            result=data.get("result"),
            error=data.get("error"),
            revision=int(data.get("revision", 1)),
        )

    async def get(self, command_id: str) -> CloudCommand | None:
        doc = await self._get_collection().document(command_id).get()
        if not doc.exists:
            return None
        return self._doc_to_cmd(doc.to_dict())

    async def find_by_idempotency_key(self, idempotency_key: str) -> CloudCommand | None:
        snaps = await self._get_collection().where("idempotency_key", "==", idempotency_key).limit(1).get()
        if not snaps:
            return None
        return self._doc_to_cmd(snaps[0].to_dict())

    async def create(self, command: CloudCommand) -> CloudCommand:
        existing = await self.find_by_idempotency_key(command.idempotency_key)
        if existing:
            return existing

        doc_ref = self._get_collection().document(command.id)
        await doc_ref.set(self._cmd_to_doc(command))
        return command

    async def update(self, command: CloudCommand, expected_revision: int | None = None) -> CloudCommand:
        from google.cloud import firestore
        doc_ref = self._get_collection().document(command.id)
        expected = command.revision if expected_revision is None else expected_revision

        @firestore.async_transactional
        async def commit(transaction):
            snap = await doc_ref.get(transaction=transaction)
            if not snap.exists:
                raise ValueError("Command not found")
            if int(snap.to_dict().get("revision", 1)) != expected:
                raise CommandConflictError("Command changed during execution")
            updated = command.model_copy(update={"revision": expected + 1})
            transaction.set(doc_ref, self._cmd_to_doc(updated))
            return updated

        return await commit(self._client_provider.get_client().transaction())

    async def lease_next_command(self, worker_id: str, lease_duration_seconds: int = 60) -> CloudCommand | None:
        from google.cloud import firestore
        import hashlib
        col = self._get_collection()
        snapshots = await col.where("status", "in", ["queued", "leased", "running"]).get()
        snapshots.sort(key=lambda snap: str(snap.to_dict().get("created_at", "")))
        for candidate in snapshots:
            ref = candidate.reference

            @firestore.async_transactional
            async def claim(transaction):
                snap = await ref.get(transaction=transaction)
                if not snap.exists:
                    return None
                cmd = self._doc_to_cmd(snap.to_dict())
                now = datetime.now(timezone.utc)
                if cmd.target_device and cmd.target_device != worker_id:
                    return None
                if cmd.status not in (CommandStatus.QUEUED, CommandStatus.LEASED, CommandStatus.RUNNING):
                    return None
                if cmd.status != CommandStatus.QUEUED and (not cmd.lease_expires_at or cmd.lease_expires_at > now):
                    return None
                if cmd.status == CommandStatus.RUNNING:
                    # An interrupted external mutation has an uncertain outcome.
                    # Never replay it automatically after sleep/process death.
                    transaction.update(ref, {"status": "failed", "error": "Execution interrupted; verify the previous result before requesting another action.", "revision": cmd.revision + 1})
                    return None
                if cmd.expires_at and cmd.expires_at <= now:
                    transaction.update(ref, {"status": "expired", "revision": cmd.revision + 1})
                    return None
                if cmd.available_at > now:
                    return None
                key_id = hashlib.sha256(cmd.idempotency_key.encode()).hexdigest()
                key_ref = col.parent.collection("command_keys").document(key_id)
                key_snap = await key_ref.get(transaction=transaction)
                if key_snap.exists and key_snap.to_dict().get("command_id") != cmd.id:
                    transaction.update(ref, {"status": "failed", "error": "Duplicate idempotency key", "revision": cmd.revision + 1})
                    return None
                cmd.status = CommandStatus.LEASED
                cmd.lease_owner = worker_id
                cmd.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
                cmd.attempts += 1
                cmd.revision += 1
                transaction.set(key_ref, {"command_id": cmd.id})
                transaction.set(ref, self._cmd_to_doc(cmd))
                return cmd

            leased = await claim(self._client_provider.get_client().transaction())
            if leased:
                return leased
        return None

    async def list(self, status: CommandStatus | None = None) -> list[CloudCommand]:
        col = self._get_collection()
        query = col
        if status is not None:
            query = query.where("status", "==", status.value)

        snaps = await query.get()
        return [self._doc_to_cmd(s.to_dict()) for s in snaps]
