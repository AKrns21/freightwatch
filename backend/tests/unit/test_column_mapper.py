"""Tests for column_mapper.py — port of service-mapper.service.spec.ts."""

from __future__ import annotations

import pytest

from app.services.parsing.column_mapper import bulk_normalize, normalize


class TestNormalize:
    def test_empty_string_returns_standard(self) -> None:
        assert normalize("") == "STANDARD"

    def test_none_returns_standard(self) -> None:
        assert normalize(None) == "STANDARD"  # type: ignore[arg-type]

    def test_whitespace_only_returns_standard(self) -> None:
        assert normalize("   ") == "STANDARD"

    # --- Express ---

    def test_express_variants(self) -> None:
        assert normalize("Express Delivery") == "EXPRESS"
        assert normalize("24h Service") == "EXPRESS"
        assert normalize("Overnight") == "EXPRESS"
        assert normalize("Next Day Delivery") == "EXPRESS"
        assert normalize("Eilsendung") == "EXPRESS"
        assert normalize("Schnell Service") == "EXPRESS"

    # --- Same Day ---

    def test_same_day_variants(self) -> None:
        assert normalize("Same Day") == "SAME_DAY"
        assert normalize("SameDay Delivery") == "SAME_DAY"
        assert normalize("Same Day Service") == "SAME_DAY"

    # Same Day must beat Express (pattern order check)
    def test_same_day_takes_priority_over_express(self) -> None:
        # "sameday" should not fall through to "day" matching express
        assert normalize("Same Day Express") == "SAME_DAY"

    # --- Economy ---

    def test_economy_variants(self) -> None:
        assert normalize("Economy") == "ECONOMY"
        assert normalize("Eco Delivery") == "ECONOMY"
        assert normalize("Slow Service") == "ECONOMY"
        assert normalize("Spar Versand") == "ECONOMY"
        assert normalize("Günstig") == "ECONOMY"
        assert normalize("Cheap Delivery") == "ECONOMY"
        assert normalize("Sparversand") == "ECONOMY"
        assert normalize("Langsam") == "ECONOMY"

    # --- Premium ---

    def test_premium_variants(self) -> None:
        assert normalize("Premium Service") == "PREMIUM"
        assert normalize("Priority Delivery") == "PREMIUM"
        assert normalize("First Class") == "PREMIUM"
        assert normalize("FirstClass Service") == "PREMIUM"

    # --- Standard fallback ---

    def test_standard_variants(self) -> None:
        assert normalize("Standard") == "STANDARD"
        assert normalize("Normal Delivery") == "STANDARD"
        assert normalize("Regular Service") == "STANDARD"
        assert normalize("Unknown Service Type") == "STANDARD"

    # --- Case insensitivity ---

    def test_case_insensitive(self) -> None:
        assert normalize("EXPRESS") == "EXPRESS"
        assert normalize("express") == "EXPRESS"
        assert normalize("ExPrEsS") == "EXPRESS"

    # --- Whitespace handling ---

    def test_leading_trailing_whitespace(self) -> None:
        assert normalize("  Express  ") == "EXPRESS"
        assert normalize("\tEconomy\n") == "ECONOMY"


class TestBulkNormalize:
    def test_normalizes_multiple_texts(self) -> None:
        result = bulk_normalize(["Express", "Standard", "Economy"])
        assert result["Express"] == "EXPRESS"
        assert result["Standard"] == "STANDARD"
        assert result["Economy"] == "ECONOMY"

    def test_empty_list(self) -> None:
        assert bulk_normalize([]) == {}

    def test_deduplicates_identical_inputs(self) -> None:
        result = bulk_normalize(["Express", "Express", "Express"])
        assert len(result) == 1
        assert result["Express"] == "EXPRESS"

    def test_stores_each_case_variation_separately(self) -> None:
        # Different string values → separate keys, same normalized output
        result = bulk_normalize(["Express", "express", "EXPRESS"])
        assert len(result) == 3
        assert result["Express"] == "EXPRESS"
        assert result["express"] == "EXPRESS"
        assert result["EXPRESS"] == "EXPRESS"
