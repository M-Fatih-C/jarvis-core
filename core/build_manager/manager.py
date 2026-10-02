"""Central coordinator for iOS Build, Provisioning Renewal, and Device Installation."""

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any
from core.build_manager.builder import XcodeBuilder
from core.build_manager.device_monitor import DeviceMonitor
from core.build_manager.installer import AppInstaller
from core.build_manager.notifier import BuildNotifier
from core.build_manager.signing import SigningInspector
from core.build_manager.state import (
    BuildLifecycleState,
    BuildManagerState,
    BuildResult,
    DeviceInfo,
    InstallResult,
    NotificationEvent,
    ProvisioningInfo,
)
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.manager")


class BuildManager:
    """Manages iOS application compilation, provisioning expiration tracking, and installation."""

    def __init__(
        self,
        project_dir: str = "ios/JarvisiOS",
        target_device_name: str = "Fatih",
        target_bundle_id: str = "com.mfatihc.jarvis",
        state_file: str | None = None,
    ) -> None:
        self.target_device_name = target_device_name
        self.target_bundle_id = target_bundle_id
        self.state_file = os.path.abspath(
            state_file or os.path.expanduser("~/.cache/jarvis/build_manager_state.json")
        )
        self.device_monitor = DeviceMonitor(default_target_name=target_device_name)
        self.builder = XcodeBuilder(project_dir=project_dir)
        self.installer = AppInstaller()
        self.signing_inspector = SigningInspector()
        self.notifier = BuildNotifier()

        self._lock = asyncio.Lock()
        self.state = self._load_state()

    def _load_state(self) -> BuildManagerState:
        """Load persisted state or initialize default state."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return BuildManagerState.model_validate(data)
            except Exception as exc:
                logger.warning("failed_loading_build_manager_state", error=str(exc))
        return BuildManagerState(
            target_device_name=self.target_device_name,
            target_bundle_id=self.target_bundle_id,
        )

    def _save_state(self) -> None:
        """Persist state to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state.model_dump(mode="json"), f, indent=2)
        except Exception as exc:
            logger.error("failed_saving_build_manager_state", error=str(exc))

    async def get_status(self) -> dict[str, Any]:
        """Return the complete current status of the build manager, device, and provisioning."""
        # Query target device status
        dev = await self.device_monitor.get_target_device(self.state.target_device_name)
        self.state.last_device_info = dev
        self.state.last_checked_at = datetime.now(timezone.utc)

        # Check existing provisioning if artifact exists
        prov_info: ProvisioningInfo | None = None
        if self.state.last_build_result and self.state.last_build_result.artifact:
            app_path = self.state.last_build_result.artifact.app_path
            prof_path = os.path.join(app_path, "embedded.mobileprovision")
            if os.path.exists(prof_path):
                prov_info = await self.signing_inspector.parse_provisioning_profile(prof_path)
                self.state.last_provisioning_info = prov_info

        self._save_state()

        return {
            "status": self.state.status.value,
            "device": dev.model_dump(mode="json") if dev else None,
            "provisioning": prov_info.model_dump(mode="json") if prov_info else (
                self.state.last_provisioning_info.model_dump(mode="json")
                if self.state.last_provisioning_info else None
            ),
            "last_build": self.state.last_build_result.model_dump(mode="json") if self.state.last_build_result else None,
            "last_install": self.state.last_install_result.model_dump(mode="json") if self.state.last_install_result else None,
            "auto_renew_enabled": self.state.auto_renew_enabled,
            "target_device_name": self.state.target_device_name,
            "target_bundle_id": self.state.target_bundle_id,
            "last_checked_at": self.state.last_checked_at.isoformat() if self.state.last_checked_at else None,
        }

    async def check_device(self) -> DeviceInfo | None:
        """Probe for the target iPhone."""
        dev = await self.device_monitor.get_target_device(self.state.target_device_name)
        self.state.last_device_info = dev
        self._save_state()
        if not dev or not dev.reachable:
            await self.notifier.notify(NotificationEvent.DEVICE_UNAVAILABLE)
        return dev

    async def build(
        self,
        allow_provisioning_updates: bool = True,
        configuration: str = "Debug",
        clean_first: bool = False,
        code_signing_allowed: bool = True,
    ) -> BuildResult:
        """Acquire lock and build the iOS app using Xcode."""
        if self._lock.locked():
            return BuildResult(
                success=False,
                error="Another build or installation is already in progress.",
                duration_seconds=0.0
            )

        async with self._lock:
            self.state.status = BuildLifecycleState.BUILDING
            self._save_state()

            # Target connected device if available, otherwise generic iOS
            dev = await self.device_monitor.get_target_device(self.state.target_device_name)
            destination = f"id={dev.udid}" if dev and dev.udid else "generic/platform=iOS"

            build_res = await self.builder.build(
                device_destination=destination,
                allow_provisioning_updates=allow_provisioning_updates,
                configuration=configuration,
                clean_first=clean_first,
                code_signing_allowed=code_signing_allowed,
            )

            self.state.last_build_result = build_res

            if build_res.success:
                self.state.status = BuildLifecycleState.SUCCESS
                if build_res.artifact:
                    prof_path = os.path.join(build_res.artifact.app_path, "embedded.mobileprovision")
                    if os.path.exists(prof_path):
                        self.state.last_provisioning_info = await self.signing_inspector.parse_provisioning_profile(prof_path)
                await self.notifier.notify(NotificationEvent.BUILD_SUCCEEDED)
            else:
                if build_res.requires_user_action:
                    self.state.status = BuildLifecycleState.USER_ACTION_REQUIRED
                    await self.notifier.notify(NotificationEvent.USER_ACTION_REQUIRED)
                else:
                    self.state.status = BuildLifecycleState.FAILED
                    await self.notifier.notify(NotificationEvent.BUILD_FAILED, detail=build_res.error)

            self._save_state()
            return build_res

    async def install(self) -> InstallResult:
        """Acquire lock and install the latest built artifact onto the target device."""
        if self._lock.locked():
            return InstallResult(
                success=False,
                device_id="",
                bundle_id=self.state.target_bundle_id,
                error="Another operation is currently active."
            )

        async with self._lock:
            # Verify target device is reachable
            dev = await self.device_monitor.get_target_device(self.state.target_device_name)
            if not dev or not dev.reachable:
                self.state.status = BuildLifecycleState.DEVICE_UNAVAILABLE
                self._save_state()
                await self.notifier.notify(NotificationEvent.DEVICE_UNAVAILABLE)
                return InstallResult(
                    success=False,
                    device_id=dev.identifier if dev else "unknown",
                    bundle_id=self.state.target_bundle_id,
                    error=f"Device '{self.state.target_device_name}' is not reachable via wired or wireless Xcode connection."
                )

            # Check if artifact exists
            if not self.state.last_build_result or not self.state.last_build_result.artifact:
                return InstallResult(
                    success=False,
                    device_id=dev.identifier,
                    bundle_id=self.state.target_bundle_id,
                    error="No compiled build artifact available. Please run ios.build.build first."
                )

            app_path = self.state.last_build_result.artifact.app_path
            self.state.status = BuildLifecycleState.INSTALLING
            self._save_state()

            install_res = await self.installer.install(
                device_identifier=dev.identifier,
                app_bundle_path=app_path,
                bundle_id=self.state.target_bundle_id
            )

            self.state.last_install_result = install_res

            if install_res.success:
                self.state.status = BuildLifecycleState.SUCCESS
                await self.notifier.notify(NotificationEvent.INSTALL_SUCCEEDED)
            else:
                self.state.status = BuildLifecycleState.FAILED
                await self.notifier.notify(NotificationEvent.INSTALL_FAILED, detail=install_res.error)

            self._save_state()
            return install_res

    async def renew(self) -> dict[str, Any]:
        """Trigger provisioning profile renewal via clean build with provisioning updates."""
        logger.info("provisioning_renewal_triggered")
        old_exp_date: datetime | None = None
        if self.state.last_provisioning_info:
            old_exp_date = self.state.last_provisioning_info.expiration_date

        build_res = await self.build(allow_provisioning_updates=True, clean_first=True)
        if not build_res.success:
            return {
                "success": False,
                "error": f"Renewal build failed: {build_res.error}",
                "user_action_required": build_res.requires_user_action
            }

        # Check if expiration date actually advanced
        new_prov = self.state.last_provisioning_info
        renewed = False
        if new_prov and new_prov.expiration_date:
            if old_exp_date is None or new_prov.expiration_date > old_exp_date:
                renewed = True

        self.state.last_renewal_attempt = datetime.now(timezone.utc)
        self._save_state()

        return {
            "success": build_res.success,
            "renewed": renewed,
            "days_remaining": new_prov.days_remaining if new_prov else None,
            "expiration_date": new_prov.expiration_date.isoformat() if new_prov and new_prov.expiration_date else None,
            "user_action_required": build_res.requires_user_action,
        }

    async def evaluate_auto_trigger(self) -> bool:
        """
        Evaluate auto-deploy condition:
        Device reachable AND user opted in AND
        (provisioning expiration approaching OR approved new build available OR explicit manual build requested)
        """
        if not self.state.auto_renew_enabled:
            logger.info("auto_trigger_skipped_not_opted_in")
            return False

        dev = await self.device_monitor.get_target_device(self.state.target_device_name)
        if not dev or not dev.reachable:
            logger.info("auto_trigger_device_not_reachable")
            return False

        needs_action = False
        if self.state.last_provisioning_info and self.state.last_provisioning_info.needs_renewal:
            needs_action = True

        if not needs_action:
            return False

        logger.info("auto_trigger_executing_renewal")
        renew_res = await self.renew()
        if renew_res.get("success"):
            await self.install()
            return True
        return False

    def set_auto_renew(self, enabled: bool) -> bool:
        """Toggle automatic wireless build and renewal."""
        self.state.auto_renew_enabled = enabled
        self._save_state()
        logger.info("auto_renew_setting_updated", enabled=enabled)
        return self.state.auto_renew_enabled
