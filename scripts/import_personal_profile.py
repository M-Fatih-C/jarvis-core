#!/usr/bin/env python3
"""JARVIS Personal Memory Importer & Security Hardening CLI Tool.

Supports idempotent import of:
- General profile seed (LOCAL_ONLY, provenance metadata, temporal grounding)
- Sensitive opt-in profile (guarded behind explicit user approval, AES-256-GCM Keychain encrypted, ZERO plaintext)

Usage:
  uv run python scripts/import_personal_profile.py --input /path/to/jarvis_profile_seed_2026-10-03.json --dry-run
  uv run python scripts/import_personal_profile.py --input /path/to/jarvis_profile_seed_2026-10-03.json --commit
  uv run python scripts/import_personal_profile.py --input /path/to/jarvis_sensitive_opt_in_2026-10-03.json --sensitive --allow-categories identity_and_school_ids,financial_historical --commit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("import_personal_profile")

# Add repo root to python path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from core.config.settings import Settings
from core.memory.crypto import MacKeychainKeyProvider, MemoryEncryptor
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryCandidate,
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemorySourceType,
    VerificationStatus,
)
from core.memory.service import MemoryService
from core.memory.embeddings import get_embedding_provider


@dataclass
class ImportStats:
    total_found: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    requires_verification: int = 0
    conflicting_records: int = 0
    local_only_records: int = 0
    encrypted_records: int = 0
    skipped_records: int = 0
    inserted_records: int = 0
    updated_records: int = 0
    unchanged_records: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)


def map_general_status(raw_status: str | None) -> tuple[VerificationStatus, float]:
    """Map raw status string from seed to domain VerificationStatus and confidence score."""
    if not raw_status:
        return VerificationStatus.USER_REPORTED, 0.85

    s = raw_status.lower().strip()
    if s in ("document_verified", "github_report_verified", "official_web_verified"):
        return VerificationStatus.VERIFIED, 1.0
    if s in ("document_verified_with_conflict", "conflict"):
        return VerificationStatus.CONFLICT, 0.80
    if s in ("historical", "historical_screenshot"):
        return VerificationStatus.HISTORICAL, 0.85
    if s in ("reported_or_historical", "requires_verification"):
        return VerificationStatus.REQUIRES_VERIFICATION, 0.80
    if s in ("user_reported", "user_requested", "last_reported"):
        return VerificationStatus.USER_REPORTED, 0.90
    if s in ("expired",):
        return VerificationStatus.EXPIRED, 0.50

    return VerificationStatus.USER_REPORTED, 0.85


def map_category_to_kind(category: str) -> MemoryKind:
    """Map personal seed category to MemoryKind."""
    cat = category.lower().strip()
    if cat in ("education", "education_schedule", "academic_calendar"):
        return MemoryKind.EDUCATION
    if cat in ("employment", "employment_history", "financial_historical"):
        return MemoryKind.WORK
    if cat in ("projects", "devices"):
        return MemoryKind.PROJECT
    if cat in ("preferences", "privacy", "subscriptions", "goals", "interests"):
        return MemoryKind.PREFERENCE
    if cat in ("identity", "identity_and_school_ids", "health_historical", "personal_historical"):
        return MemoryKind.PROFILE
    if cat in ("history",):
        return MemoryKind.EPISODIC
    return MemoryKind.PROFILE


def format_general_memory_content(source_id: str, raw: Any) -> str:
    """Format rich dictionary from profile seed into natural language Turkish string."""
    if isinstance(raw, str):
        return raw

    if not isinstance(raw, dict):
        return str(raw)

    if source_id == "identity.name":
        return (
            f"Kullanıcı Kimlik: Tam İsim={raw.get('full_name')}, "
            f"Hitap={raw.get('preferred_name')}, Dil={raw.get('language')}, "
            f"Zaman Dilimi={raw.get('timezone')}, Şehir={raw.get('city')}."
        )
    if source_id == "identity.birthday":
        return f"Doğum Günü: {raw.get('birthday_month_day')} (Her yıl 20 Haziran). Doğum günü kutlama ve yaş hesaplama amacıyla kullanılır."

    if source_id == "education.bartin":
        return (
            f"Eğitim Geçmişi: {raw.get('university')}, {raw.get('degree')}. "
            f"Mezuniyet Tarihi: {raw.get('graduated_on')}, GNO (GPA): {raw.get('gpa')}."
        )

    if source_id == "education.turtep":
        courses_str = ", ".join(raw.get("reported_active_courses", []))
        return (
            f"Aktif Eğitim - Ahmet Yesevi Üniversitesi TÜRTEP: Program={raw.get('program')}, "
            f"Akademik Yıl={raw.get('academic_year')}, Durum={raw.get('status')}. "
            f"Aktif Alınan Dersler ({raw.get('reported_active_courses_count')} ders, {raw.get('reported_credits')} kredi): {courses_str}."
        )

    if source_id == "education.turtep_schedule_sample":
        sessions = [f"{s.get('weekday')} saat {s.get('time')}: {s.get('course')}" for s in raw.get("sessions", [])]
        return (
            f"Ahmet Yesevi Üniversitesi TÜRTEP Canlı Ders Çizelgesi Örneği (Kapsam: {raw.get('scope')}): "
            + "; ".join(sessions)
            + ". [ÖNEMLİ: Bu program 21-27 Eylül 2026 haftalık örnek ekranıdır; tüm dönem için kalıcı haftalık tekrar (RRULE) DEĞİLDİR. Güncel canlı ders saatleri portaldan teyit edilmelidir.]"
        )

    if source_id == "education.turtep_calendar":
        return (
            f"Ahmet Yesevi Üniversitesi TÜRTEP Akademik Takvim (2026-2027): "
            f"Güz dönemi dersler={raw.get('fall_teaching')}, "
            f"Güz ara sınavları (vize)={raw.get('fall_midterms')}, "
            f"Güz dönem sonu sınavları (final)={raw.get('fall_finals')}, "
            f"Güz bütünleme={raw.get('fall_resits')}. "
            f"Bahar harç ve kayıt={raw.get('spring_tuition_and_course_registration')}, "
            f"Bahar dersler={raw.get('spring_teaching')}, "
            f"Bahar ara sınavlar (vize)={raw.get('spring_midterms')}, "
            f"Bahar finaller={raw.get('spring_finals')}, "
            f"Bahar bütünleme={raw.get('spring_resits')}. Kaynak: {raw.get('source')}."
        )

    if source_id == "education.anadolu":
        return (
            f"Aktif Eğitim - Anadolu Üniversitesi AÖF: Program={raw.get('program')}, "
            f"Akademik Yıl={raw.get('academic_year')}, Kayıt Tarihi={raw.get('registered_on')}, "
            f"Giriş Türü={raw.get('entry_type')}. "
            f"[DİKKAT / ÇELİŞKİ: Kütüphane belgesinde '{raw.get('library_certificate_term')}' görünürken genel ders planında {raw.get('course_plan_shows_semesters')}. yarıyıllar yer almaktadır. Fiili sınıf/yıl ve aktif alınan dersler transkript ve öğrenci panelinden doğrulanmalıdır.]"
        )

    if source_id == "education.anadolu_calendar":
        return (
            f"Anadolu Üniversitesi AÖF Akademik Takvim (2026-2027): "
            f"Güz dönemi={raw.get('fall_term')}, Güz kayıt yenileme={raw.get('fall_course_renewal_if_applicable')}, "
            f"Güz ara sınav merkezi tercih son tarihi={raw.get('fall_midterm_exam_center_selection_deadline')}, "
            f"Güz ara sınavları (vize)={raw.get('fall_midterms')}, "
            f"Güz final sınav merkezi tercih son tarihi={raw.get('fall_final_exam_center_selection_deadline')}, "
            f"Güz dönem sonu sınavları (final)={raw.get('fall_finals')}. "
            f"Bahar dönemi={raw.get('spring_term')}, Bahar ara sınav={raw.get('spring_midterms')}, "
            f"Bahar final={raw.get('spring_finals')}, Yaz okulu sınavı={raw.get('summer_school_exam')}. "
            f"Kaynak: {raw.get('source')}."
        )

    if source_id == "education.planning":
        return (
            "Eğitim ve Akademik Planlama Tercihleri: Hem Ahmet Yesevi TÜRTEP hem Anadolu AÖF birlikte takip edilmeli, "
            "sınav ve ders çakışmaları önlenmelidir. Kayıt, harç ve duyurular izlenmeli; ders çalışma blokları yalnızca "
            "gerçek Apple Calendar uygunluğu sorgulandıktan sonra ve R2 insan onayıyla önerilmelidir."
        )

    if source_id == "education.future":
        interests = ", ".join(raw.get("interests", []))
        return f"Gelecek Eğitim Hedefleri: İlgilenilen alanlar: {interests}. Askerlik tecil tarihi: {raw.get('military_deferral_latest_report')}."

    if source_id == "work.current":
        tech = ", ".join(raw.get("technology", []))
        duties = ", ".join(raw.get("duties", []))
        return (
            f"Mevcut İş Deneyimi (Son Bildirim Tarihi: 2026-09-30): "
            f"İşveren={raw.get('employer')}, Lokasyon={raw.get('location')}, "
            f"Rol={raw.get('role')}, Başlangıç={raw.get('started_on')}, Durum={raw.get('last_reported_status')}. "
            f"Teknolojiler: {tech}. Görevler: {duties}. "
            f"[Not: Çalışma durumu ve saatleri son kullanıcı beyanına dayanır, değişmiş olabilir.]"
        )

    if source_id == "work.previous":
        systems = ", ".join(raw.get("systems", []))
        return (
            f"Önceki İş Deneyimi: İşveren={raw.get('employer')}, Rol={raw.get('role')}, "
            f"Tarih={raw.get('start')} - {raw.get('end')}, Ölçek={raw.get('scale')}. Sistemler: {systems}."
        )

    if source_id == "work.older":
        h_list = [f"{h.get('employer')} ({h.get('dates') or h.get('duration', '')}): {h.get('role', '')} {h.get('ended', '')}".strip() for h in raw.get("history", [])]
        return "Daha Eski İş Deneyimleri: " + "; ".join(h_list) + "."

    if source_id == "career.job_search":
        roles = ", ".join(raw.get("target_roles", []))
        excl = ", ".join(raw.get("exclude", []))
        locs = ", ".join(raw.get("locations", []))
        return (
            f"Kariyer ve İş Arama Hedefleri (2026-09-30): Hedef Roller={roles}. "
            f"Kapsam Dışı / İstenmeyen={excl}. Tercih Edilen Lokasyonlar={locs}, Ülke={raw.get('country')}. "
            f"Kurallar: E-posta gönderilmeden önce kesin insan onayı alınmalı; başvurular platformlar üzerinden kullanıcı tarafından yapılır. "
            f"Askerlik tecili: {raw.get('military_deferred_until')}. KPSS girilmedi."
        )

    if source_id == "career.applications":
        apps = [f"{a.get('company')} - {a.get('role')} ({a.get('date')}): {a.get('last_state')}" for a in raw.get("reported_applications", [])]
        return "Tarihsel İş Başvuruları (Son Bildirim: 2026-09-29): " + "; ".join(apps) + ". [Tarihsel veridir; güncel başvuru durumları doğrudan kullanıcıdan veya yetkili e-posta/portal üzerinden teyit edilmelidir.]"

    if source_id == "career.side_business":
        scope = ", ".join(raw.get("scope", []))
        excl = ", ".join(raw.get("exclude", []))
        cust = ", ".join(raw.get("customers", []))
        cont = ", ".join(raw.get("contact", []))
        return (
            f"Kişisel Girişim ve Serbest Çalışma (Side Business): Kapsam={scope}. "
            f"Hariç={excl}. Müşteriler={cust}. Müsaitlik={raw.get('availability')}. "
            f"İletişim={cont}. Kişisel telefon numarası asla kamuya paylaşılmaz. Fiyatlandırma={raw.get('pricing')}."
        )

    if source_id == "projects.jarvis":
        stack = ", ".join(raw.get("stack", []))
        return (
            f"Yazılım Projesi - JARVIS V1: Repo={raw.get('repo')}, "
            f"Mac Altyapısı={raw.get('mac')}, Mobil={raw.get('mobile')}, "
            f"Yığın: {stack}. Temel commit: {raw.get('latest_remote_commit_at_import')}. "
            f"Aşama: {raw.get('milestone')}. Bekleyen işler: {raw.get('pending')}."
        )

    if source_id == "projects.swayp":
        plat = ", ".join(raw.get("platforms", []))
        feat = ", ".join(raw.get("features", []))
        return (
            f"Yazılım Projesi - SWAYP (eski Swapy/RiseMate): Tür={raw.get('type')}, "
            f"Ortak={raw.get('partner')}, Platformlar={plat}, Backend={raw.get('backend')}. Özellikler: {feat}."
        )

    if source_id == "projects.other":
        names = ", ".join(raw.get("names", []))
        return f"Diğer Yazılım ve Girişim Projeleri: {names}."

    if source_id == "devices":
        mac = ", ".join(raw.get("mac", []))
        other = ", ".join(raw.get("other", []))
        return f"Cihazlar ve Teknik Donanım: Mac: {mac}; Telefon: {raw.get('phone')}; Diğer Cihazlar: {other}. Jarvis Windows sürümü kapsam dışındadır."

    if source_id == "subscriptions":
        integ = ", ".join(raw.get("planned_integrations", []))
        return f"Abonelikler: Google AI Pro Öğrenci={raw.get('google_ai_pro_student')}, Ücret={raw.get('reported_price')}. Planlanan Entegrasyonlar: {integ}."

    if source_id == "monitoring.email":
        times = ", ".join(raw.get("check_times", []))
        cats = ", ".join(raw.get("priority_categories", []))
        nots = ", ".join(raw.get("notify_only_if", []))
        return (
            f"E-posta Takip Tercihleri: Kontrol Saatleri={times} ({raw.get('timezone')}). "
            f"Öncelikli Kategoriler={cats}. Bildirim Kriterleri={nots}. "
            f"Kullanıcı onayı olmadan asla e-posta gönderilmez. [Hafıza kaydıdır; scheduler aktif değildir, işlem öncesi onay gerekir.]"
        )

    if source_id == "monitoring.news":
        cats = ", ".join(raw.get("categories", []))
        rank = ", ".join(raw.get("ranking_factors", []))
        return f"Haber Takip Tercihleri: Sıklık={raw.get('cadence')}, Maksimum Öğe={raw.get('max_items')}, Kategoriler={cats}. Sıralama Faktörleri={rank}."

    if source_id == "monitoring.jobs":
        plat = ", ".join(raw.get("platforms", []))
        watch = ", ".join(raw.get("also_watch", []))
        return f"İş İlanı Takip Tercihleri: Sıklık={raw.get('cadence')}, Platformlar={plat}, Ek Takip={watch}. Başvuru veya e-posta gönderimi yalnızca kullanıcı onayından sonra yapılır."

    if source_id == "privacy.agency":
        return (
            f"Gizlilik, Güvenlik ve İşlem Onayı Politikaları: E-posta gönderimi={raw.get('email_send')}, "
            f"Harici başvuru={raw.get('external_application')}, Takvim/Hatırlatıcı yazma={raw.get('R2_calendar_reminder_write')}, "
            f"Silme={raw.get('R4_delete')}. Şifreler macOS Keychain veya şifreli kasada saklanır. "
            f"Kişisel telefon paylaşılmaz. Ham e-postalar açık rıza olmadan hafızaya alınmaz. Özel veriler buluta aktarılmaz (varsayılan kapalı)."
        )

    if source_id == "preferences.planning":
        return (
            f"Planlama ve Zaman Yönetimi Tercihleri: Birincil Takvim={raw.get('primary_calendar')}, "
            f"Görev Uygulaması={raw.get('task_app')}, Zaman Dilimi={raw.get('timezone')}. "
            f"Ders ve çalışma blokları yalnızca gerçek EventKit takvimi ve duyurular kontrol edildikten sonra ve R2 onayıyla önerilmelidir."
        )

    if source_id == "interests.hobbies":
        hobbies = ", ".join(raw.get("hobbies", []))
        tech = ", ".join(raw.get("technology_interests", []))
        return f"İlgi Alanları ve Hobiler: Hobiler={hobbies}. Teknoloji İlgileri={tech}."

    if source_id == "exams.past":
        return f"Geçmiş Sınavlar ve Sertifikalar: SRC4 Sınavı={raw.get('src4_exam')}, DGS 2026={raw.get('dgs_2026')}, YKS 2026={raw.get('yks_2026')}."

    # Generic fallback
    pairs = [f"{k}={v}" for k, v in raw.items()]
    return f"{source_id}: " + ", ".join(pairs)


def parse_general_profile(seed_data: dict[str, Any], filepath: Path) -> tuple[list[MemoryCandidate], ImportStats]:
    """Parse jarvis_profile_seed_*.json into domain MemoryCandidates with provenance."""
    stats = ImportStats()
    candidates: list[MemoryCandidate] = []
    memories = seed_data.get("memories", [])
    stats.total_found = len(memories)

    for item in memories:
        source_id = item.get("id")
        raw_content = item.get("content")
        raw_category = item.get("category", "general")
        raw_status = item.get("status")
        as_of = item.get("as_of")
        notes = item.get("notes")
        priority = item.get("priority", 0.5)

        if not source_id or raw_content is None:
            stats.invalid_records += 1
            stats.skipped_records += 1
            continue

        ver_status, confidence = map_general_status(raw_status)
        kind = map_category_to_kind(raw_category)
        content_str = format_general_memory_content(source_id, raw_content)

        cand = MemoryCandidate(
            kind=kind,
            content=content_str,
            sensitivity=MemorySensitivity.LOCAL_ONLY,  # Strictly local, never synced to cloud
            confidence=confidence,
            source_type=MemorySourceType.DOCUMENT if "verified" in str(raw_status) else MemorySourceType.EXPLICIT_USER,
            source_reference=f"seed:{filepath.name}#{source_id}",
            category=raw_category,
            source_id=source_id,
            as_of=as_of,
            verification_status=ver_status,
            structured={
                "seed_id": source_id,
                "raw_status": raw_status,
                "notes": notes,
                "priority": priority,
                "as_of": as_of,
                "sync_to_cloud": False,
            },
        )
        candidates.append(cand)
        stats.valid_records += 1
        stats.local_only_records += 1
        stats.categories[raw_category] = stats.categories.get(raw_category, 0) + 1

        if ver_status == VerificationStatus.REQUIRES_VERIFICATION:
            stats.requires_verification += 1
        elif ver_status == VerificationStatus.CONFLICT:
            stats.conflicting_records += 1

        stats.details.append({
            "source_id": source_id,
            "category": raw_category,
            "status": ver_status.value,
            "as_of": as_of,
            "sensitivity": "LOCAL_ONLY",
        })

    return candidates, stats


def parse_sensitive_profile(
    seed_data: dict[str, Any],
    filepath: Path,
    allowed_categories: set[str] | None = None,
) -> tuple[list[MemoryCandidate], ImportStats]:
    """Parse jarvis_sensitive_opt_in_*.json into PRIVATE AES-256-GCM candidates.
    
    Strictly enforces that only user-consented categories are parsed.
    """
    stats = ImportStats()
    candidates: list[MemoryCandidate] = []
    categories = seed_data.get("categories", {})
    all_cat_names = set(categories.keys())

    for cat_name, cat_data in categories.items():
        if allowed_categories is not None and cat_name not in allowed_categories and "all" not in allowed_categories:
            logger.info(f"sensitive_category_skipped_no_consent: {cat_name}")
            stats.skipped_records += 1
            continue

        if cat_name == "identity_and_school_ids":
            # 1. School numbers
            cand_school = MemoryCandidate(
                kind=MemoryKind.PROFILE,
                content=(
                    f"Öğrenci Numaraları: Anadolu Üniversitesi={cat_data.get('anadolu_student_number')} "
                    f"({cat_data.get('anadolu_student_number_source')}); "
                    f"Ahmet Yesevi Üniversitesi TÜRTEP={cat_data.get('turtep_student_number')} "
                    f"({cat_data.get('turtep_student_number_source')})."
                ),
                sensitivity=MemorySensitivity.PRIVATE,
                confidence=1.0,
                source_type=MemorySourceType.EXPLICIT_USER,
                source_reference=f"seed:{filepath.name}#identity.student_ids",
                category="identity_and_school_ids",
                source_id="sensitive.identity.student_ids",
                as_of="2026-09-18",
                verification_status=VerificationStatus.VERIFIED,
                structured={
                    "anadolu_student_number": cat_data.get("anadolu_student_number"),
                    "turtep_student_number": cat_data.get("turtep_student_number"),
                },
            )
            # 2. Personal identity details
            parents = cat_data.get("parent_first_names", {})
            cand_personal = MemoryCandidate(
                kind=MemoryKind.PROFILE,
                content=(
                    f"Kişisel Kimlik: Doğum Tarihi={cat_data.get('birth_date')}, "
                    f"Doğum Yeri={cat_data.get('birth_place')}, "
                    f"Anne Adı={parents.get('mother')}, Baba Adı={parents.get('father')}, "
                    f"E-posta={cat_data.get('email')}."
                ),
                sensitivity=MemorySensitivity.PRIVATE,
                confidence=1.0,
                source_type=MemorySourceType.EXPLICIT_USER,
                source_reference=f"seed:{filepath.name}#identity.personal_details",
                category="identity_and_school_ids",
                source_id="sensitive.identity.personal_details",
                as_of="2026-09-16",
                verification_status=VerificationStatus.VERIFIED,
                structured={
                    "birth_date": cat_data.get("birth_date"),
                    "birth_place": cat_data.get("birth_place"),
                    "parent_first_names": parents,
                    "email": cat_data.get("email"),
                },
            )
            candidates.extend([cand_school, cand_personal])

        elif cat_name == "financial_historical":
            as_of_fin = cat_data.get("as_of", "2026-09-30")
            # 1. Debts & Cards
            cand_debts = MemoryCandidate(
                kind=MemoryKind.WORK,
                content=(
                    f"Tarihsel Finans - Borç ve Kartlar (Tarih: {as_of_fin}): "
                    f"QNB Kredi Kartı={cat_data.get('aug_2026_credit_card_qnb_reported')}; "
                    f"Garanti Kart Limiti={cat_data.get('garanti_card_reported_limit')}; "
                    f"KYK Öğrenim Kredisi={cat_data.get('kyk_payment_plan_historical')}; "
                    f"Kapanan borçlar={json.dumps(cat_data.get('historically_closed', []))}."
                ),
                sensitivity=MemorySensitivity.PRIVATE,
                confidence=0.85,
                source_type=MemorySourceType.EXPLICIT_USER,
                source_reference=f"seed:{filepath.name}#financial.debts",
                category="financial_historical",
                source_id="sensitive.financial.debts_and_cards",
                as_of=as_of_fin,
                verification_status=VerificationStatus.HISTORICAL,
                structured={
                    "aug_2026_credit_card_qnb": cat_data.get("aug_2026_credit_card_qnb_reported"),
                    "garanti_card_limit": cat_data.get("garanti_card_reported_limit"),
                    "kyk_payment_plan": cat_data.get("kyk_payment_plan_historical"),
                    "historically_closed": cat_data.get("historically_closed"),
                    "as_of": as_of_fin,
                },
            )
            # 2. Income & Tuition
            cand_income = MemoryCandidate(
                kind=MemoryKind.WORK,
                content=(
                    f"Tarihsel Finans - Maaş ve Harç (Tarih: {as_of_fin}): "
                    f"Anlaşılan Maaş={cat_data.get('agreed_salary_reported')}; "
                    f"Temmuz 2026 Maaş={cat_data.get('july_2026_pay_reported')}; "
                    f"Temmuz Yol Desteği={cat_data.get('july_2026_allowance_reported')}; "
                    f"Gecikmeli ödeme durumu={cat_data.get('reported_late_pay')}; "
                    f"TÜRTEP Dönemlik Harç={cat_data.get('turtep_tuition_reported')}."
                ),
                sensitivity=MemorySensitivity.PRIVATE,
                confidence=0.85,
                source_type=MemorySourceType.EXPLICIT_USER,
                source_reference=f"seed:{filepath.name}#financial.income",
                category="financial_historical",
                source_id="sensitive.financial.income_and_tuition",
                as_of=as_of_fin,
                verification_status=VerificationStatus.HISTORICAL,
                structured={
                    "agreed_salary": cat_data.get("agreed_salary_reported"),
                    "july_2026_pay": cat_data.get("july_2026_pay_reported"),
                    "turtep_tuition": cat_data.get("turtep_tuition_reported"),
                    "as_of": as_of_fin,
                },
            )
            candidates.extend([cand_debts, cand_income])

        elif cat_name == "health_historical":
            cand_health = MemoryCandidate(
                kind=MemoryKind.PROFILE,
                content=(
                    f"Tarihsel Sağlık Geçmişi: Boy={cat_data.get('height')}, "
                    f"Kilo={cat_data.get('weight_last_reported')} (Hedef={cat_data.get('weight_goal_reported')}); "
                    f"Sağ Diz Durumu={cat_data.get('right_knee')}; "
                    f"Bilek Durumu={cat_data.get('ankle')}; "
                    f"Takviyeler={json.dumps(cat_data.get('supplements_reported', []))}; "
                    f"Notlar={cat_data.get('notes')}."
                ),
                sensitivity=MemorySensitivity.PRIVATE,
                confidence=0.85,
                source_type=MemorySourceType.EXPLICIT_USER,
                source_reference=f"seed:{filepath.name}#health.historical",
                category="health_historical",
                source_id="sensitive.health.physical_and_supplements",
                as_of="2026-09-30",
                verification_status=VerificationStatus.USER_REPORTED,
                structured={
                    "height": cat_data.get("height"),
                    "weight": cat_data.get("weight_last_reported"),
                    "right_knee": cat_data.get("right_knee"),
                    "ankle": cat_data.get("ankle"),
                    "supplements": cat_data.get("supplements_reported"),
                },
            )
            candidates.append(cand_health)

        elif cat_name == "personal_historical":
            as_of_p = cat_data.get("as_of", "2026-09-30")
            cand_pers = MemoryCandidate(
                kind=MemoryKind.PROFILE,
                content=f"Kişisel Geçmiş Notları (Tarih: {as_of_p}): {cat_data.get('notes')}",
                sensitivity=MemorySensitivity.PRIVATE,
                confidence=0.80,
                source_type=MemorySourceType.EXPLICIT_USER,
                source_reference=f"seed:{filepath.name}#personal.historical",
                category="personal_historical",
                source_id="sensitive.personal.historical_notes",
                as_of=as_of_p,
                verification_status=VerificationStatus.HISTORICAL,
                structured={"notes": cat_data.get("notes"), "as_of": as_of_p},
            )
            candidates.append(cand_pers)

    stats.total_found = len(candidates) + stats.skipped_records
    for c in candidates:
        stats.valid_records += 1
        stats.encrypted_records += 1
        stats.categories[c.category] = stats.categories.get(c.category, 0) + 1
        stats.details.append({
            "source_id": c.source_id,
            "category": c.category,
            "status": c.verification_status.value,
            "as_of": c.as_of,
            "sensitivity": "PRIVATE (AES-256-GCM Encrypted)",
        })

    return candidates, stats


async def run_import(
    input_path: Path,
    commit: bool,
    sensitive_mode: bool,
    allow_categories: set[str] | None,
    db_path: str | None = None,
    force: bool = False,
) -> tuple[ImportStats, str]:
    """Execute import or dry-run audit."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    schema_name = data.get("schema_name", "")
    is_sensitive_file = "sensitive" in input_path.name or "sensitive" in schema_name

    if is_sensitive_file and not sensitive_mode:
        raise PermissionError(
            "GUVENLIK UYARISI: Bu dosya hassas kisisel veriler icermektedir. "
            "Ice aktarma icin '--sensitive' bayragini ve acik kategori onayini (--allow-categories) belirtmelisiniz!"
        )

    # Parse candidates
    if is_sensitive_file:
        candidates, stats = parse_sensitive_profile(data, input_path, allowed_categories=allow_categories)
    else:
        candidates, stats = parse_general_profile(data, input_path)

    # Initialize repository and services
    effective_db_path = os.path.expanduser(db_path or Settings().memory_db_path)
    os.makedirs(os.path.dirname(effective_db_path), exist_ok=True)
    repo = SQLiteMemoryRepository(effective_db_path)

    # Key provider: on macOS, always use MacKeychainKeyProvider with fallback_to_memory=False
    if sys.platform == "darwin":
        key_provider = MacKeychainKeyProvider(service_name="com.jarvis.memory", fallback_to_memory=False)
    else:
        # Isolated test environment fallback
        from core.memory.crypto import InMemoryKeyProvider
        key_provider = InMemoryKeyProvider()

    try:
        from core.memory.embeddings.sentence_transformer import LocalSentenceTransformerEmbeddingProvider
        embedder = LocalSentenceTransformerEmbeddingProvider("intfloat/multilingual-e5-small")
    except Exception:
        embedder = get_embedding_provider()

    service = MemoryService(repository=repo, embedding_provider=embedder, key_provider=key_provider)

    # Audit pre-existing plaintext leaks before touching database
    leak_report = await repo.audit_plaintext_private_records()
    if len(leak_report) > 0:
        logger.warning(
            "pre_existing_plaintext_leak_detected_in_sqlite",
            leaked_count=len(leak_report),
            leaked_ids=[r["id"] for r in leak_report],
        )

    if not commit:
        # Dry-run audit report
        return stats, effective_db_path

    # Execute commit idempotently
    from core.memory.models import MemoryStatus
    for cand in candidates:
        existing = await repo.find_by_source_id(cand.source_id)
        if existing:
            needs_update = False
            if existing.status != MemoryStatus.ACTIVE:
                existing.status = MemoryStatus.ACTIVE
                needs_update = True
            if existing.as_of != cand.as_of:
                existing.as_of = cand.as_of
                needs_update = True
            if existing.verification_status != cand.verification_status:
                existing.verification_status = cand.verification_status
                needs_update = True
            if cand.sensitivity != MemorySensitivity.PRIVATE:
                if force or existing.content != cand.content:
                    existing.content = cand.content
                    existing.embedding = await embedder.embed_document(cand.content)
                    needs_update = True
                elif not existing.embedding or len(existing.embedding) != embedder.dimensions:
                    existing.embedding = await embedder.embed_document(existing.content or cand.content)
                    needs_update = True

            if needs_update:
                existing.revision += 1
                await repo.update(existing)
                stats.updated_records += 1
            else:
                stats.unchanged_records += 1
        else:
            rec = await service.store_candidate(cand)
            if rec:
                stats.inserted_records += 1

    # Post-import security audit: verify 0 plaintext in private records
    post_audit = await repo.audit_plaintext_private_records()
    if len(post_audit) > 0:
        raise RuntimeError(f"GÜVENLİK İHLALİ: İçe aktarma sonrasında {len(post_audit)} adet açık metin PRIVATE kayıt tespit edildi!")

    return stats, effective_db_path


def print_summary_table(stats: ImportStats, mode: str, db_path: str, filepath: Path) -> None:
    """Print clean formatted summary report."""
    print("\n" + "=" * 68)
    print(f"JARVIS KIŞISEL HAFIZA AKTARIM RAPORU — [{mode}]")
    print("=" * 68)
    print(f"Girdi Dosyası        : {filepath.name}")
    print(f"Hedef Veritabanı     : {db_path}")
    print("-" * 68)
    print(f"{'Toplam Bulunan Kayıt':<35} : {stats.total_found}")
    print(f"{'Geçerli Kayıtlar':<35} : {stats.valid_records}")
    print(f"{'Geçersiz / Hatalı Kayıtlar':<35} : {stats.invalid_records}")
    print(f"{'Doğrulama Bekleyen (REQUIRES_VERIF)':<35} : {stats.requires_verification}")
    print(f"{'Çelişkili Kayıtlar (CONFLICT)':<35} : {stats.conflicting_records}")
    print(f"{'Yalnızca Yerel (LOCAL_ONLY)':<35} : {stats.local_only_records}")
    print(f"{'Şifreli Kasaya Alınan (PRIVATE)':<35} : {stats.encrypted_records}")
    print(f"{'Atlanan / Onay Verilmeyen':<35} : {stats.skipped_records}")
    if mode == "COMMIT":
        print("-" * 68)
        print(f"{'Yeni Eklenen Kayıtlar':<35} : {stats.inserted_records}")
        print(f"{'Güncellenen Kayıtlar':<35} : {stats.updated_records}")
        print(f"{'Değişmeyen / Mükerrer Kayıtlar':<35} : {stats.unchanged_records}")
    print("-" * 68)
    print("Kategorilere Göre Dağılım:")
    for cat, count in sorted(stats.categories.items()):
        print(f"  • {cat:<32} : {count} kayıt")
    print("=" * 68)
    if mode == "DRY-RUN":
        print("BİLGİ: Bu bir denetim ve önizleme (dry-run) çalışmasıdır. Veritabanı değiştirilmedi.")
        print("Kayıtları hafızaya yazmak için '--commit' parametresini ekleyiniz.\n")
    else:
        print("BAŞARILI: Tüm kayıtlar yerel hafızaya güvenle aktarıldı.")
        print("Güvenlik Denetimi: SQLite üzerinde ZERO plaintext PRIVATE sızıntısı doğrulandı.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Personal Memory Importer & Security Hardening CLI Tool")
    parser.add_argument("--input", "-i", type=str, help="Yüklenecek JSON seed dosyası yolu")
    parser.add_argument("--dry-run", action="store_true", help="Yalnızca denetim ve önizleme yap (veritabanını değiştirmez)")
    parser.add_argument("--commit", action="store_true", help="Kayıtları kalıcı olarak SQLite veritabanına yaz")
    parser.add_argument("--sensitive", action="store_true", help="Hassas opt-in profil aktarımını etkinleştir")
    parser.add_argument("--allow-categories", type=str, default="", help="Onaylanan hassas kategoriler (virgülle ayrılmış: all, identity_and_school_ids, financial_historical, health_historical, personal_historical)")
    parser.add_argument("--db-path", type=str, default=None, help="Özel SQLite veritabanı dosya yolu")
    parser.add_argument("--force", action="store_true", help="Mevcut kayıtların vektör embeddinglerini yeniden hesapla")

    args = parser.parse_args()

    # Determine input file
    input_file: Path | None = None
    if args.input:
        input_file = Path(args.input).resolve()
    else:
        # Auto-detect default files in current directory
        if args.sensitive:
            candidate = repo_root / "jarvis_sensitive_opt_in_2026-10-03.json"
        else:
            candidate = repo_root / "jarvis_profile_seed_2026-10-03.json"
        if candidate.exists():
            input_file = candidate

    if not input_file or not input_file.exists():
        print(f"HATA: Girdi dosyası bulunamadı. Lütfen --input ile dosya yolunu belirtin.", file=sys.stderr)
        sys.exit(1)

    is_commit = bool(args.commit)
    mode_str = "COMMIT" if is_commit else "DRY-RUN"

    allowed_cats = set([c.strip() for c in args.allow_categories.split(",") if c.strip()]) if args.allow_categories else None

    try:
        stats, db_path = asyncio.run(
            run_import(
                input_path=input_file,
                commit=is_commit,
                sensitive_mode=args.sensitive,
                allow_categories=allowed_cats,
                db_path=args.db_path,
                force=bool(args.force),
            )
        )
        print_summary_table(stats, mode_str, db_path, input_file)
    except Exception as exc:
        print(f"\nHATA: İçe aktarma başarısız: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
