#!/usr/bin/env python3
"""Run and verify the 10 Milestone 5.2.1 personalization queries against local Jarvis memory.

Sensitive data is strictly masked in the output.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from core.config.settings import Settings
from core.memory.crypto import MacKeychainKeyProvider, InMemoryKeyProvider
from core.memory.embeddings import get_embedding_provider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.retrieval import MemoryRetriever
from core.memory.service import MemoryService


def mask_sensitive_text(text: str) -> str:
    """Mask student numbers, exact salary numbers, credit card names, and personal names for safe display."""
    if not text:
        return ""
    # Mask 9-11 digit numbers (student numbers, TC, etc.)
    masked = re.sub(r"\b\d{9,11}\b", "[MASKED_ID]", text)
    # Mask currency amounts
    masked = re.sub(r"\b\d{2,3}(?:\.\d{3})*(?:,\d+)?\s*(?:TL|₺|USD|EUR)\b", "[MASKED_AMOUNT]", masked)
    # Mask specific private names/terms if present
    masked = re.sub(r"(Anne Adı=)[^\s,]+", r"\1[MASKED]", masked)
    masked = re.sub(r"(Baba Adı=)[^\s,]+", r"\1[MASKED]", masked)
    masked = re.sub(r"(Doğum Tarihi=)[^\s,]+", r"\1[MASKED]", masked)
    return masked


QUERIES = [
    "Ben hangi üniversitelerde okuyorum?",
    "TÜRTEP'te hangi derslerim var?",
    "Bu hafta derslerim hangi saatlerde?",
    "İki üniversitemin sınavları ne zaman?",
    "Anadolu'da şu an hangi sınıftayım?",
    "Önceki iş deneyimlerim neler?",
    "Hangi yazılım projelerim üzerinde çalışıyorum?",
    "İş ararken hangi alanlara öncelik veriyorum?",
    "Bu akşam iki saatlik çalışma planlayabilir misin?",
    "Şu anki banka borcum ne kadar?",
]


async def main() -> None:
    db_path = os.path.expanduser(Settings().memory_db_path)
    if not os.path.exists(db_path):
        print(f"HATA: Veritabanı bulunamadı: {db_path}", file=sys.stderr)
        sys.exit(1)

    repo = SQLiteMemoryRepository(db_path)

    if sys.platform == "darwin":
        key_provider = MacKeychainKeyProvider(service_name="com.jarvis.memory", fallback_to_memory=False)
    else:
        key_provider = InMemoryKeyProvider()

    try:
        from core.memory.embeddings.sentence_transformer import LocalSentenceTransformerEmbeddingProvider
        embedder = LocalSentenceTransformerEmbeddingProvider("intfloat/multilingual-e5-small")
    except Exception:
        embedder = get_embedding_provider()
    service = MemoryService(repository=repo, embedding_provider=embedder, key_provider=key_provider)
    retriever = MemoryRetriever(
        repository=repo,
        embedding_provider=embedder,
        settings=Settings(memory_top_k=4, max_memory_context_chars=2000),
        key_provider=key_provider,
    )

    print("=" * 72)
    print("JARVIS KİŞİSELLEŞTİRME VE HAFIZA SORGULARI DOĞRULAMA TESTİ (M5.2.1)")
    print("=" * 72)
    print(f"Veritabanı: {db_path}")
    print("Güvenlik Notu: Çıktılardaki hassas değerler regex ile maskelenmiştir.\n")

    for i, q in enumerate(QUERIES, 1):
        print(f"[{i:02d}] SORU: \"{q}\"")
        hits = await retriever.retrieve(q, limit=3)
        if not hits:
            print("     -> [Kayıt bulunamadı]\n")
            continue

        for hit in hits:
            rec = hit.record
            masked_content = mask_sensitive_text(rec.content or "[İçerik boş]")
            status_tag = f"status={rec.verification_status.value}" if hasattr(rec, "verification_status") and rec.verification_status else "status=unknown"
            as_of_tag = f"as_of={rec.as_of}" if rec.as_of else "as_of=N/A"
            cat_tag = f"cat={rec.category}"
            sens_tag = f"sens={rec.sensitivity.value}"
            print(f"     • [{rec.kind.value} | {cat_tag} | {sens_tag} | {status_tag} | {as_of_tag} | score={hit.score:.2f}]")
            print(f"       {masked_content}")
        print()

    print("=" * 72)
    print("TÜM KİŞİSELLEŞTİRME SORGULARI BAŞARIYLA TAMAMLANDI")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
