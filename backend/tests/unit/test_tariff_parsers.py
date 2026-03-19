"""Unit tests for TariffXlsxParser and TariffPdfParser.

All tests run without a real DB or LLM API.
Fixture data from data/*.json is used directly.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.services.parsing import (
    NebenkostenInfo,
    PlzZoneMapping,
    TariffEntry,
    TariffParseResult,
    ZoneInfo,
)
from app.services.parsing.tariff_pdf_parser import (
    TariffPdfParser,
    _calculate_confidence,
    _entries_from_structure,
    _extract_from_text_grid,
    _extract_metadata,
    _llm_response_to_result,
    _nebenkosten_from_block,
    _parse_date,
    _parse_eu_number,
    _parse_json_response,
    _parse_zone_label,
    _scan_text_for_metadata,
    _validate_entries,
    _zone_maps_from_structure,
    _zones_from_structure,
)
from app.services.parsing.tariff_xlsx_parser import TariffXlsxParser

# Path to shared fixtures
_FIXTURES_DIR = Path(__file__).parents[3] / "data"


# ── fixture loader ────────────────────────────────────────────────────────────


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text())


# ============================================================================
# Shared helper: _parse_eu_number
# ============================================================================


class TestParseEuNumber:
    def test_plain_integer(self):
        assert _parse_eu_number("52") == 52.0

    def test_eu_decimal_comma(self):
        assert _parse_eu_number("62,20") == pytest.approx(62.20)

    def test_eu_thousands_dot_decimal_comma(self):
        assert _parse_eu_number("1.234,56") == pytest.approx(1234.56)

    def test_us_thousands_comma_decimal_dot(self):
        assert _parse_eu_number("1,234.56") == pytest.approx(1234.56)

    def test_empty_returns_none(self):
        assert _parse_eu_number("") is None

    def test_non_numeric_returns_none(self):
        assert _parse_eu_number("Zone") is None


# ============================================================================
# Shared helper: _parse_zone_label
# ============================================================================


class TestParseZoneLabel:
    def test_integer_string(self):
        assert _parse_zone_label("3") == 3

    def test_roman_i(self):
        assert _parse_zone_label("I") == 1

    def test_roman_iv(self):
        assert _parse_zone_label("IV") == 4

    def test_roman_viii(self):
        assert _parse_zone_label("VIII") == 8

    def test_lowercase_roman(self):
        assert _parse_zone_label("vi") == 6

    def test_unknown_returns_1(self):
        assert _parse_zone_label("?") == 1


# ============================================================================
# Shared helper: _parse_date
# ============================================================================


class TestParseDate:
    def test_eu_format_dots(self):
        assert _parse_date("01.01.2023") == date(2023, 1, 1)

    def test_eu_format_slashes(self):
        assert _parse_date("31/12/2023") == date(2023, 12, 31)

    def test_iso_format(self):
        assert _parse_date("2023-06-15") == date(2023, 6, 15)

    def test_invalid_month_returns_none(self):
        assert _parse_date("01.13.2023") is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_invalid_day_returns_none(self):
        assert _parse_date("31.02.2023") is None


# ============================================================================
# Shared helper: _validate_entries
# ============================================================================


class TestValidateEntries:
    def _entry(self, zone=1, wmin=1, wmax=50, price=Decimal("19.10"), currency="EUR"):
        return TariffEntry(
            zone=zone,
            weight_min=Decimal(str(wmin)),
            weight_max=Decimal(str(wmax)),
            base_amount=price,
            currency=currency,
        )

    def test_valid_entries_no_issues(self):
        entries = [self._entry(), self._entry(zone=2, price=Decimal("22.00"))]
        assert _validate_entries(entries) == []

    def test_negative_zone_flagged(self):
        issues = _validate_entries([self._entry(zone=-1)])
        assert any("zone" in i.lower() for i in issues)

    def test_inverted_weight_range_flagged(self):
        issues = _validate_entries([self._entry(wmin=100, wmax=50)])
        assert any("weight" in i.lower() for i in issues)

    def test_zero_price_flagged(self):
        issues = _validate_entries([self._entry(price=Decimal("0"))])
        assert any("price" in i.lower() for i in issues)


# ============================================================================
# Shared helper: _calculate_confidence
# ============================================================================


class TestCalculateConfidence:
    def _entry(self, zone=1, wmin=1, wmax=50, price=Decimal("19.10")):
        return TariffEntry(
            zone=zone,
            weight_min=Decimal(str(wmin)),
            weight_max=Decimal(str(wmax)),
            base_amount=price,
            currency="EUR",
        )

    def test_no_entries_returns_zero(self):
        assert _calculate_confidence([], "template") == Decimal("0")

    def test_template_baseline_095(self):
        entries = [self._entry(zone=z) for z in range(1, 9)]
        c = _calculate_confidence(entries, "template")
        assert c == Decimal("0.95")

    def test_llm_baseline_085(self):
        entries = [self._entry(zone=z) for z in range(1, 9)]
        c = _calculate_confidence(entries, "llm")
        assert c == Decimal("0.85")

    def test_partial_coverage_reduces_confidence(self):
        # 4 good + 4 bad (price=0) entries → 50% coverage
        good = [self._entry(zone=z) for z in range(1, 5)]
        bad = [
            TariffEntry(
                zone=z,
                weight_min=Decimal("1"),
                weight_max=Decimal("50"),
                base_amount=Decimal("0"),
                currency="EUR",
            )
            for z in range(5, 9)
        ]
        c = _calculate_confidence(good + bad, "template")
        assert c == Decimal("0.95") * Decimal("0.5")


# ============================================================================
# _entries_from_structure — canonical JSON fixture
# ============================================================================


class TestEntriesFromStructure:
    def test_gebr_weiss_fixture(self):
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")
        tariff = fixture["tariff"]
        entries = _entries_from_structure(tariff, {"currency": "EUR"})

        # Fixture has 8 zones × N weight bands
        assert len(entries) > 0

        # Spot-check first band, zone 1
        zone1_band1 = next(
            e for e in entries if e.zone == 1 and e.weight_max == Decimal("50")
        )
        assert zone1_band1.base_amount == Decimal("19.10")
        assert zone1_band1.weight_min == Decimal("1")
        assert zone1_band1.currency == "EUR"

    def test_null_price_skipped(self):
        structure = {
            "matrix": [
                {
                    "weight_from": 1,
                    "weight_to": 50,
                    "prices": {"zone_1": 19.10, "zone_2": None},
                }
            ]
        }
        entries = _entries_from_structure(structure, {})
        assert len(entries) == 1
        assert entries[0].zone == 1

    def test_negative_price_skipped(self):
        structure = {
            "matrix": [
                {"weight_from": 1, "weight_to": 50, "prices": {"zone_1": -5.0}}
            ]
        }
        assert _entries_from_structure(structure, {}) == []


# ============================================================================
# _zones_from_structure
# ============================================================================


class TestZonesFromStructure:
    def test_gebr_weiss_zones(self):
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")
        zones = _zones_from_structure(fixture["tariff"])
        assert len(zones) == 8
        assert zones[0].zone_number == 1
        assert zones[0].label == "Zone 1"
        assert "75000" in (zones[0].plz_description or "")


# ============================================================================
# _zone_maps_from_structure
# ============================================================================


class TestZoneMapsFromStructure:
    def test_returns_plz_entries(self):
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")
        maps = _zone_maps_from_structure(fixture["tariff"])
        assert len(maps) > 0
        assert all(isinstance(m, PlzZoneMapping) for m in maps)
        assert all(m.country_code in ("DE", "CH", "AT") for m in maps)


# ============================================================================
# _nebenkosten_from_block
# ============================================================================


class TestNebenkostenFromBlock:
    def test_gebr_weiss_fixture(self):
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")
        nk = fixture["nebenkosten"]
        info = _nebenkosten_from_block(nk)
        assert info is not None
        # Fixture has 1 cbm = 150 kg, 1 ldm = 750 kg
        assert info.min_weight_per_cbm_kg == Decimal("150")
        assert info.min_weight_per_ldm_kg == Decimal("750")

    def test_empty_block_returns_none(self):
        assert _nebenkosten_from_block({}) is None

    def test_flat_layout_parsed(self):
        nk = {"diesel_floater_pct": 15.2, "maut_included": False}
        info = _nebenkosten_from_block(nk)
        assert info is not None
        assert info.diesel_floater_pct == Decimal("15.2")
        assert info.maut_included is False


# ============================================================================
# _scan_text_for_metadata
# ============================================================================


class TestScanTextForMetadata:
    def test_detects_valid_from_german(self):
        text = "Gültig ab 01.01.2023"
        meta = _scan_text_for_metadata(text)
        assert meta["valid_from"] == date(2023, 1, 1)

    def test_detects_valid_until_german(self):
        text = "Gültig bis 31.12.2023"
        meta = _scan_text_for_metadata(text)
        assert meta.get("valid_until") == date(2023, 12, 31)

    def test_detects_lane_domestic_at(self):
        text = "Tarif für Österreich Sendungen"
        meta = _scan_text_for_metadata(text)
        assert meta["lane_type"] == "domestic_at"

    def test_detects_lane_domestic_de(self):
        text = "Stückgutversand Deutschland Tarifliste"
        meta = _scan_text_for_metadata(text)
        assert meta["lane_type"] == "domestic_de"

    def test_carrier_name_extracted(self):
        text = "Gebrüder Weiss GmbH, Tarifblatt 2023"
        meta = _scan_text_for_metadata(text)
        assert "Gebrüder Weiss GmbH" in meta.get("carrier_name", "")


# ============================================================================
# _extract_from_text_grid
# ============================================================================


class TestExtractFromTextGrid:
    _SAMPLE_TEXT = (
        "Tarifblatt Inland\n"
        "Zone I  Zone II  Zone III\n"
        "bis 50 kg  19,10  19,62  22,22\n"
        "bis 100 kg  25,06  28,72  32,71\n"
    )

    def test_extracts_entries(self):
        mappings = {"zone_count": 3, "currency": "EUR"}
        entries = _extract_from_text_grid(self._SAMPLE_TEXT, mappings)
        assert len(entries) == 6  # 3 zones × 2 weight bands

    def test_first_band_weight_from_is_1(self):
        mappings = {"zone_count": 3}
        entries = _extract_from_text_grid(self._SAMPLE_TEXT, mappings)
        band1 = [e for e in entries if e.weight_max == Decimal("50")]
        assert all(e.weight_min == Decimal("1") for e in band1)

    def test_second_band_weight_from_continues(self):
        mappings = {"zone_count": 3}
        entries = _extract_from_text_grid(self._SAMPLE_TEXT, mappings)
        band2 = [e for e in entries if e.weight_max == Decimal("100")]
        assert all(e.weight_min == Decimal("51") for e in band2)

    def test_roman_zone_labels_mapped(self):
        mappings = {"zone_count": 3}
        entries = _extract_from_text_grid(self._SAMPLE_TEXT, mappings)
        zones = {e.zone for e in entries}
        assert zones == {1, 2, 3}

    def test_missing_zone_count_returns_empty(self):
        entries = _extract_from_text_grid(self._SAMPLE_TEXT, {})
        assert entries == []

    def test_eu_prices_parsed_correctly(self):
        mappings = {"zone_count": 3}
        entries = _extract_from_text_grid(self._SAMPLE_TEXT, mappings)
        zone1_band1 = next(e for e in entries if e.zone == 1 and e.weight_max == Decimal("50"))
        assert zone1_band1.base_amount == Decimal("19.10")


# ============================================================================
# _parse_json_response
# ============================================================================


class TestParseJsonResponse:
    def test_clean_json(self):
        data = _parse_json_response('{"meta": {}, "tariff": {}}')
        assert data == {"meta": {}, "tariff": {}}

    def test_json_embedded_in_text(self):
        text = 'Here is the result:\n{"meta": {"carrier_name": "Test"}}'
        data = _parse_json_response(text)
        assert data is not None
        assert data["meta"]["carrier_name"] == "Test"

    def test_invalid_returns_none(self):
        assert _parse_json_response("not json at all") is None


# ============================================================================
# _llm_response_to_result — full round-trip with fixture
# ============================================================================


class TestLlmResponseToResult:
    def test_gebr_weiss_fixture(self):
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")
        # The fixture is the canonical LLM output format
        # Reshape to match the LLM response schema
        data = {
            "meta": {
                "carrier_name": fixture["meta"]["carrier_name"],
                "valid_from": fixture["meta"]["valid_from"],
                "valid_until": fixture["meta"]["valid_until"],
                "lane_type": "domestic_de",
                "currency": "EUR",
            },
            "tariff": fixture["tariff"],
            "nebenkosten": fixture["nebenkosten"],
        }
        result = _llm_response_to_result(data)

        assert result.carrier_name == fixture["meta"]["carrier_name"]
        assert result.lane_type == "domestic_de"
        assert result.valid_from == date(2023, 1, 1)
        assert result.valid_until == date(2023, 12, 31)
        assert result.currency == "EUR"
        assert len(result.entries) > 0
        assert len(result.zones) == 8
        assert len(result.zone_maps) > 0
        assert result.nebenkosten is not None
        assert result.parsing_method == "llm"
        assert result.confidence > Decimal("0")
        assert result.issues == []


# ============================================================================
# TariffXlsxParser — pandas-based parsing
# ============================================================================


class TestTariffXlsxParser:
    """Tests for TariffXlsxParser using synthetic DataFrames."""

    def _make_matrix_df(self) -> pd.DataFrame:
        """Synthetic zone matrix matching the Gebr. Weiss fixture layout."""
        headers = ["Gewicht", "Zone 1", "Zone 2", "Zone 3", "Zone 4"]
        rows = [
            headers,
            ["bis 50 kg", "19.10", "19.62", "22.22", "23.84"],
            ["bis 100 kg", "25.06", "28.72", "32.71", "35.71"],
            ["bis 150 kg", "36.99", "38.88", "43.96", "47.62"],
        ]
        return pd.DataFrame(rows[1:], columns=rows[0])

    def _make_nebenkosten_df(self) -> pd.DataFrame:
        rows = [
            ["Dieselfloater", "15,20 %"],
            ["Maut", "enthalten"],
            ["Mindestgewicht Palette", "500 kg"],
            ["Mindestgewicht je LDM", "1.650 kg"],
        ]
        return pd.DataFrame(rows, columns=["Position", "Wert"])

    @pytest.mark.asyncio
    async def test_parse_zone_matrix(self):
        parser = TariffXlsxParser()
        df = self._make_matrix_df()
        result = await parser.parse([df], "tariff.xlsx")

        assert isinstance(result, TariffParseResult)
        assert len(result.entries) == 12  # 4 zones × 3 weight bands
        assert result.currency == "EUR"
        assert result.parsing_method == "xlsx"

    @pytest.mark.asyncio
    async def test_parse_zone_numbers_correct(self):
        parser = TariffXlsxParser()
        df = self._make_matrix_df()
        result = await parser.parse([df], "tariff.xlsx")

        zones = {e.zone for e in result.entries}
        assert zones == {1, 2, 3, 4}

    @pytest.mark.asyncio
    async def test_parse_weight_from_starts_at_1(self):
        parser = TariffXlsxParser()
        df = self._make_matrix_df()
        result = await parser.parse([df], "tariff.xlsx")

        first_band = [e for e in result.entries if e.weight_max == Decimal("50")]
        assert all(e.weight_min == Decimal("1") for e in first_band)

    @pytest.mark.asyncio
    async def test_parse_weight_from_continues(self):
        parser = TariffXlsxParser()
        df = self._make_matrix_df()
        result = await parser.parse([df], "tariff.xlsx")

        second_band = [e for e in result.entries if e.weight_max == Decimal("100")]
        assert all(e.weight_min == Decimal("51") for e in second_band)

    @pytest.mark.asyncio
    async def test_nebenkosten_extracted(self):
        parser = TariffXlsxParser()
        df_matrix = self._make_matrix_df()
        df_nk = self._make_nebenkosten_df()
        result = await parser.parse([df_matrix, df_nk], "tariff.xlsx")

        nk = result.nebenkosten
        assert nk is not None
        assert nk.diesel_floater_pct == Decimal("15.20")
        assert nk.maut_included is True
        assert nk.min_weight_pallet_kg == Decimal("500")

    @pytest.mark.asyncio
    async def test_empty_dataframes_raises(self):
        parser = TariffXlsxParser()
        with pytest.raises(ValueError, match="No DataFrames"):
            await parser.parse([], "tariff.xlsx")

    @pytest.mark.asyncio
    async def test_no_zone_matrix_returns_issue(self):
        parser = TariffXlsxParser()
        df = pd.DataFrame({"col1": ["no zones here", "just random data"]})
        result = await parser.parse([df], "tariff.xlsx")
        assert any("No tariff entries" in issue for issue in result.issues)

    @pytest.mark.asyncio
    async def test_confidence_above_zero_for_valid_data(self):
        parser = TariffXlsxParser()
        df = self._make_matrix_df()
        result = await parser.parse([df], "tariff.xlsx")
        assert result.confidence > Decimal("0")

    @pytest.mark.asyncio
    async def test_lane_type_austria_detected(self):
        parser = TariffXlsxParser()
        df = self._make_matrix_df()
        # Add an Austria indicator row
        austria_row = pd.DataFrame(
            [["Österreich Tarif", "", "", "", ""]],
            columns=df.columns,
        )
        df_with_at = pd.concat([austria_row, df], ignore_index=True)
        result = await parser.parse([df_with_at], "tariff_at.xlsx")
        assert result.lane_type == "domestic_at"


# ============================================================================
# TariffPdfParser — mocked LLM
# ============================================================================


class TestTariffPdfParser:
    """Tests for TariffPdfParser with mocked LLM and DB."""

    def _fixture_llm_response(self) -> dict:
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")
        return {
            "meta": {
                "carrier_name": fixture["meta"]["carrier_name"],
                "valid_from": fixture["meta"]["valid_from"],
                "valid_until": fixture["meta"]["valid_until"],
                "lane_type": "domestic_de",
                "currency": "EUR",
            },
            "tariff": fixture["tariff"],
            "nebenkosten": fixture["nebenkosten"],
        }

    @pytest.mark.asyncio
    async def test_llm_path_returns_result(self):
        fixture_resp = self._fixture_llm_response()

        mock_content = MagicMock()
        mock_content.text = json.dumps(fixture_resp)
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        parser = TariffPdfParser()
        with patch.object(
            parser._client.messages,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await parser.parse(
                text="dummy tariff text",
                filename="tariff.pdf",
            )

        assert isinstance(result, TariffParseResult)
        assert result.parsing_method == "llm"
        assert len(result.entries) > 0
        assert result.carrier_name == fixture_resp["meta"]["carrier_name"]
        assert result.confidence > Decimal("0")

    @pytest.mark.asyncio
    async def test_template_path_pre_parsed(self):
        fixture = _load_fixture("gebr-weiss-tariff-extraction.json")

        mock_template = MagicMock()
        mock_template.id = "template-uuid-001"
        mock_template.detection = {"filename_pattern": r"tariff", "carrier_id": "carrier-1"}
        mock_template.mappings = {
            "tariff_structure": fixture["tariff"],
            "currency": "EUR",
            "metadata": {
                "carrier_name": fixture["meta"]["carrier_name"],
                "lane_type": "domestic_de",
                "valid_from": fixture["meta"]["valid_from"],
            },
        }
        mock_template.tenant_id = None
        mock_template.usage_count = 10

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_template]
        mock_execute_result = MagicMock()
        mock_execute_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_execute_result)

        parser = TariffPdfParser()
        result = await parser.parse(
            text="dummy tariff text",
            filename="tariff_gebr_weiss.pdf",
            carrier_id="carrier-1",
            db=mock_db,
        )

        assert result.parsing_method == "template"
        assert len(result.entries) > 0

    @pytest.mark.asyncio
    async def test_template_fallback_to_llm_on_failure(self):
        """When template parsing fails, should fall back to LLM."""
        mock_template = MagicMock()
        mock_template.id = "template-uuid-bad"
        # Score: carrier(0.4) + filename(0.3) + usage(0.1) = 0.8 → selected
        mock_template.detection = {
            "carrier_id": "carrier-1",
            "filename_pattern": r"tariff",
        }
        mock_template.mappings = {}  # No strategy → will raise ValueError
        mock_template.tenant_id = None
        mock_template.usage_count = 10

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_template]
        mock_execute_result = MagicMock()
        mock_execute_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_execute_result)

        fixture_resp = self._fixture_llm_response()
        mock_content = MagicMock()
        mock_content.text = json.dumps(fixture_resp)
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        parser = TariffPdfParser()
        with patch.object(
            parser._client.messages,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await parser.parse(
                text="dummy text",
                filename="tariff.pdf",
                carrier_id="carrier-1",
                db=mock_db,
            )

        assert result.parsing_method == "llm"

    @pytest.mark.asyncio
    async def test_low_score_template_skipped(self):
        """Template with score < 0.7 should not be used."""
        mock_template = MagicMock()
        mock_template.id = "template-low-score"
        mock_template.detection = {}  # No carrier_id, no filename_pattern → score 0
        mock_template.mappings = {}
        mock_template.tenant_id = None
        mock_template.usage_count = 0

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_template]
        mock_execute_result = MagicMock()
        mock_execute_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_execute_result)

        fixture_resp = self._fixture_llm_response()
        mock_content = MagicMock()
        mock_content.text = json.dumps(fixture_resp)
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        parser = TariffPdfParser()
        with patch.object(
            parser._client.messages,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await parser.parse(
                text="dummy text",
                filename="unrelated.pdf",
                db=mock_db,
            )

        # Template was skipped → LLM was used
        assert result.parsing_method == "llm"

    @pytest.mark.asyncio
    async def test_text_grid_template_strategy(self):
        """Template with strategy='text_grid' extracts entries from raw text."""
        sample_text = (
            "Tarifblatt Inland\n"
            "Zone I  Zone II  Zone III\n"
            "bis 50 kg  19,10  19,62  22,22\n"
            "bis 100 kg  25,06  28,72  32,71\n"
        )
        mock_template = MagicMock()
        mock_template.id = "template-text-grid"
        mock_template.detection = {
            "carrier_id": "carrier-grid",
            "filename_pattern": r"tariff_grid",
        }
        mock_template.mappings = {
            "strategy": "text_grid",
            "zone_count": 3,
            "currency": "EUR",
            "metadata": {
                "carrier_name": "Test Spedition GmbH",
                "lane_type": "domestic_de",
                "valid_from": "01.01.2023",
            },
        }
        mock_template.tenant_id = None
        mock_template.usage_count = 10

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_template]
        mock_execute_result = MagicMock()
        mock_execute_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_execute_result)

        parser = TariffPdfParser()
        result = await parser.parse(
            text=sample_text,
            filename="tariff_grid.pdf",
            carrier_id="carrier-grid",
            db=mock_db,
        )

        assert result.parsing_method == "template"
        assert len(result.entries) == 6  # 3 zones × 2 weight bands
        assert result.carrier_name == "Test Spedition GmbH"
