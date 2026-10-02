"""Unit and integration tests for iOS Build Manager and native build tools."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import plistlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.build_manager.builder import XcodeBuilder
from core.build_manager.device_monitor import DeviceMonitor
from core.build_manager.installer import AppInstaller
from core.build_manager.manager import BuildManager
from core.build_manager.notifier import BuildNotifier
from core.build_manager.signing import SigningInspector
from core.build_manager.state import (
    BuildArtifact,
    BuildLifecycleState,
    BuildResult,
    DeviceInfo,
    InstallResult,
    NotificationEvent,
    ProvisioningInfo,
)
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType, PolicyRequest
from core.tools.ios_build import (
    IOSBuildBuildTool,
    IOSBuildCheckDeviceTool,
    IOSBuildInstallTool,
    IOSBuildRenewTool,
    IOSBuildStatusTool,
)


@pytest.fixture
def mock_devicectl_json(tmp_path):
    """Create sample devicectl list output JSON."""
    data = {
        "result": {
            "devices": [
                {
                    "identifier": "A06A0EAC-8F32-5BD1-A945-ED1C002C60D8",
                    "deviceProperties": {
                        "name": "Fatih",
                        "developerModeStatus": "enabled",
                        "osVersionNumber": "26.6.2"
                    },
                    "connectionProperties": {
                        "pairingState": "paired",
                        "transportType": "wired",
                        "potentialHostnames": ["Fatih.coredevice.local"]
                    },
                    "hardwareProperties": {
                        "marketingName": "iPhone 13",
                        "productType": "iPhone14,5",
                        "udid": "00008110-00182C4A2EDB601E"
                    }
                }
            ]
        }
    }
    file_path = tmp_path / "devicectl.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(file_path)


@pytest.mark.asyncio
async def test_device_monitor_discovery(tmp_path):
    """Verify device monitor extracts structured DeviceInfo from devicectl JSON."""
    monitor = DeviceMonitor(default_target_name="Fatih")
    sample_json = {
        "result": {
            "devices": [
                {
                    "identifier": "TEST-ID-123",
                    "deviceProperties": {
                        "name": "Fatih",
                        "developerModeStatus": "enabled",
                        "osVersionNumber": "17.4"
                    },
                    "connectionProperties": {
                        "pairingState": "paired",
                        "transportType": "wifi",
                        "potentialHostnames": ["Fatih.local"]
                    },
                    "hardwareProperties": {
                        "marketingName": "iPhone 13",
                        "productType": "iPhone14,5",
                        "udid": "00008110-TEST"
                    }
                }
            ]
        }
    }

    with patch("tempfile.NamedTemporaryFile") as mock_tmp, \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        tmp_file = tmp_path / "devices.json"
        with open(tmp_file, "w") as f:
            json.dump(sample_json, f)

        mock_tmp.return_value.__enter__.return_value.name = str(tmp_file)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_exec.return_value = mock_proc

        devices = await monitor.list_devices()
        assert len(devices) == 1
        dev = devices[0]
        assert dev.name == "Fatih"
        assert dev.transport_type == "wifi"
        assert dev.reachable is True
        assert dev.developer_mode is True


@pytest.mark.asyncio
async def test_signing_inspector_expiration_calculation():
    """Verify parsing of embedded.mobileprovision and 2-day renewal threshold calculation."""
    now = datetime.now(timezone.utc)
    future_exp = now + timedelta(days=1, hours=12) # ~1.5 days remaining (needs renewal)

    plist_content = {
        "ExpirationDate": future_exp,
        "CreationDate": now - timedelta(days=5),
        "TeamIdentifier": ["T92VJM9C5B"],
        "TeamName": "Muhammed Fatih Cetintas",
        "Entitlements": {
            "application-identifier": "T92VJM9C5B.com.mfatihc.jarvis"
        }
    }
    plist_bytes = plistlib.dumps(plist_content)

    with patch("os.path.exists", return_value=True), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (plist_bytes, b"")
        mock_exec.return_value = mock_proc

        info = await SigningInspector.parse_provisioning_profile("/path/to/embedded.mobileprovision")
        assert info.team_identifier == "T92VJM9C5B"
        assert info.days_remaining is not None
        assert info.days_remaining <= 2.0
        assert info.needs_renewal is True
        assert info.is_expired is False


def test_signing_inspector_user_action_detection():
    """Verify detection of Apple ID authentication requirements."""
    error_1 = "No Accounts: Add a new account in Accounts settings."
    assert SigningInspector.detect_user_action_required(error_1) is True

    error_2 = "Your session has expired. Please log in again."
    assert SigningInspector.detect_user_action_required(error_2) is True

    normal_error = "Syntax error in file Foo.swift at line 42"
    assert SigningInspector.detect_user_action_required(normal_error) is False


@pytest.mark.asyncio
async def test_build_manager_prevent_concurrent_builds(tmp_path):
    """Verify that concurrent builds are rejected with lock error."""
    state_file = str(tmp_path / "state.json")
    mgr = BuildManager(state_file=state_file)

    # Acquire lock manually
    await mgr._lock.acquire()

    res = await mgr.build()
    assert res.success is False
    assert "in progress" in res.error.lower()

    mgr._lock.release()


@pytest.mark.asyncio
async def test_build_manager_offline_device_handling(tmp_path):
    """Verify safe queuing / reporting when device is unreachable during install."""
    state_file = str(tmp_path / "state.json")
    mgr = BuildManager(state_file=state_file)

    # Mock device monitor returning unreachable device
    mgr.device_monitor.get_target_device = AsyncMock(return_value=DeviceInfo(
        identifier="TEST-ID",
        name="Fatih",
        reachable=False
    ))

    res = await mgr.install()
    assert res.success is False
    assert "not reachable" in res.error.lower()
    assert mgr.state.status == BuildLifecycleState.DEVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_build_manager_auto_trigger_conditions(tmp_path):
    """Verify auto trigger only executes when opted-in, device reachable, and renewal required."""
    state_file = str(tmp_path / "state.json")
    mgr = BuildManager(state_file=state_file)

    # 1. User not opted in -> False
    mgr.state.auto_renew_enabled = False
    assert await mgr.evaluate_auto_trigger() is False

    # 2. Opted in, but device unreachable -> False
    mgr.state.auto_renew_enabled = True
    mgr.device_monitor.get_target_device = AsyncMock(return_value=DeviceInfo(
        identifier="TEST-ID", name="Fatih", reachable=False
    ))
    assert await mgr.evaluate_auto_trigger() is False

    # 3. Opted in, device reachable, but profile healthy (>2 days) -> False
    mgr.device_monitor.get_target_device = AsyncMock(return_value=DeviceInfo(
        identifier="TEST-ID", name="Fatih", reachable=True
    ))
    mgr.state.last_provisioning_info = ProvisioningInfo(
        days_remaining=5.0,
        needs_renewal=False
    )
    assert await mgr.evaluate_auto_trigger() is False

    # 4. Opted in, reachable, and needs renewal -> Triggers renewal
    mgr.state.last_provisioning_info = ProvisioningInfo(
        days_remaining=1.2,
        needs_renewal=True
    )
    mgr.renew = AsyncMock(return_value={"success": True, "renewed": True})
    mgr.install = AsyncMock(return_value=InstallResult(success=True, device_id="TEST-ID", bundle_id="com.mfatihc.jarvis"))

    triggered = await mgr.evaluate_auto_trigger()
    assert triggered is True
    mgr.renew.assert_awaited_once()
    mgr.install.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_engine_enforcement_on_build_tools():
    """Verify PolicyEngine enforces R0 allow, R2 approval for build/install/renew, and OBSERVE denial."""
    policy_engine = PolicyEngine()

    status_tool = IOSBuildStatusTool()
    build_tool = IOSBuildBuildTool()
    install_tool = IOSBuildInstallTool()
    renew_tool = IOSBuildRenewTool()

    # R0 status tool in ASSIST mode -> ALLOW
    req_status = PolicyRequest(
        agent_mode=AgentMode.ASSIST,
        tool_definition=status_tool.definition,
        tool_call=ToolCall(id="call_status", name="ios.build.status")
    )
    dec_status = policy_engine.evaluate(req_status)
    assert dec_status.decision == PolicyDecisionType.ALLOW

    # R2 build tool in ASSIST mode -> REQUIRE_APPROVAL
    req_build = PolicyRequest(
        agent_mode=AgentMode.ASSIST,
        tool_definition=build_tool.definition,
        tool_call=ToolCall(id="call_build", name="ios.build.build", arguments={"clean": False})
    )
    dec_build = policy_engine.evaluate(req_build)
    assert dec_build.decision == PolicyDecisionType.REQUIRE_APPROVAL

    # R2 install tool in ASSIST mode -> REQUIRE_APPROVAL
    req_install = PolicyRequest(
        agent_mode=AgentMode.ASSIST,
        tool_definition=install_tool.definition,
        tool_call=ToolCall(id="call_install", name="ios.build.install")
    )
    dec_install = policy_engine.evaluate(req_install)
    assert dec_install.decision == PolicyDecisionType.REQUIRE_APPROVAL

    # In OBSERVE mode, build tool is strictly DENIED
    req_observe = PolicyRequest(
        agent_mode=AgentMode.OBSERVE,
        tool_definition=build_tool.definition,
        tool_call=ToolCall(id="call_observe", name="ios.build.build")
    )
    dec_observe = policy_engine.evaluate(req_observe)
    assert dec_observe.decision == PolicyDecisionType.DENY


@pytest.mark.asyncio
async def test_ios_build_tools_execution(tmp_path):
    """Verify tool wrappers correctly route execution to BuildManager."""
    state_file = str(tmp_path / "state.json")
    mgr = BuildManager(state_file=state_file)

    # Mock manager methods
    mgr.get_status = AsyncMock(return_value={"status": "IDLE", "device": {"name": "Fatih"}})
    mgr.check_device = AsyncMock(return_value=DeviceInfo(identifier="T1", name="Fatih", reachable=True))
    mgr.build = AsyncMock(return_value=BuildResult(
        success=True,
        artifact=BuildArtifact(app_path="/tmp/App.app", bundle_id="com.mfatihc.jarvis", codesign_valid=True),
        duration_seconds=5.2
    ))
    mgr.install = AsyncMock(return_value=InstallResult(
        success=True,
        device_id="T1",
        bundle_id="com.mfatihc.jarvis",
        duration_seconds=2.1
    ))
    mgr.renew = AsyncMock(return_value={"success": True, "renewed": True, "days_remaining": 6.8})

    # Execute tools
    status_tool = IOSBuildStatusTool(manager=mgr)
    res_status = await status_tool.execute({})
    assert res_status.success is True
    assert res_status.data["status"] == "IDLE"

    check_tool = IOSBuildCheckDeviceTool(manager=mgr)
    res_check = await check_tool.execute({})
    assert res_check.success is True
    assert res_check.data["name"] == "Fatih"

    build_tool = IOSBuildBuildTool(manager=mgr)
    res_build = await build_tool.execute({"clean": True})
    assert res_build.success is True
    assert res_build.data["codesign_valid"] is True

    install_tool = IOSBuildInstallTool(manager=mgr)
    res_install = await install_tool.execute({})
    assert res_install.success is True
    assert res_install.data["device_id"] == "T1"

    renew_tool = IOSBuildRenewTool(manager=mgr)
    res_renew = await renew_tool.execute({})
    assert res_renew.success is True
    assert res_renew.data["renewed"] is True
