"""Firestore DeviceRepository implementation under /users/{uid}/devices."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from core.cloud.models import DeviceRecord, DeviceStatus
from core.cloud.repositories import DeviceRepository
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from integrations.firebase.client import FirestoreClientProvider

logger = get_logger("jarvis.firebase.device")


class FirestoreDeviceRepository(DeviceRepository):
    """Tracks personal device fleet in Firestore under /users/{uid}/devices."""

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
        return client.collection("users").document(self._uid).collection("devices")

    def _dev_to_doc(self, dev: DeviceRecord) -> dict[str, Any]:
        return {
            "device_id": dev.device_id,
            "type": dev.type,
            "name": dev.name,
            "status": dev.status.value,
            "app_version": dev.app_version,
            "capabilities": dev.capabilities,
            "last_seen_at": dev.last_seen_at.isoformat(),
            "created_at": dev.created_at.isoformat(),
        }

    def _doc_to_dev(self, data: dict[str, Any]) -> DeviceRecord:
        last_seen = datetime.fromisoformat(data["last_seen_at"])
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        created = datetime.fromisoformat(data["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        return DeviceRecord(
            device_id=data["device_id"],
            type=data["type"],
            name=data["name"],
            status=DeviceStatus(data.get("status", DeviceStatus.ONLINE.value)),
            app_version=data.get("app_version"),
            capabilities=data.get("capabilities", []),
            last_seen_at=last_seen,
            created_at=created,
        )

    async def register_or_update(self, device: DeviceRecord) -> DeviceRecord:
        device.last_seen_at = datetime.now(timezone.utc)
        doc_ref = self._get_collection().document(device.device_id)
        await doc_ref.set(self._dev_to_doc(device))
        return device

    async def get(self, device_id: str) -> DeviceRecord | None:
        snap = await self._get_collection().document(device_id).get()
        if not snap.exists:
            return None
        return self._doc_to_dev(snap.to_dict())

    async def list(self) -> list[DeviceRecord]:
        snaps = await self._get_collection().get()
        return [self._doc_to_dev(s.to_dict()) for s in snaps]

    async def heartbeat(self, device_id: str, status: DeviceStatus = DeviceStatus.ONLINE) -> bool:
        doc_ref = self._get_collection().document(device_id)
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            await doc_ref.update({
                "status": status.value,
                "last_seen_at": now_str,
            })
            return True
        except Exception:
            return False

    async def register(self, device: DeviceRecord) -> DeviceRecord:
        """Alias for register_or_update."""
        return await self.register_or_update(device)

    async def update_heartbeat(self, device_id: str, status: DeviceStatus = DeviceStatus.ONLINE) -> bool:
        """Alias for heartbeat."""
        return await self.heartbeat(device_id, status)
