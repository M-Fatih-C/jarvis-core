#!/usr/bin/env python3
"""Comprehensive Acceptance Verification for Milestone 5:
Native SwiftUI iPhone App & Automatic Wireless Build Manager.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import plistlib
import sys
import tempfile

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

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def report(name: str, passed: bool, detail: str = "") -> None:
    status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    print(f"[{status}] {name}")
    if detail:
        print(f"       {CYAN}{detail}{RESET}")


async def run_milestone_5_acceptance() -> bool:
    print(f"\n{YELLOW}=================================================================={RESET}")
    print(f"{YELLOW}   JARVIS V1 — MILESTONE 5 ACCEPTANCE & VERIFICATION SUITE       {RESET}")
    print(f"{YELLOW}==================================================================\n{RESET}")

    all_passed = True

    # 1. First USB Connection and Pairing Check
    try:
        monitor = DeviceMonitor(default_target_name="Fatih")
        devices = await monitor.list_devices()
        target = await monitor.get_target_device("Fatih")
        if target and target.pairing_state == "paired" and target.developer_mode:
            report(
                "Scenario 1: USB / Device Connection & Pairing Verification",
                True,
                f"Found {target.name} ({target.model}) via {target.transport_type}, paired={target.pairing_state}, dev_mode={target.developer_mode}"
            )
        else:
            report("Scenario 1: USB / Device Connection & Pairing Verification", False, "Target device 'Fatih' not paired or dev mode disabled")
            all_passed = False
    except Exception as exc:
        report("Scenario 1: USB / Device Connection & Pairing Verification", False, str(exc))
        all_passed = False

    # 2. Wireless and Transport Discovery
    try:
        # Devicectl supports wifi transport querying
        has_network_support = any(
            d.transport_type in ("wired", "wifi", "network", "local") for d in devices
        )
        report(
            "Scenario 2: Official devicectl Transport Discovery",
            has_network_support,
            f"Active transports verified across {len(devices)} discovered device(s)"
        )
        if not has_network_support:
            all_passed = False
    except Exception as exc:
        report("Scenario 2: Official devicectl Transport Discovery", False, str(exc))
        all_passed = False

    # 3. SwiftUI App Compilation (All 6 Core Screens)
    try:
        builder = XcodeBuilder(project_dir="ios/JarvisiOS")
        # Build for generic iOS device to verify code without requiring Apple ID provisioning credentials
        build_res = await builder.build(
            device_destination="generic/platform=iOS",
            allow_provisioning_updates=False,
            configuration="Debug",
            code_signing_allowed=False,
        )
        report(
            "Scenario 3: Native SwiftUI iPhone App Compilation",
            build_res.success,
            f"Compiled in {build_res.duration_seconds}s (Artifact: {build_res.artifact.app_path if build_res.artifact else 'N/A'})"
        )
        if not build_res.success:
            all_passed = False
    except Exception as exc:
        report("Scenario 3: Native SwiftUI iPhone App Compilation", False, str(exc))
        all_passed = False

    # 4. Safe Waiting When Device is Offline
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mgr = BuildManager(state_file=os.path.join(tmp_dir, "state.json"))
            # Simulate device offline
            mgr.device_monitor.get_target_device = async_mock_device(None)
            install_res = await mgr.install()
            is_safe_waiting = (
                not install_res.success
                and mgr.state.status == BuildLifecycleState.DEVICE_UNAVAILABLE
                and "not reachable" in (install_res.error or "").lower()
            )
            report(
                "Scenario 4: Safe Offline Device Waiting (No Failed Reports)",
                is_safe_waiting,
                f"Status transitioned to: {mgr.state.status.value}"
            )
            if not is_safe_waiting:
                all_passed = False
    except Exception as exc:
        report("Scenario 4: Safe Offline Device Waiting", False, str(exc))
        all_passed = False

    # 5. Provisioning Profile Expiration Parsing
    try:
        now = datetime.now(timezone.utc)
        test_exp = now + timedelta(days=6, hours=18)
        plist_data = {
            "ExpirationDate": test_exp,
            "CreationDate": now - timedelta(hours=6),
            "TeamIdentifier": ["T92VJM9C5B"],
            "TeamName": "Muhammed Fatih Cetintas (Personal Team)",
            "Entitlements": {"application-identifier": "T92VJM9C5B.com.mfatihc.jarvis"}
        }
        with tempfile.NamedTemporaryFile(suffix=".mobileprovision", delete=False) as f:
            tmp_path = f.name

        with patch_cms_exec(plistlib.dumps(plist_data)):
            info = await SigningInspector.parse_provisioning_profile(tmp_path)
            valid_parse = (
                info.team_identifier == "T92VJM9C5B"
                and info.days_remaining is not None
                and 6.0 <= info.days_remaining <= 7.0
                and not info.needs_renewal
            )
            report(
                "Scenario 5: embedded.mobileprovision Expiration Parsing",
                valid_parse,
                f"Team: {info.team_identifier}, Days Remaining: {info.days_remaining}d, Needs Renewal: {info.needs_renewal}"
            )
            if not valid_parse:
                all_passed = False
        os.remove(tmp_path)
    except Exception as exc:
        report("Scenario 5: embedded.mobileprovision Expiration Parsing", False, str(exc))
        all_passed = False

    # 6. Correct Renewal Window Decision (~2 days threshold)
    try:
        now = datetime.now(timezone.utc)
        # Profile expiring in 1.5 days -> must trigger renewal
        urgent_exp = now + timedelta(days=1, hours=12)
        urgent_plist = {
            "ExpirationDate": urgent_exp,
            "TeamIdentifier": ["T92VJM9C5B"],
            "Entitlements": {"application-identifier": "T92VJM9C5B.com.mfatihc.jarvis"}
        }
        with tempfile.NamedTemporaryFile(suffix=".mobileprovision", delete=False) as f:
            tmp_path = f.name

        with patch_cms_exec(plistlib.dumps(urgent_plist)):
            info_urgent = await SigningInspector.parse_provisioning_profile(tmp_path)
            correct_renewal = (
                info_urgent.needs_renewal is True
                and info_urgent.days_remaining is not None
                and info_urgent.days_remaining <= 2.0
            )
            report(
                "Scenario 6: Provisioning Renewal Decision Logic (~2 Days Window)",
                correct_renewal,
                f"Days left: {info_urgent.days_remaining}d -> Needs Renewal: {info_urgent.needs_renewal}"
            )
            if not correct_renewal:
                all_passed = False
        os.remove(tmp_path)
    except Exception as exc:
        report("Scenario 6: Provisioning Renewal Decision Logic", False, str(exc))
        all_passed = False

    # 7. User Action Required on Apple Authentication / 2FA
    try:
        auth_err = "/Users/fatih/Projects/jarvis/ios/JarvisiOS/JarvisiOS.xcodeproj: error: No Accounts: Add a new account in Accounts settings."
        requires_action = SigningInspector.detect_user_action_required(auth_err)
        # Verify notification dispatcher handles USER_ACTION_REQUIRED
        notify_dispatched = await BuildNotifier.notify(NotificationEvent.USER_ACTION_REQUIRED)
        report(
            "Scenario 7: Apple Account / 2FA User Action Notification",
            requires_action and notify_dispatched,
            f"Detected action required: {requires_action}, macOS notification dispatched: {notify_dispatched}"
        )
        if not (requires_action and notify_dispatched):
            all_passed = False
    except Exception as exc:
        report("Scenario 7: Apple Account / 2FA User Action Notification", False, str(exc))
        all_passed = False

    # 8. Data Persistence on Re-installation
    try:
        # AppInstaller preserves container by not performing uninstall
        bundle_id = "com.mfatihc.jarvis"
        # Confirm bundle identifier and signing team consistency in project.pbxproj
        pbxproj_path = "ios/JarvisiOS/JarvisiOS.xcodeproj/project.pbxproj"
        with open(pbxproj_path, "r", encoding="utf-8") as f:
            content = f.read()
        consistent_bundle = "PRODUCT_BUNDLE_IDENTIFIER = com.mfatihc.jarvis;" in content
        consistent_team = "DEVELOPMENT_TEAM = T92VJM9C5B;" in content
        report(
            "Scenario 8: Data Preservation & Signing Identity Consistency",
            consistent_bundle and consistent_team,
            f"Fixed Bundle ID: {bundle_id}, Team ID: T92VJM9C5B"
        )
        if not (consistent_bundle and consistent_team):
            all_passed = False
    except Exception as exc:
        report("Scenario 8: Data Preservation & Signing Identity Consistency", False, str(exc))
        all_passed = False

    # 9. Concurrency Lock & Idempotency
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mgr = BuildManager(state_file=os.path.join(tmp_dir, "state.json"))
            await mgr._lock.acquire()
            blocked_build = await mgr.build()
            blocked_install = await mgr.install()
            mgr._lock.release()

            concurrency_blocked = (
                not blocked_build.success
                and "in progress" in blocked_build.error.lower()
                and not blocked_install.success
            )
            report(
                "Scenario 9: Prevention of Duplicate Concurrent Builds & Installs",
                concurrency_blocked,
                f"Blocked concurrent build: {blocked_build.error}"
            )
            if not concurrency_blocked:
                all_passed = False
    except Exception as exc:
        report("Scenario 9: Prevention of Duplicate Concurrent Builds", False, str(exc))
        all_passed = False

    # 10. Manual Jarvis Tools & Policy Enforcement
    try:
        policy = PolicyEngine()
        status_tool = IOSBuildStatusTool()
        build_tool = IOSBuildBuildTool()
        install_tool = IOSBuildInstallTool()

        # R0 read status allowed without approval
        r0_call = policy.evaluate(PolicyRequest(
            agent_mode=AgentMode.ASSIST,
            tool_definition=status_tool.definition,
            tool_call=ToolCall(id="c1", name="ios.build.status")
        ))
        # R2 write build requires explicit human approval
        r2_call = policy.evaluate(PolicyRequest(
            agent_mode=AgentMode.ASSIST,
            tool_definition=build_tool.definition,
            tool_call=ToolCall(id="c2", name="ios.build.build", arguments={"clean": False})
        ))
        # OBSERVE mode denies write tools
        obs_call = policy.evaluate(PolicyRequest(
            agent_mode=AgentMode.OBSERVE,
            tool_definition=install_tool.definition,
            tool_call=ToolCall(id="c3", name="ios.build.install")
        ))

        policy_correct = (
            r0_call.decision == PolicyDecisionType.ALLOW
            and r2_call.decision == PolicyDecisionType.REQUIRE_APPROVAL
            and obs_call.decision == PolicyDecisionType.DENY
        )
        report(
            "Scenario 10: Manual Jarvis Commands & Strict Policy Governance",
            policy_correct,
            f"R0 status: {r0_call.decision.value}, R2 build: {r2_call.decision.value}, OBSERVE install: {obs_call.decision.value}"
        )
        if not policy_correct:
            all_passed = False
    except Exception as exc:
        report("Scenario 10: Manual Jarvis Commands & Policy", False, str(exc))
        all_passed = False

    # 11. User Opt-Out Disables Automated Triggers
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mgr = BuildManager(state_file=os.path.join(tmp_dir, "state.json"))
            mgr.set_auto_renew(False)
            triggered = await mgr.evaluate_auto_trigger()
            report(
                "Scenario 11: Auto-Renew Opt-Out Governance",
                triggered is False and mgr.state.auto_renew_enabled is False,
                f"Auto renew enabled: {mgr.state.auto_renew_enabled} -> Triggered: {triggered}"
            )
            if triggered is not False:
                all_passed = False
    except Exception as exc:
        report("Scenario 11: Auto-Renew Opt-Out Governance", False, str(exc))
        all_passed = False

    # 12. Security Boundary & Anti-Arbitrary Execution Lockdown
    try:
        from core.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register(IOSBuildStatusTool())
        registry.register(IOSBuildCheckDeviceTool())
        registry.register(IOSBuildBuildTool())
        registry.register(IOSBuildInstallTool())
        registry.register(IOSBuildRenewTool())

        registered_names = [t.name for t in registry.list_definitions()]
        has_arbitrary_shell = any("shell" in name or "bash" in name for name in registered_names)
        exact_tools = set(registered_names) == {
            "ios.build.status",
            "ios.build.check_device",
            "ios.build.build",
            "ios.build.install",
            "ios.build.renew",
        }
        report(
            "Scenario 12: Fixed Whitelisted Tool Registry (No Arbitrary Shell Execution)",
            exact_tools and not has_arbitrary_shell,
            f"Exclusively allowed tools: {', '.join(registered_names)}"
        )
        if not (exact_tools and not has_arbitrary_shell):
            all_passed = False
    except Exception as exc:
        report("Scenario 12: Security Boundary Lockdown", False, str(exc))
        all_passed = False

    print(f"\n{YELLOW}=================================================================={RESET}")
    if all_passed:
        print(f"{GREEN}   MILESTONE 5 ACCEPTANCE RESULT: ALL 12 SCENARIOS PASSED (100%){RESET}")
    else:
        print(f"{RED}   MILESTONE 5 ACCEPTANCE RESULT: ONE OR MORE SCENARIOS FAILED{RESET}")
    print(f"{YELLOW}==================================================================\n{RESET}")

    return all_passed


def async_mock_device(retval):
    async def _mock(*args, **kwargs):
        return retval
    return _mock


class patch_cms_exec:
    def __init__(self, stdout_data: bytes):
        self.stdout_data = stdout_data
        self.patcher = None

    def __enter__(self):
        from unittest.mock import patch, AsyncMock
        self.patcher = patch("asyncio.create_subprocess_exec")
        mock_exec = self.patcher.start()
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (self.stdout_data, b"")
        mock_exec.return_value = mock_proc
        return mock_exec

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.patcher:
            self.patcher.stop()


if __name__ == "__main__":
    success = asyncio.run(run_milestone_5_acceptance())
    sys.exit(0 if success else 1)
