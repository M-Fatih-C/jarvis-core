"""Firebase integration package for Firestore persistence, commands, and device telemetry."""

from integrations.firebase.client import FirestoreClientProvider
from integrations.firebase.command_repository import FirestoreCommandRepository
from integrations.firebase.device_repository import FirestoreDeviceRepository
from integrations.firebase.exceptions import (
    FirebaseError,
    FirestorePermissionError,
    FirestoreUnavailableError,
)
from integrations.firebase.memory_repository import FirestoreMemoryRepository

__all__ = [
    "FirebaseError",
    "FirestoreClientProvider",
    "FirestoreCommandRepository",
    "FirestoreDeviceRepository",
    "FirestoreMemoryRepository",
    "FirestorePermissionError",
    "FirestoreUnavailableError",
]
