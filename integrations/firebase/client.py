"""Firestore client initialization and emulator configuration."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from integrations.firebase.exceptions import FirestoreUnavailableError

logger = get_logger("jarvis.firebase.client")


class FirestoreClientProvider:
    """Manages the lifecycle and connection of the Google Cloud Firestore AsyncClient."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any = None

    def get_client(self) -> Any:
        """Return or initialize the Firestore AsyncClient instance."""
        if self._client is not None:
            return self._client

        # Configure emulator host if provided in settings or env
        emulator_host = self._settings.firestore_emulator_host or os.environ.get("FIRESTORE_EMULATOR_HOST")
        if emulator_host:
            os.environ["FIRESTORE_EMULATOR_HOST"] = emulator_host
            logger.info("using_firestore_emulator", host=emulator_host)

        try:
            from google.cloud import firestore
            self._client = firestore.AsyncClient(project=self._settings.firebase_project_id)
            logger.info(
                "firestore_client_initialized",
                project=self._settings.firebase_project_id,
                emulator=bool(emulator_host),
            )
            return self._client
        except Exception as exc:
            logger.error("firestore_initialization_failed", error=str(exc))
            raise FirestoreUnavailableError(f"Could not initialize Firestore client: {exc}") from exc

    async def close(self) -> None:
        """Close client connection safely whether synchronous or asynchronous."""
        if self._client is not None:
            try:
                res = self._client.close()
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
            except Exception:
                pass
            self._client = None
