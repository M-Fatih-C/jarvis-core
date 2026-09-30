"""Unit tests ensuring both DeterministicMockEmbeddingProvider and LocalSentenceTransformerEmbeddingProvider satisfy the EmbeddingProvider contract."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.memory.embeddings.base import EmbeddingProvider
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.embeddings.sentence_transformer import LocalSentenceTransformerEmbeddingProvider


@pytest.mark.asyncio
async def test_deterministic_mock_embedding_provider_contract() -> None:
    provider = DeterministicMockEmbeddingProvider(dimensions=384)

    assert isinstance(provider, EmbeddingProvider)
    assert provider.dimensions == 384

    # embed_query
    q_emb = await provider.embed_query("Hafta içi yan projeler")
    assert isinstance(q_emb, list)
    assert len(q_emb) == 384
    assert all(isinstance(x, float) for x in q_emb)

    # embed_document
    d_emb = await provider.embed_document("Hafta içi yan projeler")
    assert isinstance(d_emb, list)
    assert len(d_emb) == 384

    # Determinism
    q_emb_2 = await provider.embed_query("Hafta içi yan projeler")
    assert q_emb == q_emb_2

    # Sensitivity to text differences
    diff_emb = await provider.embed_query("Tamamen farklı bir konu")
    assert q_emb != diff_emb


@pytest.mark.asyncio
async def test_sentence_transformer_provider_contract_with_mocked_model() -> None:
    provider = LocalSentenceTransformerEmbeddingProvider(model_name="intfloat/multilingual-e5-small")
    assert isinstance(provider, EmbeddingProvider)
    assert provider.dimensions == 384

    # Mock SentenceTransformer internal model to test provider methods without downloading weights in unit tests
    fake_st = MagicMock()
    fake_st.get_sentence_embedding_dimension.return_value = 384
    fake_st.encode.return_value = [0.05] * 384

    provider._model = fake_st

    q_emb = await provider.embed_query("query test")
    assert isinstance(q_emb, list)
    assert len(q_emb) == 384
    assert q_emb[0] == pytest.approx(0.05)
    # Verify E5 prefix was prepended
    fake_st.encode.assert_called_with("query: query test", normalize_embeddings=True)

    d_emb = await provider.embed_document("document passage")
    assert isinstance(d_emb, list)
    assert len(d_emb) == 384
    fake_st.encode.assert_called_with("passage: document passage", normalize_embeddings=True)
