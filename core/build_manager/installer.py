"""Wireless and wired app deployment using official Xcode devicectl."""

import asyncio
import os
import plistlib
import time
from core.build_manager.state import InstallResult
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.installer")


class AppInstaller:
    """Installs the compiled iOS application onto physical devices via devicectl."""

    @staticmethod
    async def verify_installed_app(
        device_identifier: str, bundle_id: str
    ) -> tuple[bool, str | None, str | None]:
        """
        Verify that the application is actually installed on the physical device
        using official Xcode xcrun devicectl device info apps.
        Returns: (is_installed, version, app_name)
        """
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
            tmp_json = tmp_file.name

        try:
            cmd = [
                "xcrun", "devicectl", "device", "info", "apps",
                "--device", device_identifier,
                "--bundle-id", bundle_id,
                "--include-all-apps",
                "--json-output", tmp_json,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await proc.communicate()

            if not os.path.exists(tmp_json) or os.path.getsize(tmp_json) == 0:
                return False, None, None

            with open(tmp_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            apps = data.get("result", {}).get("apps", [])
            for app in apps:
                if app.get("bundleIdentifier") == bundle_id:
                    version = app.get("bundleVersion") or app.get("version")
                    name = app.get("name")
                    return True, version, name

            return False, None, None
        except Exception as exc:
            logger.warning("devicectl_verify_app_failed", error=str(exc))
            return False, None, None
        finally:
            if os.path.exists(tmp_json):
                try:
                    os.remove(tmp_json)
                except OSError:
                    pass

    @classmethod
    async def install(
        cls,
        device_identifier: str,
        app_bundle_path: str,
        bundle_id: str = "com.mfatihc.jarvis",
    ) -> InstallResult:
        """Execute xcrun devicectl device install app and verify installation."""
        start_time = time.monotonic()

        if not os.path.exists(app_bundle_path):
            return InstallResult(
                success=False,
                device_id=device_identifier,
                bundle_id=bundle_id,
                error=f"App bundle not found at {app_bundle_path}",
                duration_seconds=0.0,
            )

        cmd = [
            "xcrun", "devicectl", "device", "install", "app",
            "--timeout", "60",
            "--device", device_identifier,
            app_bundle_path
        ]

        with open(os.path.join(app_bundle_path, "Info.plist"), "rb") as f:
            expected_version = str(plistlib.load(f)["CFBundleVersion"])

        logger.info(
            "devicectl_install_starting",
            device_id=device_identifier,
            app_path=app_bundle_path
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            duration = round(time.monotonic() - start_time, 2)

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")
            output_combined = (stdout_str + "\n" + stderr_str).strip()

            device_locked = any(
                term in output_combined.lower()
                for term in ["passcode", "device is locked", "locked", "unlock your device"]
            )

            if proc.returncode != 0:
                err_msg = stderr_str.strip() or stdout_str.strip()
                if device_locked:
                    err_msg = "Device is passcode locked. Please unlock your iPhone to complete installation."
                elif "developer mode" in output_combined.lower():
                    err_msg = "Developer Mode authorization required on iPhone."

                logger.error(
                    "devicectl_install_failed",
                    returncode=proc.returncode,
                    error=err_msg,
                    device_locked=device_locked,
                    duration=duration
                )
                return InstallResult(
                    success=False,
                    device_id=device_identifier,
                    bundle_id=bundle_id,
                    error=err_msg,
                    duration_seconds=duration,
                    device_locked=device_locked,
                    requires_user_action=device_locked or "developer mode" in output_combined.lower(),
                )

            # Verification: query installed apps via devicectl to verify application presence
            verified, version, _ = await cls.verify_installed_app(device_identifier, bundle_id)
            if not verified:
                # If exit code was 0 but app verification query didn't find it yet, retry once after short delay
                await asyncio.sleep(1.0)
                verified, version, _ = await cls.verify_installed_app(device_identifier, bundle_id)

            if not verified or str(version) != expected_version:
                err_msg = (
                    f"Installation command returned code 0, but app '{bundle_id}' build {expected_version} could not be verified on device (reported {version}) "
                    f"via 'devicectl device info apps'. Exit code zero alone is not accepted as proof."
                )
                logger.warning("devicectl_install_unverified", device_id=device_identifier)
                return InstallResult(
                    success=False,
                    device_id=device_identifier,
                    bundle_id=bundle_id,
                    error=err_msg,
                    duration_seconds=duration,
                    device_locked=False,
                    verified=False
                )

            logger.info("devicectl_install_success_verified", device_id=device_identifier, version=version, duration=duration)

            return InstallResult(
                success=True,
                device_id=device_identifier,
                bundle_id=bundle_id,
                error=None,
                duration_seconds=duration,
                device_locked=False,
                verified=True,
                installed_version=version,
                installed_bundle_id=bundle_id
            )

        except Exception as exc:
            duration = round(time.monotonic() - start_time, 2)
            logger.error("devicectl_install_exception", error=str(exc))
            return InstallResult(
                success=False,
                device_id=device_identifier,
                bundle_id=bundle_id,
                error=str(exc),
                duration_seconds=duration,
                device_locked=False,
                verified=False
            )
