"""Build orchestrator invoking xcodebuild with Automatic Signing and device targeting."""

import asyncio
from datetime import datetime, timezone
import os
import shutil
import time
from core.build_manager.signing import SigningInspector
from core.build_manager.state import BuildArtifact, BuildResult
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.builder")


class XcodeBuilder:
    """Executes xcodebuild targeting the connected iOS device."""

    def __init__(
        self,
        project_dir: str = "ios/JarvisiOS",
        project_name: str = "JarvisiOS.xcodeproj",
        scheme: str = "JarvisiOS",
        build_cache_dir: str | None = None,
    ) -> None:
        self.project_path = os.path.abspath(os.path.join(project_dir, project_name))
        self.scheme = scheme
        self.build_cache_dir = os.path.abspath(
            build_cache_dir or os.path.expanduser("~/.cache/jarvis/build")
        )
        os.makedirs(self.build_cache_dir, exist_ok=True)

    async def build(
        self,
        device_destination: str | None = None,
        allow_provisioning_updates: bool = True,
        configuration: str = "Debug",
        clean_first: bool = False,
        code_signing_allowed: bool = True,
    ) -> BuildResult:
        """Run xcodebuild to build the iOS application."""
        start_time = time.monotonic()
        derived_data_path = os.path.join(self.build_cache_dir, "DerivedData")
        os.makedirs(derived_data_path, exist_ok=True)

        if not os.path.exists(self.project_path):
            return BuildResult(
                success=False,
                error=f"Xcode project not found at: {self.project_path}",
                duration_seconds=0.0
            )

        destination = device_destination or "generic/platform=iOS"
        cmd = [
            "xcodebuild",
            "-project", self.project_path,
            "-scheme", self.scheme,
            "-configuration", configuration,
            "-destination", destination,
            "-derivedDataPath", derived_data_path,
        ]

        if not code_signing_allowed:
            cmd.append("CODE_SIGNING_ALLOWED=NO")
        elif allow_provisioning_updates:
            cmd.append("-allowProvisioningUpdates")

        if clean_first:
            cmd.append("clean")
        cmd.append("build")

        logger.info(
            "xcodebuild_starting",
            scheme=self.scheme,
            destination=destination,
            allow_updates=allow_provisioning_updates
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
            full_logs = stdout_str + "\n" + stderr_str

            if proc.returncode != 0:
                is_user_action = SigningInspector.detect_user_action_required(full_logs)
                logger.error(
                    "xcodebuild_failed",
                    returncode=proc.returncode,
                    duration=duration,
                    user_action_required=is_user_action
                )
                return BuildResult(
                    success=False,
                    error=stderr_str.strip() or stdout_str[-500:],
                    duration_seconds=duration,
                    logs=full_logs,
                    requires_user_action=is_user_action
                )

            # Locate compiled .app bundle
            app_path = os.path.join(
                derived_data_path,
                "Build", "Products", f"{configuration}-iphoneos",
                f"{self.scheme}.app"
            )

            if not os.path.exists(app_path):
                # Search for .app if placed under an alternative subpath
                found_apps = [
                    os.path.join(root, d)
                    for root, dirs, _ in os.walk(derived_data_path)
                    for d in dirs if d == f"{self.scheme}.app"
                ]
                if found_apps:
                    app_path = found_apps[0]
                else:
                    return BuildResult(
                        success=False,
                        error="Build succeeded but .app bundle was not found in derived data.",
                        duration_seconds=duration,
                        logs=full_logs
                    )

            is_codesigned = await SigningInspector.verify_codesign(app_path)
            artifact = BuildArtifact(
                app_path=app_path,
                bundle_id="com.mfatihc.jarvis",
                version="1.0.0",
                created_at=datetime.now(timezone.utc),
                codesign_valid=is_codesigned,
            )

            logger.info("xcodebuild_succeeded", app_path=app_path, codesign_valid=is_codesigned, duration=duration)
            return BuildResult(
                success=True,
                artifact=artifact,
                duration_seconds=duration,
                logs=full_logs,
                requires_user_action=False
            )

        except Exception as exc:
            duration = round(time.monotonic() - start_time, 2)
            logger.error("xcodebuild_exception", error=str(exc))
            return BuildResult(
                success=False,
                error=str(exc),
                duration_seconds=duration,
                requires_user_action=False
            )
