#!/usr/bin/env python3
"""Daemon job executed periodically by launchd (every ~15 minutes) to evaluate auto-renewal."""

import asyncio
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.build_manager.manager import BuildManager
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.daemon")


async def main() -> None:
    os.makedirs(os.path.expanduser("~/.cache/jarvis/logs"), exist_ok=True)
    logger.info("ios_build_manager_daemon_tick_started")

    # Singleton check via file lock to avoid duplicate daemon instances
    from core.build_manager.manager import CrossProcessLock
    daemon_lock = CrossProcessLock(os.path.expanduser("~/.cache/jarvis/build/daemon.lock"))
    if not daemon_lock.acquire(blocking=False):
        logger.warning("ios_build_manager_daemon_already_running_skipping")
        return

    try:
        mgr = BuildManager()

        # Query current device and status
        status = await mgr.get_status()
        device = status.get("device")
        prov = status.get("provisioning")

        logger.info(
            "ios_daemon_tick_status",
            auto_renew_enabled=status.get("auto_renew_enabled"),
            device_reachable=device.get("reachable") if device else False,
            days_remaining=prov.get("days_remaining") if prov else None,
            needs_renewal=prov.get("needs_renewal") if prov else False,
        )

        # Evaluate automated trigger conditions
        triggered = await mgr.evaluate_auto_trigger()
        logger.info("ios_build_manager_daemon_tick_completed", triggered=triggered)
    finally:
        daemon_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
