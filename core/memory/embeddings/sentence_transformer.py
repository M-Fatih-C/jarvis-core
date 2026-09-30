"""Local sentence transformer embedding provider using Hugging Face models."""

import asyncio
from typing import Any
from core.logging.setup import get_logger
from core.memory.embeddings.base import EmbeddingProvider

logger = get_logger("jarvis.memory.embeddings.st")


class LocalSentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Local embedding generator using sentence-transformers (e.g. intfloat/multilingual-e5-small).
    
    Model is lazy-loaded on the first embedding request and executed in an asyncio thread pool
    to avoid blocking the main async event loop.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self._model_name = model_name
        self._model: Any = None
        self._dimensions: int = 384
        self._lock = asyncio.Lock()

    def _ensure_loaded_sync(self) -> None:
        if self._model is None:
            logger.info("loading_embedding_model", model_name=self._model_name)
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            self._dimensions = int(self._model.get_sentence_embedding_dimension())
            logger.info("embedding_model_loaded", model_name=self._model_name, dimensions=self._dimensions)

    async def _ensure_loaded(self) -> None:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    await asyncio.to_thread(self._ensure_loaded_sync)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_query(self, text: str) -> list[float]:
        await self._ensure_loaded()
        # E5 models typically benefit from "query: " prefix
        prefix = "query: " if "e5" in self._model_name.lower() else ""
        formatted_text = f"{prefix}{text}"
        embedding = await asyncio.to_thread(
            self._model.encode,
            formatted_text,
            normalize_embeddings=True,
        )
        return [float(x) for x in embedding]

    async def embed_document(self, text: str) -> list[float]:
        await self._ensure_loaded()
        # E5 models typically benefit from "passage: " prefix
        prefix = "passage: " if "e5" in self._model_name.lower() else ""
        formatted_text = f"{prefix}{text}"
        embedding = await asyncio.to_thread(
            self._model.encode,
            formatted_text,
            normalize_embeddings=True,
        )
        return [float(x) for x in embedding]
