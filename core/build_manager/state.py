"""Domain models and states for the iOS Build and Installation Manager."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class BuildLifecycleState(str, Enum):
    """Lifecycle state of build and deployment operations."""
    IDLE = "IDLE"
    CHECKING_DEVICE = "CHECKING_DEVICE"
    BUILDING = "BUILDING"
    SIGNING_VERIFIED = "SIGNING_VERIFIED"
    INSTALLING = "INSTALLING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    SIGNING_EXPIRED = "SIGNING_EXPIRED"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"


class NotificationEvent(str, Enum):
    """Events triggering native macOS alerts."""
    BUILD_SUCCEEDED = "BUILD_SUCCEEDED"
    INSTALL_SUCCEEDED = "INSTALL_SUCCEEDED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    SIGNING_EXPIRED = "SIGNING_EXPIRED"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
    BUILD_FAILED = "BUILD_FAILED"
    INSTALL_FAILED = "INSTALL_FAILED"


class DeviceInfo(BaseModel):
    """Normalized iOS device representation discovered via devicectl."""
    identifier: str = Field(description="CoreDevice unique identifier")
    name: str = Field(description="Device user-assigned name (e.g. Fatih)")
    udid: str = Field(default="", description="Hardware UDID")
    model: str = Field(default="", description="Device marketing name (e.g. iPhone 13)")
    product_type: str = Field(default="", description="Product identifier (e.g. iPhone14,5)")
    os_version: str = Field(default="", description="iOS version number")
    pairing_state: str = Field(default="unknown", description="Manual pairing state")
    developer_mode: bool = Field(default=False, description="Whether Developer Mode is enabled")
    transport_type: str = Field(default="unknown", description="wired or wifi connection")
    hostname: str = Field(default="", description="CoreDevice local hostname")
    reachable: bool = Field(default=False, description="Whether device is currently available for deploy")


class ProvisioningInfo(BaseModel):
    """Provisioning profile metadata extracted from embedded.mobileprovision."""
    profile_path: str | None = None
    app_identifier: str | None = None
    team_identifier: str | None = None
    team_name: str | None = None
    creation_date: datetime | None = None
    expiration_date: datetime | None = None
    days_remaining: float | None = None
    is_expired: bool = False
    needs_renewal: bool = False
    user_action_required: bool = False
    error: str | None = None


class BuildArtifact(BaseModel):
    """Information about a compiled .app bundle."""
    app_path: str
    bundle_id: str
    version: str = "1.0.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    codesign_valid: bool = False


class BuildResult(BaseModel):
    """Outcome of an xcodebuild run."""
    success: bool
    artifact: BuildArtifact | None = None
    error: str | None = None
    duration_seconds: float = 0.0
    logs: str | None = None
    requires_user_action: bool = False


class InstallResult(BaseModel):
    """Outcome of an xcrun devicectl app install run."""
    success: bool
    device_id: str
    bundle_id: str
    error: str | None = None
    duration_seconds: float = 0.0
    device_locked: bool = False


class BuildManagerState(BaseModel):
    """Overall persistent state of the iOS Build Manager."""
    status: BuildLifecycleState = BuildLifecycleState.IDLE
    last_device_info: DeviceInfo | None = None
    last_provisioning_info: ProvisioningInfo | None = None
    last_build_result: BuildResult | None = None
    last_install_result: InstallResult | None = None
    last_renewal_attempt: datetime | None = None
    auto_renew_enabled: bool = False
    target_device_name: str = "Fatih"
    target_bundle_id: str = "com.mfatihc.jarvis"
    last_checked_at: datetime | None = None
