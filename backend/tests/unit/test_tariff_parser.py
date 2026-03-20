"""Unit tests for TariffParserService.

Tests: LLM response parsing, zone/rate conversion, routing decisions,
       DB persistence calls, carrier-resolution branching.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.parsing.tariff_parser import (
    TariffParserService,
    TariffParseResult,
    TariffRateEntry,
    TariffZoneEntry,
    get_tariff_parser,
    _tariff_parser,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TENANT_ID = uuid4()
UPLOAD_ID = uuid4()
CARRIER_ID = uuid4()

_MODULE = "app.services.parsing.tariff_parser"

_GOOD_LLM_RESPONSE = {
    "carrier_name": "AS Stahl und Logistik GmbH & Co. KG",
    "customer_name": "Mecu",
    "valid_from": "2022-04-01",
    "currency": "EUR",
    "lane_type": "domestic_de",
    "zones": [
        {"plz_prefix": "35", "zone": 35},
        {"plz_prefix": "60", "zone": 60},
    ],
    "rates": [
        {
            "zone": 35,
            "weight_from_kg": 0,
            "weight_to_kg": 300,
            "rate_per_shipment": 56.13,
            "rate_per_kg": None,
        },
        {
            "zone": 35,
            "weight_from_kg": 1500,
            "weight_to_kg": 99999,
            "rate_per_shipment": None,
            "rate_per_kg": 0.11,
        },
        {
            "zone": 0,
            "weight_from_kg": 0,
            "weight_to_kg": 4000,
            "rate_per_shipment": 530.00,
            "rate_per_kg": None,
        },
    ],
    "confidence": 0.92,
    "issues": [],
}


def _make_carrier_resolution(carrier_id: UUID = CARRIER_ID) -> MagicMock:
    res = MagicMock()
    res.carrier_id = carrier_id
    res.method = "exact"
    return res


class TestTariffParserService:
    """Test suite for TariffParserService."""

    def setup_method(self) -> None:
        self.svc = TariffParserService()

    # ============================================================================
    # _parse_zones
    # ============================================================================

    def test_parse_zones_valid(self) -> None:
        zones = self.svc._parse_zones([
            {"plz_prefix": "35", "zone": 35},
            {"plz_prefix": "60", "zone": 60},
        ])
        assert len(zones) == 2
        assert zones[0] == TariffZoneEntry(plz_prefix="35", zone=35)
        assert zones[1] == TariffZoneEntry(plz_prefix="60", zone=60)

    def test_parse_zones_skips_malformed(self) -> None:
        zones = self.svc._parse_zones([
            {"plz_prefix": "35", "zone": 35},
            {"bad_key": "oops"},          # missing zone
            {"plz_prefix": "60", "zone": "not-an-int"},  # bad zone type
        ])
        # Only the first valid entry survives
        assert len(zones) == 1
        assert zones[0].plz_prefix == "35"

    def test_parse_zones_empty(self) -> None:
        assert self.svc._parse_zones([]) == []

    # ============================================================================
    # _parse_rates
    # ============================================================================

    def test_parse_rates_per_shipment(self) -> None:
        rates = self.svc._parse_rates([
            {"zone": 35, "weight_from_kg": 0, "weight_to_kg": 300,
             "rate_per_shipment": 56.13, "rate_per_kg": None},
        ])
        assert len(rates) == 1
        r = rates[0]
        assert r.zone == 35
        assert r.weight_from_kg == Decimal("0")
        assert r.weight_to_kg == Decimal("300")
        assert r.rate_per_shipment == Decimal("56.13")
        assert r.rate_per_kg is None

    def test_parse_rates_per_kg(self) -> None:
        rates = self.svc._parse_rates([
            {"zone": 35, "weight_from_kg": 1500, "weight_to_kg": 99999,
             "rate_per_shipment": None, "rate_per_kg": 0.11},
        ])
        r = rates[0]
        assert r.rate_per_shipment is None
        assert r.rate_per_kg == Decimal("0.11")

    def test_parse_rates_skips_malformed(self) -> None:
        rates = self.svc._parse_rates([
            {"zone": 35, "weight_from_kg": 0, "weight_to_kg": 300,
             "rate_per_shipment": 56.13, "rate_per_kg": None},
            {"zone": "not-int", "weight_from_kg": 0, "weight_to_kg": 300,
             "rate_per_shipment": 10.0, "rate_per_kg": None},
        ])
        assert len(rates) == 1

    # ============================================================================
    # _parse_date
    # ============================================================================

    def test_parse_date_valid_iso(self) -> None:
        assert self.svc._parse_date("2022-04-01") == date(2022, 4, 1)

    def test_parse_date_none(self) -> None:
        assert self.svc._parse_date(None) is None

    def test_parse_date_invalid_returns_none(self) -> None:
        assert self.svc._parse_date("not-a-date") is None

    # ============================================================================
    # _decide_action
    # ============================================================================

    def test_decide_action_auto_import(self) -> None:
        action = self.svc._decide_action(
            confidence=0.92, carrier_id=CARRIER_ID, rate_count=30, zone_count=18
        )
        assert action == "auto_import"

    def test_decide_action_hold_low_confidence(self) -> None:
        action = self.svc._decide_action(
            confidence=0.65, carrier_id=CARRIER_ID, rate_count=30, zone_count=18
        )
        assert action == "hold_for_review"

    def test_decide_action_needs_review_no_carrier(self) -> None:
        action = self.svc._decide_action(
            confidence=0.95, carrier_id=None, rate_count=30, zone_count=18
        )
        assert action == "needs_manual_review"

    def test_decide_action_needs_review_no_rates(self) -> None:
        action = self.svc._decide_action(
            confidence=0.95, carrier_id=CARRIER_ID, rate_count=0, zone_count=5
        )
        assert action == "needs_manual_review"

    def test_decide_action_needs_review_below_hold_threshold(self) -> None:
        action = self.svc._decide_action(
            confidence=0.30, carrier_id=CARRIER_ID, rate_count=5, zone_count=5
        )
        assert action == "needs_manual_review"

    # ============================================================================
    # parse() — full pipeline (mocked LLM + DB + carrier)
    # ============================================================================

    @pytest.mark.asyncio
    async def test_parse_happy_path_auto_import(self) -> None:
        db = AsyncMock()
        db.flush = AsyncMock()

        doc_result = MagicMock()
        doc_result.text = "some tariff text"
        doc_result.mode = "text"
        doc_result.page_count = 2

        with (
            patch.object(self.svc._doc_service, "process", new_callable=AsyncMock, return_value=doc_result),
            patch.object(self.svc, "_extract_via_llm", new_callable=AsyncMock, return_value=_GOOD_LLM_RESPONSE),
            patch.object(
                self.svc._carrier_service,
                "resolve_carrier_id_with_fallback",
                new_callable=AsyncMock,
                return_value=_make_carrier_resolution(),
            ),
        ):
            result = await self.svc.parse(
                b"fake-pdf-bytes",
                filename="AS 04.2022 Dirk Beese.pdf",
                tenant_id=TENANT_ID,
                upload_id=UPLOAD_ID,
                db=db,
            )

        assert result.review_action == "auto_import"
        assert result.confidence == 0.92
        assert result.carrier_id == CARRIER_ID
        assert len(result.zones) == 2
        assert len(result.rates) == 3
        assert result.valid_from == date(2022, 4, 1)
        assert result.currency == "EUR"
        assert result.tariff_table_id is not None

    @pytest.mark.asyncio
    async def test_parse_unresolved_carrier_needs_manual_review(self) -> None:
        db = AsyncMock()
        db.flush = AsyncMock()

        doc_result = MagicMock()
        doc_result.text = "tariff text"
        doc_result.mode = "text"
        doc_result.page_count = 1

        with (
            patch.object(self.svc._doc_service, "process", new_callable=AsyncMock, return_value=doc_result),
            patch.object(self.svc, "_extract_via_llm", new_callable=AsyncMock, return_value=_GOOD_LLM_RESPONSE),
            patch.object(
                self.svc._carrier_service,
                "resolve_carrier_id_with_fallback",
                new_callable=AsyncMock,
                return_value=None,  # carrier not found
            ),
        ):
            result = await self.svc.parse(
                b"fake-pdf-bytes",
                filename="unknown_carrier.pdf",
                tenant_id=TENANT_ID,
                db=db,
            )

        assert result.review_action == "needs_manual_review"
        assert result.carrier_id is None
        assert result.tariff_table_id is None  # not persisted without carrier
        assert any("carrier" in issue.lower() for issue in result.issues)

    @pytest.mark.asyncio
    async def test_parse_llm_failure_returns_empty_result(self) -> None:
        db = AsyncMock()

        doc_result = MagicMock()
        doc_result.text = "text"
        doc_result.mode = "text"
        doc_result.page_count = 1

        with (
            patch.object(self.svc._doc_service, "process", new_callable=AsyncMock, return_value=doc_result),
            patch.object(
                self.svc,
                "_extract_via_llm",
                new_callable=AsyncMock,
                return_value={"confidence": 0.0, "issues": ["LLM extraction failed: timeout"]},
            ),
            patch.object(
                self.svc._carrier_service,
                "resolve_carrier_id_with_fallback",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await self.svc.parse(
                b"bytes",
                filename="bad.pdf",
                tenant_id=TENANT_ID,
                db=db,
            )

        assert result.review_action == "needs_manual_review"
        assert result.tariff_table_id is None

    # ============================================================================
    # to_dict
    # ============================================================================

    def test_to_dict_serialises_correctly(self) -> None:
        result = TariffParseResult(
            carrier_name="Test Carrier",
            carrier_id=CARRIER_ID,
            customer_name="Mecu",
            valid_from=date(2022, 4, 1),
            currency="EUR",
            lane_type="domestic_de",
            zones=[TariffZoneEntry(plz_prefix="35", zone=35)],
            rates=[
                TariffRateEntry(
                    zone=35,
                    weight_from_kg=Decimal("0"),
                    weight_to_kg=Decimal("300"),
                    rate_per_shipment=Decimal("56.13"),
                    rate_per_kg=None,
                )
            ],
            tariff_table_id=uuid4(),
            confidence=0.92,
            parsing_method="llm",
            review_action="auto_import",
        )
        d = result.to_dict()
        assert d["carrier_name"] == "Test Carrier"
        assert d["zone_count"] == 1
        assert d["rate_count"] == 1
        assert d["valid_from"] == "2022-04-01"
        assert d["review_action"] == "auto_import"

    # ============================================================================
    # Singleton
    # ============================================================================

    def test_get_tariff_parser_returns_singleton(self) -> None:
        import app.services.parsing.tariff_parser as mod
        mod._tariff_parser = None
        p1 = get_tariff_parser()
        p2 = get_tariff_parser()
        assert p1 is p2
