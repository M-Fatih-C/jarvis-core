#!/usr/bin/env python3
"""JARVIS Local Memory Consent Management CLI Tool.

Enforces explicit, granular, category-specific user consent for sensitive memory categories:
- Identity and student identifiers (identity_and_school_ids)
- Historical financial information (financial_historical)
- Health information (health_historical)
- Personal-life notes (personal_historical)
- Family relationships (family)

Usage:
  uv run python scripts/manage_consent.py status
  uv run python scripts/manage_consent.py review
  uv run python scripts/manage_consent.py grant --category financial_historical
  uv run python scripts/manage_consent.py revoke --category financial_historical
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from core.config.settings import Settings
from core.memory.consent import (
    ConsentStatus,
    ConsentStore,
    REGISTERED_SENSITIVE_CATEGORIES,
)


def get_record_count_for_category(db_path: str, category: str, aliases: list[str]) -> int:
    """Count how many private memory records exist in SQLite for a given category."""
    all_cats = [category] + aliases
    placeholders = ",".join("?" for _ in all_cats)
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                f"SELECT count(*) FROM memories WHERE sensitivity = 'private' AND category IN ({placeholders})",
                all_cats,
            )
            return cur.fetchone()[0]
    except Exception:
        return 0


def cmd_status(store: ConsentStore, db_path: str) -> None:
    """Print the current consent status of all sensitive categories."""
    consents = store.list_consents()
    consent_map = {c.category: c for c in consents}

    print("=" * 80)
    print("JARVIS KİŞİSEL HAFIZA KULLANICI RIZA VE İZİN DURUMU (CONSENT STATUS)")
    print("=" * 80)
    print(f"Veritabanı: {db_path}\n")

    print(f"{'KATEGORİ':<28} | {'DURUM':<10} | {'KAYIT':<6} | {'GÜNCELLENME':<20} | {'AÇIKLAMA'}")
    print("-" * 80)

    for cat_key, meta in REGISTERED_SENSITIVE_CATEGORIES.items():
        rec = consent_map.get(cat_key)
        status_val = rec.status.value.upper() if rec else "PENDING"
        aliases = [a.strip() for a in meta.get("aliases", "").split(",")]
        count = get_record_count_for_category(db_path, cat_key, aliases)

        ts = "Henüz verilmedi"
        if rec and rec.granted_at and rec.status == ConsentStatus.GRANTED:
            ts = rec.granted_at[:19].replace("T", " ")
        elif rec and rec.revoked_at and rec.status == ConsentStatus.REVOKED:
            ts = f"İptal: {rec.revoked_at[:10]}"

        status_str = f"[{status_val}]"
        if status_val == "GRANTED":
            status_display = f"\033[92m{status_str:<10}\033[0m"
        elif status_val == "PENDING":
            status_display = f"\033[93m{status_str:<10}\033[0m"
        else:
            status_display = f"\033[91m{status_str:<10}\033[0m"

        print(f"{cat_key:<28} | {status_display} | {count:<6} | {ts:<20} | {meta['display_name']}")

    print("=" * 80)
    print("Güvenlik Notu: PENDING veya REVOKED durumundaki kategorilere ait şifreli kayıtlar")
    print("hafıza sorgularında ve karar süreçlerinde KESİNLİKLE çözülmez ve getirilmez.")
    print("=" * 80)


def cmd_grant(store: ConsentStore, category: str, notes: str | None = None) -> None:
    canon = store.resolve_category(category)
    meta = REGISTERED_SENSITIVE_CATEGORIES.get(canon, {})
    disp = meta.get("display_name", canon)

    store.grant_consent(canon, granted_by=Settings().jarvis_uid, notes=notes)
    print(f"\n✅ BAŞARILI: '{canon}' ({disp}) kategorisi için açık kullanıcı rızası VERİLDİ.")
    print("Bu kategoriye ait şifreli kayıtlar yetkili sorgularda çözülebilecektir.\n")


def cmd_revoke(store: ConsentStore, category: str, notes: str | None = None) -> None:
    canon = store.resolve_category(category)
    meta = REGISTERED_SENSITIVE_CATEGORIES.get(canon, {})
    disp = meta.get("display_name", canon)

    store.revoke_consent(canon, notes=notes)
    print(f"\n🛑 İPTAL EDİLDİ: '{canon}' ({disp}) kategorisi için kullanıcı rızası GERİ ÇEKİLDİ.")
    print("Bu kategoriye ait şifreli kayıtların erişimi derhal durduruldu.\n")


def cmd_review(store: ConsentStore, db_path: str) -> None:
    """Interactive review tool for local user consent."""
    print("=" * 80)
    print("JARVIS HASSAS VERİ RIZA İNCELEME VE YETKİLENDİRME SİHİRBAZI")
    print("=" * 80)
    print("Aşağıdaki her kategori için verilerinizin yerel olarak şifreli saklanması ve")
    print("yalnızca yetkili bağlamlarda çözülerek kullanılması için açık onayınız gereklidir.")
    print("-" * 80)

    for cat_key, meta in REGISTERED_SENSITIVE_CATEGORIES.items():
        aliases = [a.strip() for a in meta.get("aliases", "").split(",")]
        count = get_record_count_for_category(db_path, cat_key, aliases)
        is_consented = store.is_category_consented(cat_key)
        cur_status = "VERİLDİ" if is_consented else "VERİLMEDİ (PENDING/REVOKED)"

        print(f"\nKategori: \033[1m{meta['display_name']}\033[0m ({cat_key})")
        print(f"Tanım:    {meta['description']}")
        print(f"Mevcut Kayıt: {count} adet şifreli kayıt")
        print(f"Mevcut Durum: {cur_status}")

        try:
            choice = input("Bu kategorinin kullanımına izin veriyor musunuz? [e (evet) / h (hayır) / s (geç)]: ").strip().lower()
            if choice in ("e", "evet", "y", "yes"):
                store.grant_consent(cat_key, granted_by=Settings().jarvis_uid)
                print(f"-> '{cat_key}' için rıza kaydedildi.")
            elif choice in ("h", "hayır", "n", "no"):
                store.revoke_consent(cat_key, notes="Revoked via review wizard")
                print(f"-> '{cat_key}' için rıza iptal edildi.")
            else:
                print("-> Değişiklik yapılmadan geçildi.")
        except (EOFError, KeyboardInterrupt):
            print("\nİnceleme sonlandırıldı.")
            break

    print("\n" + "=" * 80)
    print("İnceleme tamamlandı. Güncel durum:")
    cmd_status(store, db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage JARVIS sensitive memory consent")
    parser.add_argument("--db-path", default=None, help="Path to SQLite memory database")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show current consent status of all categories")
    subparsers.add_parser("review", help="Interactively review and authorize categories")

    grant_parser = subparsers.add_parser("grant", help="Grant consent for a category")
    grant_parser.add_argument("--category", "-c", required=True, help="Category name or alias")
    grant_parser.add_argument("--notes", help="Optional notes on consent decision")

    revoke_parser = subparsers.add_parser("revoke", help="Revoke consent for a category")
    revoke_parser.add_argument("--category", "-c", required=True, help="Category name or alias")
    revoke_parser.add_argument("--notes", help="Optional notes on revocation")

    args = parser.parse_args()

    db_path = args.db_path or os.path.expanduser(Settings().memory_db_path)
    if not os.path.exists(db_path):
        print(f"HATA: Veritabanı bulunamadı: {db_path}", file=sys.stderr)
        sys.exit(1)

    store = ConsentStore(db_path=db_path)

    if args.command == "status" or not args.command:
        cmd_status(store, db_path)
    elif args.command == "grant":
        cmd_grant(store, args.category, args.notes)
    elif args.command == "revoke":
        cmd_revoke(store, args.category, args.notes)
    elif args.command == "review":
        cmd_review(store, db_path)


if __name__ == "__main__":
    main()
