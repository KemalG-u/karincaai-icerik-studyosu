"""Validation for read-only, persisted evidence about bookmarked X sources."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, unquote
from typing import Any


def valid_source_url(value: Any) -> bool:
    """Syntax only: never resolves a host or fetches a URL, never normalizes input."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in unquote(value)) or '\\' in value:
        return False
    try:
        parsed = urlsplit(value)
        return bool('?' not in value and '#' not in value and parsed.scheme == 'https' and parsed.hostname and parsed.username is None
                    and parsed.password is None and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        return False


X_ID_RE = re.compile(r"^\d{15,20}$")
X_URL_RE = re.compile(r"^https://(?:www\.)?x\.com/[A-Za-z0-9_]{1,15}/status/(\d{15,20})(?:[/?#].*)?$")
FULL_TEXT_STATUSES = {"full", "partial", "unavailable"}


def _string(value: Any, label: str, limit: int, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Kanıt alanı geçersiz: {label}.")
    return value


def _string_list(value: Any, label: str, limit: int = 10_000) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"Kanıt listesi geçersiz: {label}.")
    if not all(isinstance(item, str) and len(item) <= limit for item in value):
        raise ValueError(f"Kanıt listesi geçersiz: {label}.")
    return list(value)


def validate_source_enrichment(payload: Any) -> list[dict[str, Any]]:
    """Return a bounded, schema-checked copy of an enrichment document's items."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Kaynak kanıt paketi bir items listesi içermelidir.")
    items = payload["items"]
    if not 1 <= len(items) <= 500:
        raise ValueError("Kaynak kanıt paketi öğe sayısı geçersiz.")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Kaynak kanıt öğesi nesne olmalıdır.")
        required = (
            "id", "url", "author", "checked_at", "full_text_status", "full_text",
            "full_text_evidence", "linked_primary_sources", "verified_claims",
            "unsupported_claims", "recommended_turkish_angle", "rights_media_note",
        )
        if any(key not in item for key in required):
            raise ValueError("Kaynak kanıt alanları eksik.")
        item_id = _string(item["id"], "id", 20)
        url = _string(item["url"], "url", 2_048)
        match = X_URL_RE.fullmatch(url)
        if not X_ID_RE.fullmatch(item_id) or not match or match.group(1) != item_id or not valid_source_url(url):
            raise ValueError("Kaynak kanıt kimliği ve URL'si eşleşmelidir.")
        if item_id in seen_ids:
            raise ValueError("Kaynak kanıt paketi aynı kimliği iki kez içeremez.")
        seen_ids.add(item_id)
        status = item["full_text_status"]
        if not isinstance(status, str) or status not in FULL_TEXT_STATUSES:
            raise ValueError("Tam metin erişim durumu geçersiz.")
        full_text = _string(item["full_text"], "full_text", 100_000, required=False)
        if status == "full" and (not full_text or not full_text.strip()):
            raise ValueError("Tam erişim durumunda tam metin zorunludur.")
        primary_sources = item["linked_primary_sources"]
        if not isinstance(primary_sources, list) or len(primary_sources) > 100:
            raise ValueError("Bağlı birincil kaynak listesi geçersiz.")
        linked_primary_sources: list[dict[str, str]] = []
        for source in primary_sources:
            if not isinstance(source, dict) or set(source) != {"url", "relation"}:
                raise ValueError("Bağlı birincil kaynak geçersiz.")
            if not valid_source_url(source["url"]):
                raise ValueError("Bağlı kaynak URL'si geçersiz veya özel bileşen içeriyor.")
            linked_primary_sources.append({
                "url": _string(source["url"], "linked_primary_sources.url", 2_048),
                "relation": _string(source["relation"], "linked_primary_sources.relation", 10_000),
            })
        normalized.append({
            "id": item_id,
            "url": url,
            "author": _string(item["author"], "author", 500),
            "checked_at": _string(item["checked_at"], "checked_at", 100),
            # This is an access-coverage flag. It is deliberately not a truth flag.
            "full_text_status": status,
            "full_text": full_text,
            "full_text_evidence": _string(item["full_text_evidence"], "full_text_evidence", 20_000),
            "linked_primary_sources": linked_primary_sources,
            "verified_claims": _string_list(item["verified_claims"], "verified_claims"),
            "unsupported_claims": _string_list(item["unsupported_claims"], "unsupported_claims"),
            "recommended_turkish_angle": _string(item["recommended_turkish_angle"], "recommended_turkish_angle", 20_000),
            "rights_media_note": _string(item["rights_media_note"], "rights_media_note", 20_000),
        })
    return normalized
