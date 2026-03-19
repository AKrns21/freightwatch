"""ColumnMapper — normalize service-level text to standard codes.

Port of backend_legacy/src/modules/parsing/service-mapper.service.ts

NO DATABASE LOOKUPS — pure regex/pattern matching.
"""

from __future__ import annotations

import re


# Ordered list of (pattern, code) pairs — first match wins.
_SERVICE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"same[\s_-]*day|sameday", re.IGNORECASE), "SAME_DAY"),
    (
        re.compile(
            r"express|24h|next[\s_-]*day|overnight|eilsendung|schnell",
            re.IGNORECASE,
        ),
        "EXPRESS",
    ),
    (
        re.compile(
            r"eco|economy|slow|spar|günstig|cheap|sparversand|langsam",
            re.IGNORECASE,
        ),
        "ECONOMY",
    ),
    (
        re.compile(r"premium|priority|first[\s_-]*class|firstclass", re.IGNORECASE),
        "PREMIUM",
    ),
]


def normalize(service_text: str | None) -> str:
    """Normalize a free-text service description to a standard service code.

    Args:
        service_text: Raw service text from CSV/invoice (may be None or empty).

    Returns:
        One of: "EXPRESS", "SAME_DAY", "ECONOMY", "PREMIUM", "STANDARD".
    """
    if not service_text:
        return "STANDARD"

    text = service_text.strip()
    if not text:
        return "STANDARD"

    for pattern, code in _SERVICE_PATTERNS:
        if pattern.search(text):
            return code

    return "STANDARD"


def bulk_normalize(service_texts: list[str]) -> dict[str, str]:
    """Normalize multiple service texts, deduplicating calls.

    Args:
        service_texts: List of raw service text values.

    Returns:
        Dict mapping each unique input text to its normalized code.
    """
    result: dict[str, str] = {}
    for text in service_texts:
        if text not in result:
            result[text] = normalize(text)
    return result
