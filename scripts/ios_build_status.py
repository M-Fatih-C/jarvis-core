#!/usr/bin/env python3
"""CLI utility to query iOS build status, device connectivity, and provisioning expiration."""

import asyncio
import json
import sys
from core.build_manager.manager import BuildManager


async def main() -> None:
    mgr = BuildManager()
    status = await mgr.get_status()

    if "--json" in sys.argv:
        print(json.dumps(status, indent=2))
        return

    print("=== JARVIS iOS Build Manager Status ===")
    print(f"Overall Status: {status['status']}")
    print(f"Target Device:  {status['target_device_name']}")
    print(f"Bundle ID:      {status['target_bundle_id']}")
    print(f"Auto Renew:     {'Enabled' if status['auto_renew_enabled'] else 'Disabled'}")

    device = status.get("device")
    if device:
        print("\n--- Device Connection ---")
        print(f"  Name:        {device.get('name')}")
        print(f"  Identifier:  {device.get('identifier')}")
        print(f"  Model:       {device.get('model')}")
        print(f"  Transport:   {device.get('transport_type')}")
        print(f"  Paired:      {device.get('pairing_state')}")
        print(f"  Dev Mode:    {'Enabled' if device.get('developer_mode') else 'Disabled'}")
        print(f"  Reachable:   {'YES' if device.get('reachable') else 'NO'}")
    else:
        print("\n--- Device Connection ---")
        print("  Device unreachable or not found.")

    prov = status.get("provisioning")
    if prov:
        print("\n--- Provisioning Profile ---")
        print(f"  App ID:      {prov.get('app_identifier')}")
        print(f"  Team:        {prov.get('team_identifier')} ({prov.get('team_name')})")
        print(f"  Expires:     {prov.get('expiration_date')}")
        days = prov.get('days_remaining')
        print(f"  Days Left:   {days} days" if days is not None else "  Days Left:   Unknown")
        print(f"  Needs Renew: {'YES' if prov.get('needs_renewal') else 'NO'}")
        if prov.get("error"):
            print(f"  Error:       {prov.get('error')}")

    last_build = status.get("last_build")
    if last_build:
        print("\n--- Last Build ---")
        print(f"  Success:     {'YES' if last_build.get('success') else 'NO'}")
        print(f"  Duration:    {last_build.get('duration_seconds')}s")
        if last_build.get("error"):
            print(f"  Error:       {last_build.get('error')}")

    last_install = status.get("last_install")
    if last_install:
        print("\n--- Last Install ---")
        print(f"  Success:     {'YES' if last_install.get('success') else 'NO'}")
        print(f"  Duration:    {last_install.get('duration_seconds')}s")
        if last_install.get("error"):
            print(f"  Error:       {last_install.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
