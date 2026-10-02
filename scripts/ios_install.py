#!/usr/bin/env python3
"""CLI utility to deploy the compiled iOS application to the target device."""

import asyncio
import sys
from core.build_manager.manager import BuildManager


async def main() -> None:
    mgr = BuildManager()
    print("Initiating iOS app deployment...")
    result = await mgr.install()

    if result.success:
        print(f"INSTALL SUCCEEDED on device {result.device_id} in {result.duration_seconds}s!")
        sys.exit(0)
    else:
        print(f"INSTALL FAILED on device {result.device_id}: {result.error}")
        if result.device_locked:
            print("\n[ACTION REQUIRED] Please unlock your iPhone screen and retry.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
