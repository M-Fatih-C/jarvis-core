"""MIME multipart message normalization, HTML-to-text conversion, and header extraction."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime, parseaddr
import html
import re
from typing import Any

from core.logging.setup import get_logger
from integrations.gmail.models import AttachmentMetadata, NormalizedEmail

logger = get_logger("jarvis.gmail.normalizer")


def _decode_base64url(data: str) -> str:
    """Decode base64url-encoded string from Gmail API."""
    if not data:
        return ""
    # Add padding if needed
    padded = data + "=" * ((4 - len(data) % 4) % 4)
    try:
        decoded_bytes = base64.urlsafe_b64decode(padded)
        return decoded_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("base64url_decode_failed", error=str(exc))
        return ""


def html_to_readable_text(html_content: str) -> str:
    """Convert HTML email body to clean, readable plain text."""
    if not html_content:
        return ""

    # 1. Remove script, style, head, and noscript tags and their contents
    text = re.sub(r"<(script|style|head|noscript)[^>]*>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

    # 2. Convert line breaks and paragraph ends to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|tr|h[1-6]|li|blockquote)[^>]*>", "\n", text, flags=re.IGNORECASE)

    # 3. Format hyperlinks: <a href="url">text</a> -> text (url)
    def _replace_link(match: re.Match[str]) -> str:
        attrs = match.group(1)
        link_text = match.group(2).strip()
        href_match = re.search(r'href=[\'"]([^\'"]+)[\'"]', attrs, re.IGNORECASE)
        if href_match:
            url = href_match.group(1)
            if url.startswith("http") and link_text and link_text != url:
                return f"{link_text} ({url})"
        return link_text

    text = re.sub(r"<a\s+([^>]+)>(.*?)</a>", _replace_link, text, flags=re.DOTALL | re.IGNORECASE)

    # 4. Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # 5. Decode HTML entities
    text = html.unescape(text)

    # 6. Normalize whitespace and newlines
    lines = [line.strip() for line in text.splitlines()]
    # Remove excessive blank lines
    collapsed: list[str] = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 2:
                collapsed.append("")
        else:
            blank_count = 0
            collapsed.append(line)

    return "\n".join(collapsed).strip()


class GmailNormalizer:
    """Parses raw Gmail API message payloads into NormalizedEmail instances."""

    @staticmethod
    def normalize_message(raw_msg: dict[str, Any]) -> NormalizedEmail:
        """Parse raw Gmail message resource into NormalizedEmail."""
        msg_id = raw_msg.get("id", "")
        thread_id = raw_msg.get("threadId", "")
        labels = raw_msg.get("labelIds", [])

        payload = raw_msg.get("payload", {})
        headers_list = payload.get("headers", [])
        headers: dict[str, str] = {}
        for h in headers_list:
            name = h.get("name", "").lower()
            val = h.get("value", "")
            if name:
                headers[name] = val

        subject = headers.get("subject", "(No Subject)")
        raw_from = headers.get("from", "")
        sender_name, sender_email = parseaddr(raw_from)
        sender_display = f"{sender_name} <{sender_email}>" if sender_name else sender_email or raw_from
        recipient = headers.get("to", "")

        # Parse date
        received_at: datetime
        date_str = headers.get("date")
        if date_str:
            try:
                received_at = parsedate_to_datetime(date_str)
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=timezone.utc)
            except Exception:
                # Fallback to internalDate (epoch ms)
                internal_ms = raw_msg.get("internalDate")
                if internal_ms:
                    received_at = datetime.fromtimestamp(int(internal_ms) / 1000.0, tz=timezone.utc)
                else:
                    received_at = datetime.now(timezone.utc)
        else:
            internal_ms = raw_msg.get("internalDate")
            if internal_ms:
                received_at = datetime.fromtimestamp(int(internal_ms) / 1000.0, tz=timezone.utc)
            else:
                received_at = datetime.now(timezone.utc)

        # Extract bodies and attachments recursively
        plain_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[AttachmentMetadata] = []

        def _traverse_parts(part: dict[str, Any]) -> None:
            mime_type = part.get("mimeType", "")
            filename = part.get("filename", "")
            body = part.get("body", {})

            # Check if this part is an attachment
            if filename:
                att_id = body.get("attachmentId", "")
                size = int(body.get("size", 0))
                attachments.append(
                    AttachmentMetadata(
                        attachment_id=att_id,
                        filename=filename,
                        mime_type=mime_type,
                        size_bytes=size,
                    )
                )

            data = body.get("data")
            if data:
                decoded = _decode_base64url(data)
                if mime_type == "text/plain":
                    plain_parts.append(decoded)
                elif mime_type == "text/html":
                    html_parts.append(decoded)

            for subpart in part.get("parts", []):
                _traverse_parts(subpart)

        _traverse_parts(payload)

        # Assemble final body: prefer plain text; fallback to converted HTML
        body_text = ""
        if plain_parts:
            body_text = "\n\n".join(plain_parts).strip()
        elif html_parts:
            html_combined = "\n\n".join(html_parts)
            body_text = html_to_readable_text(html_combined)
        elif raw_msg.get("snippet"):
            body_text = raw_msg["snippet"]

        # Body preview (up to 200 chars)
        preview = (body_text[:197] + "...") if len(body_text) > 200 else body_text

        normalized = NormalizedEmail(
            message_id=msg_id,
            thread_id=thread_id,
            sender=sender_display,
            sender_email=sender_email.lower(),
            recipient=recipient,
            subject=subject,
            received_at=received_at,
            body_plain=body_text,
            body_preview=preview,
            attachments=attachments,
            headers={k: v for k, v in headers.items() if k in {"subject", "from", "to", "date", "message-id", "in-reply-to"}},
            labels=labels,
        )
        normalized.content_hash = normalized.compute_content_hash()
        return normalized
