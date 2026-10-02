#!/usr/bin/env python3
"""CLI utility to trigger an iOS application build."""

import argparse
import asyncio
import sys
from core.build_manager.manager import BuildManager


async def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger Jarvis iOS application build.")
    parser.add_argument("--clean", action="store_true", help="Perform clean build")
    parser.add_argument("--no-updates", action="store_true", help="Disallow automatic provisioning updates")
    args = parser.parse_args()

    mgr = BuildManager()
    print("Initiating iOS build for scheme JarvisiOS...")
    result = await mgr.build(
        allow_provisioning_updates=not args.no_updates,
        clean_first=args.clean
    )

    if result.success:
        print(f"BUILD SUCCEEDED in {result.duration_seconds}s!")
        if result.artifact:
            print(f"Artifact: {result.artifact.app_path}")
            print(f"Codesign valid: {result.artifact.codesign_valid}")
        sys.exit(0)
    else:
        print(f"BUILD FAILED in {result.duration_seconds}s.")
        print(f"Error: {result.error}")
        if result.requires_user_action:
            print("\n[ACTION REQUIRED] Please open Xcode -> Settings -> Accounts and sign in with your Apple ID.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
