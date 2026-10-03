"""Unit tests for GmailNormalizer MIME parsing, HTML to text conversion, and attachment extraction."""

import base64
from datetime import datetime, timezone
import pytest

from integrations.gmail.normalizer import GmailNormalizer, html_to_readable_text


def test_receipt_time_takes_precedence_over_sender_date() -> None:
    from datetime import datetime, timezone
    received = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)
    email = GmailNormalizer.normalize_message({
        "id": "delayed-mail", "threadId": "thread", "internalDate": str(int(received.timestamp() * 1000)),
        "payload": {"headers": [{"name": "Date", "value": "Thu, 01 Oct 2026 14:30:00 +0300"}], "mimeType": "text/plain", "body": {}},
    })
    assert email.received_at == received


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def test_html_to_readable_text_conversion() -> None:
    html_sample = """
    <html>
      <head><style>body { color: red; }</style></head>
      <body>
        <h2>Ders Kaydı Bilgilendirmesi</h2>
        <p>Merhaba Öğrenci,<br/>Ders kayıtları <b>8 Ekim</b> tarihine kadar tamamlanmalıdır.</p>
        <div>Daha fazla bilgi için: <a href="https://universite.edu.tr/kayit">Kayıt Sistemi</a></div>
        <script>console.log('malicious');</script>
      </body>
    </html>
    """
    text = html_to_readable_text(html_sample)
    assert "Ders Kaydı Bilgilendirmesi" in text
    assert "8 Ekim" in text
    assert "Kayıt Sistemi (https://universite.edu.tr/kayit)" in text
    assert "console.log" not in text
    assert "<style>" not in text
    assert "<p>" not in text


def test_normalize_plain_text_message() -> None:
    raw_message = {
        "id": "18928374abcd1234",
        "threadId": "thread_999",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "YBS Proje Teslimi"},
                {"name": "From", "value": "Prof. Dr. Ahmet Yılmaz <ahmet.yilmaz@univ.edu>"},
                {"name": "To", "value": "student@univ.edu"},
                {"name": "Date", "value": "Thu, 01 Oct 2026 14:30:00 +0300"},
            ],
            "mimeType": "text/plain",
            "body": {
                "data": _b64url("Proje raporlarınızı yarın saat 17:00'ye kadar yükleyiniz."),
            },
        },
    }

    email = GmailNormalizer.normalize_message(raw_message)
    assert email.message_id == "18928374abcd1234"
    assert email.thread_id == "thread_999"
    assert email.subject == "YBS Proje Teslimi"
    assert email.sender_email == "ahmet.yilmaz@univ.edu"
    assert "Prof. Dr. Ahmet Yılmaz" in email.sender
    assert email.body_plain == "Proje raporlarınızı yarın saat 17:00'ye kadar yükleyiniz."
    assert email.received_at.year == 2026
    assert len(email.attachments) == 0
    assert len(email.content_hash) == 64


def test_normalize_multipart_with_attachments_metadata_only() -> None:
    raw_message = {
        "id": "msg_with_attachment_123",
        "threadId": "thread_att_1",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Staj Başvuru Formu"},
                {"name": "From", "value": "İnsan Kaynakları <ik@sirket.com>"},
                {"name": "Date", "value": "Wed, 30 Sep 2026 10:00:00 +0000"},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"data": _b64url("Lütfen ekteki formu doldurarak iletiniz.")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "Staj_Basvuru_Formu.pdf",
                    "body": {
                        "attachmentId": "att_blob_id_999",
                        "size": 245760,  # 240 KB
                    },
                },
            ],
        },
    }

    email = GmailNormalizer.normalize_message(raw_message)
    assert email.subject == "Staj Başvuru Formu"
    assert email.body_plain == "Lütfen ekteki formu doldurarak iletiniz."
    assert len(email.attachments) == 1
    att = email.attachments[0]
    assert att.filename == "Staj_Basvuru_Formu.pdf"
    assert att.mime_type == "application/pdf"
    assert att.size_bytes == 245760
    assert att.attachment_id == "att_blob_id_999"


def test_content_hash_deduplication() -> None:
    msg1 = {
        "id": "msg_hash_1",
        "threadId": "t1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Toplantı"}],
            "mimeType": "text/plain",
            "body": {"data": _b64url("Yarın saat 10'da.")},
        },
    }
    msg2 = {
        "id": "msg_hash_1",
        "threadId": "t1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Toplantı"}],
            "mimeType": "text/plain",
            "body": {"data": _b64url("Yarın saat 10'da.")},
        },
    }
    msg3 = {
        "id": "msg_hash_1",
        "threadId": "t1",
        "payload": {
            "headers": [{"name": "Subject", "value": "Toplantı Değişti"}],
            "mimeType": "text/plain",
            "body": {"data": _b64url("Yarın saat 14'te.")},
        },
    }

    email1 = GmailNormalizer.normalize_message(msg1)
    email2 = GmailNormalizer.normalize_message(msg2)
    email3 = GmailNormalizer.normalize_message(msg3)

    assert email1.content_hash == email2.content_hash
    assert email1.content_hash != email3.content_hash
