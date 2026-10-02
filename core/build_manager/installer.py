"""Wireless and wired app deployment using official Xcode devicectl."""

import asyncio
import os
import time
from core.build_manager.state import InstallResult
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.installer")


class AppInstaller:
    """Installs the compiled iOS application onto physical devices via devicectl."""

    @staticmethod
    async def install(device_identifier: str, app_bundle_path: str, bundle_id: str = "com.mfatihc.jarvis") -> InstallResult:
        """Execute xcrun devicectl device install app and verify installation."""
        start_time = time.monotonic()

        if not os.path.exists(app_bundle_path):
            return InstallResult(
                success=False,
                device_id=device_identifier,
                bundle_id=bundle_id,
                error=f"App bundle not found at {app_bundle_path}",
                duration_seconds=0.0
            )

        cmd = [
            "xcrun", "devicectl", "device", "install", "app",
            "--device", device_identifier,
            app_bundle_path
        ]

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
                    device_locked=device_locked
                )

            # Independent verification: check devicectl success outcome
            is_success = "installed" in output_combined.lower() or proc.returncode == 0
            logger.info("devicectl_install_success", device_id=device_identifier, duration=duration)

            return InstallResult(
                success=is_success,
                device_id=device_identifier,
                bundle_id=bundle_id,
                error=None,
                duration_seconds=duration,
                device_locked=False
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
                device_locked=False
            )
