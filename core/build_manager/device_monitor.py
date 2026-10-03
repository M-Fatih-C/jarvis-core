"""Device discovery and connectivity monitor using official Xcode xcrun devicectl."""

import asyncio
import json
import os
import tempfile
from typing import Any
from core.build_manager.state import DeviceInfo
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.device_monitor")


class DeviceMonitor:
    """Discovers and monitors connected iOS devices via official Xcode devicectl."""

    def __init__(
        self,
        default_target_name: str = "Fatih",
        approved_identifier: str = "A06A0EAC-8F32-5BD1-A945-ED1C002C60D8",
        approved_udid: str = "00008110-00182C4A2EDB601E",
    ) -> None:
        self.default_target_name = default_target_name
        self.approved_identifier = approved_identifier
        self.approved_udid = approved_udid

    async def list_devices(self) -> list[DeviceInfo]:
        """Query xcrun devicectl list devices with JSON output."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
            tmp_json_path = tmp_file.name

        try:
            cmd = ["xcrun", "devicectl", "list", "devices", "--json-output", tmp_json_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if not os.path.exists(tmp_json_path) or os.path.getsize(tmp_json_path) == 0:
                logger.warning("devicectl_empty_output", stderr=stderr.decode("utf-8", errors="replace"))
                return []

            with open(tmp_json_path, "r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)

            devices: list[DeviceInfo] = []
            raw_devices = data.get("result", {}).get("devices", [])
            for raw in raw_devices:
                device_props = raw.get("deviceProperties", {})
                conn_props = raw.get("connectionProperties", {})
                hw_props = raw.get("hardwareProperties", {})

                name = device_props.get("name", "")
                dev_id = raw.get("identifier", "")
                udid = hw_props.get("udid", "")
                model = hw_props.get("marketingName", "")
                product_type = hw_props.get("productType", "")
                os_ver = device_props.get("osVersionNumber", "")
                pairing_state = conn_props.get("pairingState", "unknown")
                dev_mode = device_props.get("developerModeStatus") == "enabled"
                transport = conn_props.get("transportType", "unknown")
                potential_hosts = conn_props.get("potentialHostnames", [])
                hostname = potential_hosts[0] if potential_hosts else ""

                # Reachable if paired, developer mode is enabled, and transport is wired or wireless
                reachable = (
                    pairing_state == "paired"
                    and dev_mode
                    and transport in ("wired", "wifi", "local", "network")
                )

                info = DeviceInfo(
                    identifier=dev_id,
                    name=name,
                    udid=udid,
                    model=model,
                    product_type=product_type,
                    os_version=os_ver,
                    pairing_state=pairing_state,
                    developer_mode=dev_mode,
                    transport_type=transport,
                    hostname=hostname,
                    reachable=reachable,
                )
                devices.append(info)

            logger.info("devicectl_scan_complete", device_count=len(devices))
            return devices

        except Exception as exc:
            logger.error("devicectl_scan_error", error=str(exc))
            return []
        finally:
            if os.path.exists(tmp_json_path):
                try:
                    os.remove(tmp_json_path)
                except OSError:
                    pass

    async def get_target_device(
        self,
        target_name: str | None = None,
        target_identifier: str | None = None,
        target_udid: str | None = None,
    ) -> DeviceInfo | None:
        """
        Find designated target device.
        Requires exact match against approved device identifier/UDID to prevent
        selection based solely on potentially duplicated display names.
        """
        req_id = target_identifier or self.approved_identifier
        req_udid = target_udid or self.approved_udid
        target = target_name or self.default_target_name

        devices = await self.list_devices()

        # 1. First priority: match exact CoreDevice identifier or hardware UDID
        for dev in devices:
            if req_id and dev.identifier.upper() == req_id.upper():
                return dev
            if req_udid and dev.udid.upper() == req_udid.upper():
                return dev

        # 2. Second priority: match target parameter if it's an ID
        if target:
            for dev in devices:
                if dev.identifier.lower() == target.lower() or dev.udid.lower() == target.lower():
                    if req_id and dev.identifier.upper() != req_id.upper():
                        logger.warning(
                            "device_identifier_mismatch_rejected",
                            found_id=dev.identifier,
                            expected_id=req_id,
                        )
                        continue
                    return dev

        # 3. Third priority: match by display name, but STRICTLY verify against approved identifier
        if target:
            for dev in devices:
                if dev.name.lower() == target.lower():
                    if req_id and dev.identifier.upper() != req_id.upper():
                        logger.warning(
                            "device_name_matched_but_identifier_unapproved",
                            device_name=dev.name,
                            device_id=dev.identifier,
                            approved_id=req_id,
                        )
                        continue
                    return dev

        return None
