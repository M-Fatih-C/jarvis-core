"""Provisioning profile parser, signature inspector, and renewal evaluator."""

import asyncio
from datetime import datetime, timezone
import os
import plistlib
from typing import Any
from core.build_manager.state import ProvisioningInfo
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.signing")


class SigningInspector:
    """Inspects embedded.mobileprovision and codesign signatures."""

    @staticmethod
    async def parse_provisioning_profile(profile_path: str) -> ProvisioningInfo:
        """Decode and parse an embedded.mobileprovision file using security cms -D."""
        if not os.path.exists(profile_path):
            return ProvisioningInfo(
                profile_path=profile_path,
                error=f"Provisioning profile not found at {profile_path}"
            )

        try:
            cmd = ["security", "cms", "-D", "-i", profile_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error("cms_decode_failed", error=err_msg, returncode=proc.returncode)
                return ProvisioningInfo(profile_path=profile_path, error=f"Decode error: {err_msg}")

            plist_data: dict[str, Any] = plistlib.loads(stdout)

            exp_date: datetime | None = plist_data.get("ExpirationDate")
            create_date: datetime | None = plist_data.get("CreationDate")
            team_ids = plist_data.get("TeamIdentifier", [])
            team_id = team_ids[0] if isinstance(team_ids, list) and team_ids else str(team_ids)
            team_name = plist_data.get("TeamName")
            entitlements = plist_data.get("Entitlements", {})
            app_id = entitlements.get("application-identifier")

            now = datetime.now(timezone.utc)
            days_remaining: float | None = None
            is_expired = False
            needs_renewal = False

            if exp_date:
                if exp_date.tzinfo is None:
                    exp_date = exp_date.replace(tzinfo=timezone.utc)
                diff_seconds = (exp_date - now).total_seconds()
                days_remaining = max(0.0, diff_seconds / 86400.0)
                is_expired = diff_seconds <= 0
                # Target renewal window: ~2 days before expiration
                needs_renewal = is_expired or (days_remaining <= 2.0)

            if create_date and create_date.tzinfo is None:
                create_date = create_date.replace(tzinfo=timezone.utc)

            info = ProvisioningInfo(
                profile_path=profile_path,
                app_identifier=app_id,
                team_identifier=team_id,
                team_name=team_name,
                creation_date=create_date,
                expiration_date=exp_date,
                days_remaining=round(days_remaining, 2) if days_remaining is not None else None,
                is_expired=is_expired,
                needs_renewal=needs_renewal,
                user_action_required=False,
                provisioned_devices=plist_data.get("ProvisionedDevices", []),
            )
            logger.info(
                "provisioning_profile_parsed",
                days_remaining=info.days_remaining,
                needs_renewal=info.needs_renewal,
                app_id=info.app_identifier
            )
            return info

        except Exception as exc:
            logger.error("provisioning_parse_exception", error=str(exc))
            return ProvisioningInfo(profile_path=profile_path, error=str(exc))

    @staticmethod
    def detect_user_action_required(error_text: str) -> bool:
        """Check if an xcodebuild failure stems from Apple login / 2FA requirements."""
        indicators = [
            "No Accounts: Add a new account in Accounts settings",
            "Apple ID session has expired",
            "Two-factor authentication is required",
            "Your session has expired. Please log in again",
            "authentication failed",
            "requires a development team with active membership",
        ]
        lower_err = error_text.lower()
        return any(ind.lower() in lower_err for ind in indicators)

    @staticmethod
    async def verify_codesign(app_bundle_path: str) -> bool:
        """Run codesign --verify --deep --strict on the built app bundle."""
        if not os.path.exists(app_bundle_path):
            return False

        try:
            cmd = ["codesign", "--verify", "--deep", "--strict", app_bundle_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, _ = await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    @classmethod
    async def verify_artifact_integrity(
        cls, app_bundle_path: str, expected_bundle_id: str, expected_device_udid: str | None = None
    ) -> tuple[bool, str | None]:
        """
        Verify bundle identifier, signing identity, codesign validity,
        and embedded provisioning profile before allowing device installation.
        """
        if not os.path.exists(app_bundle_path):
            return False, f"App bundle not found at {app_bundle_path}"

        # 1. Verify codesign validity
        valid_sign = await cls.verify_codesign(app_bundle_path)
        if not valid_sign:
            return False, "Codesign verification failed (--verify --deep --strict failed)"

        # 2. Extract signing details using codesign -dvvv
        try:
            cmd = ["codesign", "-dvvv", app_bundle_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            codesign_info = (
                stderr.decode("utf-8", errors="replace") + "\n" +
                stdout.decode("utf-8", errors="replace")
            )

            # Check bundle identifier
            bundle_id_match = False
            for line in codesign_info.splitlines():
                if line.startswith("Identifier="):
                    actual_id = line.split("=", 1)[1].strip()
                    if actual_id == expected_bundle_id:
                        bundle_id_match = True
                    else:
                        return False, f"Bundle ID mismatch: expected {expected_bundle_id}, got {actual_id}"

            if not bundle_id_match:
                return False, f"Could not verify Identifier={expected_bundle_id} from codesign metadata"

            # Check signing identity exists
            has_authority = any("Authority=" in line for line in codesign_info.splitlines())
            if not has_authority:
                return False, "No valid signing Authority found in code signature"

        except Exception as exc:
            return False, f"Error inspecting code signature: {exc}"

        # 3. Check embedded provisioning profile
        prof_path = os.path.join(app_bundle_path, "embedded.mobileprovision")
        if not os.path.exists(prof_path):
            return False, "Missing embedded.mobileprovision in app bundle"

        prov_info = await cls.parse_provisioning_profile(prof_path)
        if prov_info.error:
            return False, f"Provisioning profile invalid: {prov_info.error}"

        if prov_info.is_expired:
            return False, f"Provisioning profile is expired (expired at {prov_info.expiration_date})"

        # Check provisioning app identifier
        if not prov_info.expiration_date or not prov_info.app_identifier or not prov_info.team_identifier:
            return False, "Provisioning profile is missing required validity or identity fields"
        if prov_info.app_identifier != f"{prov_info.team_identifier}.{expected_bundle_id}":
            return False, f"Provisioning profile app ID {prov_info.app_identifier} does not match {expected_bundle_id}"
        if expected_device_udid and expected_device_udid not in prov_info.provisioned_devices:
            return False, "Provisioning profile does not authorize the approved iPhone"

        return True, None
