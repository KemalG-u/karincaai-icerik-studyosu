"""Versioned KarıncaAI content-standard loader."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


STANDARD_PATH = Path(__file__).with_name("content_standard.v1.json")
EXPECTED_REVIEW_KEYS = (
    "usefulness", "evidence", "concreteness", "accessibility",
    "originality", "natural_voice", "focus", "boundaries",
)


def validate_content_standard(value: Any) -> dict[str, Any]:
    """Fail closed when the active standard loses its versioned contract."""
    if not isinstance(value, dict):
        raise ValueError("İçerik standardı nesne olmalıdır.")
    required = {
        "standard_id", "version", "status", "owner", "scope", "audience", "promise",
        "positioning", "content_outcomes", "voice", "language", "content_types",
        "source_integrity", "originality", "composition", "review", "anti_patterns",
        "calibration_examples", "learning", "operation",
    }
    if not required.issubset(value):
        raise ValueError("İçerik standardı zorunlu bölümleri eksik.")
    if value.get("standard_id") != "karincaai-content-v1" or value.get("version") != "1.0.0":
        raise ValueError("İçerik standardı kimliği veya sürümü geçersiz.")
    operation = value.get("operation")
    if not isinstance(operation, dict) or operation.get("content_quota") is not None or operation.get("publishing_quota") is not None:
        raise ValueError("İçerik standardı kota dayatamaz.")
    dimensions = value.get("review", {}).get("dimensions") if isinstance(value.get("review"), dict) else None
    if not isinstance(dimensions, list) or len(dimensions) != len(EXPECTED_REVIEW_KEYS):
        raise ValueError("İçerik standardı inceleme boyutları eksik veya fazla.")
    keys = [item.get("key") for item in dimensions if isinstance(item, dict)]
    if len(keys) != len(dimensions) or len(set(keys)) != len(keys):
        raise ValueError("İçerik standardı inceleme anahtarları benzersiz olmalıdır.")
    if tuple(keys) != EXPECTED_REVIEW_KEYS:
        raise ValueError("İçerik standardı inceleme anahtarları veya sırası geçersiz.")
    for item in dimensions:
        if set(item) != {"key", "question", "pass", "hard_gate"}:
            raise ValueError("İçerik standardı inceleme alanları geçersiz.")
        if not isinstance(item["question"], str) or not item["question"].strip() or not isinstance(item["pass"], str) or not item["pass"].strip() or type(item["hard_gate"]) is not bool:
            raise ValueError("İçerik standardı inceleme ölçütü geçersiz.")
    if not isinstance(value.get("anti_patterns"), list) or len(value["anti_patterns"]) < 8:
        raise ValueError("İçerik standardı yapay kalıp denetimi eksik.")
    examples = value.get("calibration_examples")
    if not isinstance(examples, list) or [item.get("verdict") for item in examples if isinstance(item, dict)] != ["near_target", "needs_reframe"]:
        raise ValueError("İçerik standardı karşıt kalibrasyon örnekleri eksik.")
    if any(set(item) != {"verdict", "text", "reasons", "provenance", "rights"} or item["provenance"] != "sentetik-bu-secki-icin-yazildi" or item["rights"] != "MIT" or not isinstance(item["text"], str) or not item["text"].strip() or not isinstance(item["reasons"], list) or not item["reasons"] for item in examples):
        raise ValueError("İçerik standardı kalibrasyon örneği geçersiz.")
    return value


@lru_cache(maxsize=1)
def load_content_standard() -> dict[str, Any]:
    """Load and validate the active, versioned standard without mutating it."""
    value = json.loads(STANDARD_PATH.read_text(encoding="utf-8"))
    return validate_content_standard(value)
