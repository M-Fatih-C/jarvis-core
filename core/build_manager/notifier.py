"""Native macOS notification dispatcher for iOS build and deployment events."""

import asyncio
from typing import Any
from core.build_manager.state import NotificationEvent
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.notifier")


class BuildNotifier:
    """Dispatches native macOS banner notifications for build lifecycle events."""

    NOTIFICATION_MESSAGES: dict[NotificationEvent, tuple[str, str]] = {
        NotificationEvent.BUILD_SUCCEEDED: (
            "Build Succeeded",
            "Jarvis iOS application compiled and signed successfully."
        ),
        NotificationEvent.INSTALL_SUCCEEDED: (
            "Install Succeeded",
            "Jarvis iOS application deployed to your iPhone."
        ),
        NotificationEvent.DEVICE_UNAVAILABLE: (
            "iPhone Unavailable",
            "Designated iPhone is unreachable. Installation queued until device reconnects."
        ),
        NotificationEvent.SIGNING_EXPIRED: (
            "Signing Profile Expired",
            "Personal Team certificate has expired. A fresh build renewal is required."
        ),
        NotificationEvent.USER_ACTION_REQUIRED: (
            "Apple Account Action Required",
            "Please open Xcode -> Settings -> Accounts and sign in to renew your provisioning profile."
        ),
        NotificationEvent.BUILD_FAILED: (
            "Build Failed",
            "An error occurred while compiling the iOS app."
        ),
        NotificationEvent.INSTALL_FAILED: (
            "Install Failed",
            "Failed to install app on iPhone. Check if device is passcode locked."
        ),
    }

    @classmethod
    async def notify(cls, event: NotificationEvent, detail: str | None = None) -> bool:
        """Display an immediate native notification on macOS via osascript."""
        title_prefix = "JARVIS Build Manager"
        default_sub, default_body = cls.NOTIFICATION_MESSAGES.get(
            event,
            ("Status Update", "Build manager event triggered.")
        )
        body = detail if detail else default_body

        # Escape double quotes for AppleScript
        safe_title = title_prefix.replace('"', '\\"')
        safe_sub = default_sub.replace('"', '\\"')
        safe_body = body.replace('"', '\\"')

        applescript = (
            f'display notification "{safe_body}" '
            f'with title "{safe_title}" '
            f'subtitle "{safe_sub}" '
            f'sound name "Default"'
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", applescript,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, _ = await proc.communicate()
            logger.info("build_notification_sent", notification_event=event.value, subtitle=default_sub)
            return proc.returncode == 0
        except Exception as exc:
            logger.warning("build_notification_failed", error=str(exc))
            return False
