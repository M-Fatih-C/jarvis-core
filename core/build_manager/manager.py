"""Central coordinator for iOS Build, Provisioning Renewal, and Device Installation."""

import asyncio
from datetime import datetime, timezone
import fcntl
import json
import os
import tempfile
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


class CrossProcessLock:
    """
    File-based cross-process mutex using fcntl.flock.
    Prevents concurrent build and installation operations across separate CLI,
    daemon, and agent processes.
    """

    def __init__(self, lock_file: str | None = None) -> None:
        self.lock_file = lock_file or os.path.expanduser("~/.cache/jarvis/build/operation.lock")
        self._fd: Any = None

    def acquire(self, blocking: bool = False) -> bool:
        """Acquire non-blocking or blocking exclusive file lock."""
        try:
            os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)
            fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._fd = os.fdopen(fd, "a")
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._fd, flags)
            return True
        except (BlockingIOError, OSError):
            if self._fd is not None:
                try:
                    self._fd.close()
                except OSError:
                    pass
                self._fd = None
            return False

    def release(self) -> None:
        """Release lock and close descriptor."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "CrossProcessLock":
        if not self.acquire(blocking=False):
            raise BlockingIOError("Another process currently holds the iOS build/install lock.")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


class BuildManager:
    """Manages iOS application compilation, provisioning expiration tracking, and installation."""

    def __init__(
        self,
        project_dir: str = "ios/JarvisiOS",
        target_device_name: str = "Fatih",
        target_device_id: str = "A06A0EAC-8F32-5BD1-A945-ED1C002C60D8",
        target_device_udid: str = "00008110-00182C4A2EDB601E",
        target_bundle_id: str = "com.mfatihc.jarvis",
        state_file: str | None = None,
    ) -> None:
        self.target_device_name = target_device_name
        self.target_device_id = target_device_id
        self.target_device_udid = target_device_udid
        self.target_bundle_id = target_bundle_id
        self.state_file = os.path.abspath(
            state_file or os.path.expanduser("~/.cache/jarvis/build_manager_state.json")
        )
        self.device_monitor = DeviceMonitor(
            default_target_name=target_device_name,
            approved_identifier=target_device_id,
            approved_udid=target_device_udid,
        )
        self.builder = XcodeBuilder(project_dir=project_dir)
        self.installer = AppInstaller()
        self.signing_inspector = SigningInspector()
        self.notifier = BuildNotifier()

        self._lock = asyncio.Lock()
        self._cross_lock = CrossProcessLock()
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
            target_device_id=self.target_device_id,
            target_device_udid=self.target_device_udid,
            target_bundle_id=self.target_bundle_id,
        )

    def _save_state(self) -> None:
        """Persist state to disk."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(self.state_file), delete=False) as f:
                json.dump(self.state.model_dump(mode="json"), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(f.name, self.state_file)
        except Exception as exc:
            logger.error("failed_saving_build_manager_state", error=str(exc))

    async def get_status(self) -> dict[str, Any]:
        """Return the complete current status of the build manager, device, and provisioning."""
        dev = await self.device_monitor.get_target_device(
            target_name=self.state.target_device_name,
            target_identifier=self.state.target_device_id,
            target_udid=self.state.target_device_udid,
        )
        self.state.last_device_info = dev
        self.state.last_checked_at = datetime.now(timezone.utc)

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
            "last_successful_renewal": self.state.last_successful_renewal.isoformat() if self.state.last_successful_renewal else None,
            "renewal_retry_count": self.state.renewal_retry_count,
            "auto_renew_enabled": self.state.auto_renew_enabled,
            "target_device_name": self.state.target_device_name,
            "target_device_id": self.state.target_device_id,
            "target_bundle_id": self.state.target_bundle_id,
            "last_checked_at": self.state.last_checked_at.isoformat() if self.state.last_checked_at else None,
        }

    async def check_device(self) -> DeviceInfo | None:
        """Probe for the target iPhone matching approved identifier."""
        dev = await self.device_monitor.get_target_device(
            target_name=self.state.target_device_name,
            target_identifier=self.state.target_device_id,
            target_udid=self.state.target_device_udid,
        )
        self.state.last_device_info = dev
        self._save_state()
        # Device probes are status reads, not actionable failures. A sleeping or
        # disconnected iPhone must not generate a banner on every status check.
        return dev

    async def build(
        self,
        allow_provisioning_updates: bool = True,
        configuration: str = "Debug",
        clean_first: bool = False,
        code_signing_allowed: bool = True,
    ) -> BuildResult:
        """Acquire in-process and cross-process locks, then build the iOS app."""
        if self._lock.locked():
            return BuildResult(
                success=False,
                error="Another build or installation is already in progress (in-process lock held).",
                duration_seconds=0.0
            )

        try:
            if not self._cross_lock.acquire(blocking=False):
                raise BlockingIOError("Build lock held")
        except Exception:
            return BuildResult(
                success=False,
                error="Another build or installation is already in progress (cross-process lock held).",
                duration_seconds=0.0
            )

        try:
            async with self._lock:
                self.state.status = BuildLifecycleState.BUILDING
                self._save_state()

                dev = await self.device_monitor.get_target_device(
                    target_name=self.state.target_device_name,
                    target_identifier=self.state.target_device_id,
                    target_udid=self.state.target_device_udid,
                )
                destination = f"id={dev.udid}" if dev and dev.udid and dev.reachable else "generic/platform=iOS"

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
                            self.state.last_provisioning_info = (
                                await self.signing_inspector.parse_provisioning_profile(prof_path)
                            )
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
        finally:
            self._cross_lock.release()

    async def install(self) -> InstallResult:
        """
        Acquire locks, verify artifact integrity (bundle ID, signature, provisioning),
        install on approved device, and verify via devicectl.
        """
        if self._lock.locked():
            return InstallResult(
                success=False,
                device_id="",
                bundle_id=self.state.target_bundle_id,
                error="Another operation is currently active (in-process lock held)."
            )

        try:
            if not self._cross_lock.acquire(blocking=False):
                raise BlockingIOError("Build lock held")
        except Exception:
            return InstallResult(
                success=False,
                device_id="",
                bundle_id=self.state.target_bundle_id,
                error="Another operation is currently active (cross-process lock held)."
            )

        try:
            async with self._lock:
                dev = await self.device_monitor.get_target_device(
                    target_name=self.state.target_device_name,
                    target_identifier=self.state.target_device_id,
                    target_udid=self.state.target_device_udid,
                )
                if not dev or not dev.reachable:
                    self.state.status = BuildLifecycleState.DEVICE_UNAVAILABLE
                    self._save_state()
                    await self.notifier.notify(NotificationEvent.DEVICE_UNAVAILABLE)
                    return InstallResult(
                        success=False,
                        device_id=dev.identifier if dev else "unknown",
                        bundle_id=self.state.target_bundle_id,
                        error=f"Approved device '{self.state.target_device_id}' is not reachable via wired or wireless Xcode connection."
                    )

                if not self.state.last_build_result or not self.state.last_build_result.artifact:
                    return InstallResult(
                        success=False,
                        device_id=dev.identifier,
                        bundle_id=self.state.target_bundle_id,
                        error="No compiled build artifact available. Please run ios.build.build first."
                    )

                app_path = self.state.last_build_result.artifact.app_path

                # Pre-installation artifact integrity check
                valid_artifact, integrity_err = await self.signing_inspector.verify_artifact_integrity(
                    app_path, self.state.target_bundle_id, expected_device_udid=dev.udid
                )
                if not valid_artifact:
                    err_msg = f"Artifact pre-installation security verification failed: {integrity_err}"
                    logger.error("install_aborted_artifact_verification_failed", error=err_msg)
                    return InstallResult(
                        success=False,
                        device_id=dev.identifier,
                        bundle_id=self.state.target_bundle_id,
                        error=err_msg,
                        verified=False
                    )

                self.state.status = BuildLifecycleState.INSTALLING
                self._save_state()

                install_res = await self.installer.install(
                    device_identifier=dev.identifier,
                    app_bundle_path=app_path,
                    bundle_id=self.state.target_bundle_id
                )

                self.state.last_install_result = install_res

                if install_res.success and install_res.verified:
                    self.state.status = BuildLifecycleState.SUCCESS
                    await self.notifier.notify(NotificationEvent.INSTALL_SUCCEEDED)
                elif install_res.device_locked or install_res.requires_user_action:
                    self.state.status = BuildLifecycleState.USER_ACTION_REQUIRED
                    await self.notifier.notify(NotificationEvent.USER_ACTION_REQUIRED)
                else:
                    self.state.status = BuildLifecycleState.FAILED
                    await self.notifier.notify(NotificationEvent.INSTALL_FAILED, detail=install_res.error)

                self._save_state()
                return install_res
        finally:
            self._cross_lock.release()

    async def renew(self, install_after: bool = False) -> dict[str, Any]:
        """
        Execute provisioning profile renewal:
        1. Check bounded retries and cooldown.
        2. Clean build with allow_provisioning_updates.
        3. Verify expiration date advanced later than previous profile.
        4. Optionally install onto target device and verify.
        """
        logger.info("provisioning_renewal_triggered")
        now = datetime.now(timezone.utc)

        # Check cooldown & bounded retries
        if self.state.last_renewal_attempt and self.state.renewal_retry_count >= self.state.renewal_max_retries:
            elapsed = (now - self.state.last_renewal_attempt).total_seconds()
            if elapsed < self.state.renewal_cooldown_seconds:
                cooldown_left = int(self.state.renewal_cooldown_seconds - elapsed)
                logger.warning("renewal_skipped_in_cooldown", cooldown_remaining_seconds=cooldown_left)
                return {
                    "success": False,
                    "renewed": False,
                    "cooldown": True,
                    "error": f"Renewal is in cooldown (max {self.state.renewal_max_retries} retries reached). Cooldown expires in {cooldown_left}s.",
                }

        old_exp_date: datetime | None = None
        if self.state.last_provisioning_info:
            old_exp_date = self.state.last_provisioning_info.expiration_date

        build_res = await self.build(allow_provisioning_updates=True, clean_first=True)
        if not build_res.success:
            self.state.renewal_retry_count += 1
            self.state.last_renewal_attempt = now
            if build_res.requires_user_action:
                self.state.status = BuildLifecycleState.USER_ACTION_REQUIRED
            self._save_state()
            return {
                "success": False,
                "renewed": False,
                "error": f"Renewal build failed: {build_res.error}",
                "user_action_required": build_res.requires_user_action
            }

        # Check if expiration date actually advanced
        new_prov = self.state.last_provisioning_info
        renewed = False
        if new_prov and new_prov.expiration_date:
            if old_exp_date is None or new_prov.expiration_date > old_exp_date:
                renewed = True

        if not renewed:
            self.state.renewal_retry_count += 1
            self.state.last_renewal_attempt = now
            self._save_state()
            return {
                "success": False,
                "renewed": False,
                "error": "Xcode returned the same provisioning profile; expiration date did not advance.",
                "days_remaining": new_prov.days_remaining if new_prov else None,
                "user_action_required": False,
            }

        if install_after:
            install_res = await self.install()
            if not install_res.success or not install_res.verified:
                self.state.renewal_retry_count += 1
                self.state.last_renewal_attempt = now
                self._save_state()
                return {
                    "success": False,
                    "renewed": False,
                    "error": f"Renewal installation failed: {install_res.error}",
                    "user_action_required": install_res.requires_user_action or install_res.device_locked,
                }
            self.state.last_successful_renewal = now
            self.state.renewal_retry_count = 0

        self.state.last_renewal_attempt = now
        self._save_state()

        return {
            "success": True,
            "renewed": install_after,
            "profile_generated": True,
            "installation_required": not install_after,
            "days_remaining": new_prov.days_remaining if new_prov else None,
            "expiration_date": new_prov.expiration_date.isoformat() if new_prov and new_prov.expiration_date else None,
            "user_action_required": False,
        }

    async def evaluate_auto_trigger(self) -> bool:
        """
        Evaluate auto-deploy condition:
        Device reachable AND user opted in AND NOT in cooldown AND
        provisioning expiration approaching.
        Requires successful signed renewal build AND successful installation on device.
        Only then records renewal as completed.
        """
        if not self.state.auto_renew_enabled:
            logger.info("auto_trigger_skipped_not_opted_in")
            return False

        if not self.state.last_provisioning_info or not self.state.last_provisioning_info.needs_renewal:
            return False

        # Cooldown guard against repeated 15-minute rebuild loops
        if self.state.last_renewal_attempt and self.state.renewal_retry_count >= self.state.renewal_max_retries:
            elapsed = (datetime.now(timezone.utc) - self.state.last_renewal_attempt).total_seconds()
            if elapsed < self.state.renewal_cooldown_seconds:
                logger.info("auto_trigger_skipped_cooldown_active", retries=self.state.renewal_retry_count)
                return False

        dev = await self.device_monitor.get_target_device(
            target_name=self.state.target_device_name,
            target_identifier=self.state.target_device_id,
            target_udid=self.state.target_device_udid,
        )
        if not dev or not dev.reachable:
            logger.info("auto_trigger_device_not_reachable")
            self.state.status = BuildLifecycleState.USER_ACTION_REQUIRED
            self._save_state()
            await self.notifier.notify(NotificationEvent.USER_ACTION_REQUIRED)
            return False

        needs_action = False
        if self.state.last_provisioning_info and self.state.last_provisioning_info.needs_renewal:
            needs_action = True

        if not needs_action:
            return False

        logger.info("auto_trigger_executing_renewal")
        renew_res = await self.renew(install_after=True)
        return bool(renew_res.get("success") and renew_res.get("renewed"))

    def set_auto_renew(self, enabled: bool) -> bool:
        """Toggle automatic wireless build and renewal."""
        self.state.auto_renew_enabled = enabled
        self._save_state()
        logger.info("auto_renew_setting_updated", enabled=enabled)
        return self.state.auto_renew_enabled
