"""Tests for csv_parser.py — port of csv-parser.service.spec.ts."""

from __future__ import annotations

import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.parsing.csv_parser import parse, parse_with_template

TENANT_ID = "tenant-123"
UPLOAD_ID = "upload-456"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_csv(tmp_path: Path, content: str) -> str:
    p = tmp_path / "test.csv"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# parse() — alias-based parsing
# ---------------------------------------------------------------------------


class TestParse:
    def test_parse_german_column_names(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            Datum,Spediteur,VonPLZ,NachPLZ,Gewicht,Kosten,Währung
            01.03.2024,DHL,10115,80331,15.5,45.20,EUR
            02.03.2024,UPS,20095,50667,8.3,32.10,EUR
            """,
        )
        shipments, confidence = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 2
        assert 0 < confidence <= 1

        s = shipments[0]
        assert s.tenant_id == TENANT_ID
        assert s.upload_id == UPLOAD_ID
        assert s.extraction_method == "csv_direct"
        assert s.confidence_score is not None and s.confidence_score > 0
        assert s.date == date(2024, 3, 1)
        assert s.origin_zip == "10115"
        assert s.dest_zip == "80331"
        assert s.weight_kg == Decimal("15.50")
        assert s.actual_total_amount == Decimal("45.20")
        assert s.currency == "EUR"
        assert s.source_data["carrier_name"] == "DHL"

    def test_parse_english_column_names(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            date,carrier,origin_zip,dest_zip,weight,cost,currency
            2024-03-01,FedEx,12345,54321,25.75,67.80,USD
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 1
        s = shipments[0]
        assert s.date == date(2024, 3, 1)
        assert s.origin_zip == "12345"
        assert s.dest_zip == "54321"
        assert s.weight_kg == Decimal("25.75")
        assert s.actual_total_amount == Decimal("67.80")
        assert s.currency == "USD"
        assert s.source_data["carrier_name"] == "FedEx"

    def test_preserve_leading_zeros_in_zip(self, tmp_path: Path) -> None:
        """Leading zeros in ZIP codes must be preserved (dtype=str)."""
        csv = write_csv(
            tmp_path,
            """\
            datum,vonplz,nachplz,kosten
            01.03.2024,01234,09876,10.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 1
        assert shipments[0].origin_zip == "01234"
        assert shipments[0].dest_zip == "09876"

    def test_weight_with_comma_decimal(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,gewicht,kosten
            01.03.2024,"12,5","34,90"
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 1
        assert shipments[0].weight_kg == Decimal("12.50")
        assert shipments[0].actual_total_amount == Decimal("34.90")

    def test_multiple_date_formats(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,kosten
            01.03.2024,10.00
            01/03/2024,20.00
            2024-03-01,30.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 3
        expected = date(2024, 3, 1)
        for s in shipments:
            assert s.date == expected

    def test_service_level_normalization(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,service,kosten
            01.03.2024,Express,10.00
            02.03.2024,24h Delivery,20.00
            03.03.2024,Economy Plus,30.00
            04.03.2024,Standard,40.00
            05.03.2024,Custom Service,50.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 5
        assert shipments[0].service_level == "EXPRESS"
        assert shipments[1].service_level == "EXPRESS"
        assert shipments[2].service_level == "ECONOMY"
        assert shipments[3].service_level == "STANDARD"
        assert shipments[4].service_level == "STANDARD"

    def test_cost_strings_with_currency_symbols(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,betrag
            01.03.2024,"€ 1.234,56"
            02.03.2024,"$ 987.65"
            03.03.2024,"45,30 EUR"
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 3
        assert shipments[0].actual_total_amount == Decimal("1234.56")
        assert shipments[1].actual_total_amount == Decimal("987.65")
        assert shipments[2].actual_total_amount == Decimal("45.30")

    def test_skips_rows_with_invalid_date(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,kosten
            01.03.2024,10.00
            invalid-date,20.00
            ,30.00
            02.03.2024,40.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 2
        assert shipments[0].actual_total_amount == Decimal("10.00")
        assert shipments[1].actual_total_amount == Decimal("40.00")

    def test_cost_breakdown_fields(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,grundpreis,dieselzuschlag,maut,kosten
            01.03.2024,100.00,18.50,5.25,123.75
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 1
        s = shipments[0]
        assert s.actual_base_amount == Decimal("100.00")
        assert s.actual_diesel_amount == Decimal("18.50")
        assert s.actual_toll_amount == Decimal("5.25")
        assert s.actual_total_amount == Decimal("123.75")

    def test_empty_csv_returns_zero_confidence(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,kosten
            """,
        )
        shipments, confidence = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 0
        assert confidence == 0.0

    def test_source_data_preserves_original_row(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            custom_field,datum,kosten,extra_info
            special_value,01.03.2024,10.00,additional_data
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 1
        sd = shipments[0].source_data
        assert sd["custom_field"] == "special_value"
        assert sd["datum"] == "01.03.2024"
        assert sd["kosten"] == "10.00"
        assert sd["extra_info"] == "additional_data"


class TestParseDateEdgeCases:
    def test_invalid_date_components_are_skipped(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,kosten
            32.01.2024,10.00
            01.13.2024,20.00
            01.01.1800,30.00
            01.01.2200,40.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 0

    def test_feb_31_is_invalid(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,kosten
            31.02.2024,10.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)
        assert len(shipments) == 0


class TestWeightNormalizationEdgeCases:
    def test_negative_and_invalid_weights_not_set(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            datum,gewicht,kosten
            01.03.2024,-5.0,10.00
            02.03.2024,invalid,20.00
            03.03.2024,abc,30.00
            04.03.2024,15.5,40.00
            """,
        )
        shipments, _ = parse(csv, TENANT_ID, UPLOAD_ID)

        assert len(shipments) == 4
        assert shipments[0].weight_kg is None
        assert shipments[1].weight_kg is None
        assert shipments[2].weight_kg is None
        assert shipments[3].weight_kg == Decimal("15.50")


# ---------------------------------------------------------------------------
# parse_with_template()
# ---------------------------------------------------------------------------


class TestParseWithTemplate:
    def test_template_with_string_mapping(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            Datum,Carrier,FromZip,ToZip,Weight,Total,CCY
            01.03.2024,DHL,10115,80331,15.5,45.20,EUR
            """,
        )
        mappings = {
            "date": "Datum",
            "carrier_name": "Carrier",
            "origin_zip": "FromZip",
            "dest_zip": "ToZip",
            "weight_kg": "Weight",
            "actual_total_amount": "Total",
            "currency": "CCY",
        }
        shipments, errors, confidence = parse_with_template(
            csv, TENANT_ID, UPLOAD_ID, mappings
        )

        assert len(shipments) == 1
        assert len(errors) == 0
        s = shipments[0]
        assert s.date == date(2024, 3, 1)
        assert s.origin_zip == "10115"
        assert s.dest_zip == "80331"
        assert s.weight_kg == Decimal("15.50")
        assert s.actual_total_amount == Decimal("45.20")
        assert s.currency == "EUR"
        assert s.extraction_method == "template"

    def test_template_with_keywords_fallback(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            Datum,Betrag
            02.03.2024,99.00
            """,
        )
        mappings = {
            "date": {"keywords": ["Datum", "date"]},
            "actual_total_amount": {"keywords": ["Betrag", "total"]},
        }
        shipments, errors, _ = parse_with_template(
            csv, TENANT_ID, UPLOAD_ID, mappings
        )

        assert len(shipments) == 1
        assert shipments[0].actual_total_amount == Decimal("99.00")

    def test_template_missing_date_produces_row_error(self, tmp_path: Path) -> None:
        csv = write_csv(
            tmp_path,
            """\
            Datum,Betrag
            ,99.00
            """,
        )
        mappings = {"date": "Datum", "actual_total_amount": "Betrag"}
        shipments, errors, confidence = parse_with_template(
            csv, TENANT_ID, UPLOAD_ID, mappings
        )

        assert len(shipments) == 0
        assert len(errors) == 1
        assert confidence == 0.0

    def test_template_confidence_nonzero_when_shipments_parsed(
        self, tmp_path: Path
    ) -> None:
        csv = write_csv(
            tmp_path,
            """\
            Datum,VonPLZ,NachPLZ,Gewicht,Kosten,CCY
            01.03.2024,10115,80331,10.0,50.00,EUR
            """,
        )
        mappings = {
            "date": "Datum",
            "origin_zip": "VonPLZ",
            "dest_zip": "NachPLZ",
            "weight_kg": "Gewicht",
            "actual_total_amount": "Kosten",
            "currency": "CCY",
        }
        shipments, errors, confidence = parse_with_template(
            csv, TENANT_ID, UPLOAD_ID, mappings
        )

        assert len(shipments) == 1
        assert confidence > 0
