"""Unit tests for TariffEngineService.

Tests: lane-type determination, zone fallback, tariff/rate lookups,
       chargeable-weight (LDM + pallet), diesel surcharge, toll heuristic,
       FX conversion, delta classification, benchmark persistence.

Port of backend_legacy/src/modules/tariff/tariff-engine.service.spec.ts
Issue: #47
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.services.tariff_engine_service import TariffEngineService


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
CARRIER_ID = UUID("00000000-0000-0000-0000-000000000002")
TARIFF_ID = UUID("00000000-0000-0000-0000-000000000003")
RATE_ID = UUID("00000000-0000-0000-0000-000000000004")
TEST_DATE = date(2023, 1, 15)


def _make_shipment(**kwargs: object) -> MagicMock:
    s = MagicMock()
    s.id = UUID("00000000-0000-0000-0000-000000000010")
    s.tenant_id = TENANT_ID
    s.carrier_id = CARRIER_ID
    s.date = TEST_DATE
    s.origin_country = "DE"
    s.dest_country = "DE"
    s.dest_zip = "80331"
    s.weight_kg = Decimal("450")
    s.length_m = None
    s.pallets = None
    s.currency = "EUR"
    s.actual_total_amount = None
    s.actual_toll_amount = None
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_tariff_table(
    tariff_id: UUID = TARIFF_ID,
    lane_type: str = "DE",
    currency: str = "EUR",
) -> MagicMock:
    t = MagicMock()
    t.id = tariff_id
    t.tenant_id = TENANT_ID
    t.carrier_id = CARRIER_ID
    t.name = f"{lane_type} Standard Tariff"
    t.lane_type = lane_type
    t.currency = currency
    t.valid_from = date(2023, 1, 1)
    t.valid_until = None
    return t


def _make_tariff_rate(
    zone: int = 3,
    weight_from: float = 400,
    weight_to: float = 500,
    rate_per_shipment: float | None = 294.3,
    rate_per_kg: float | None = None,
) -> MagicMock:
    r = MagicMock()
    r.id = RATE_ID
    r.tariff_table_id = TARIFF_ID
    r.zone = zone
    r.weight_from_kg = Decimal(str(weight_from))
    r.weight_to_kg = Decimal(str(weight_to))
    r.rate_per_shipment = Decimal(str(rate_per_shipment)) if rate_per_shipment is not None else None
    r.rate_per_kg = Decimal(str(rate_per_kg)) if rate_per_kg is not None else None
    return r


def _make_diesel_floater(pct: float = 18.5, basis: str = "base") -> MagicMock:
    d = MagicMock()
    d.floater_pct = Decimal(str(pct))
    d.basis = basis
    return d


def _db_returning(value: object) -> AsyncMock:
    """Return an AsyncMock whose .execute() scalar_one_or_none yields value."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db = AsyncMock()
    db.execute.return_value = result
    return db


def _db_sequence(*values: object) -> AsyncMock:
    """Cycle through values on successive db.execute() calls."""
    db = AsyncMock()
    results = []
    for v in values:
        r = MagicMock()
        r.scalar_one_or_none.return_value = v
        results.append(r)
    db.execute.side_effect = results
    return db


class TestTariffEngineService:
    """Test suite for TariffEngineService."""

    def setup_method(self) -> None:
        self.zone_svc = AsyncMock()
        self.fx_svc = AsyncMock()
        self.service = TariffEngineService(
            zone_calculator=self.zone_svc,
            fx_service=self.fx_svc,
        )

    # ============================================================================
    # determine_lane_type — pure function
    # ============================================================================

    def test_lane_type_de_domestic(self) -> None:
        assert TariffEngineService.determine_lane_type("DE", "DE") == "DE"

    def test_lane_type_de_at(self) -> None:
        assert TariffEngineService.determine_lane_type("DE", "AT") == "AT"
        assert TariffEngineService.determine_lane_type("AT", "DE") == "AT"

    def test_lane_type_de_ch(self) -> None:
        assert TariffEngineService.determine_lane_type("DE", "CH") == "CH"
        assert TariffEngineService.determine_lane_type("CH", "DE") == "CH"

    def test_lane_type_eu(self) -> None:
        assert TariffEngineService.determine_lane_type("DE", "FR") == "EU"
        assert TariffEngineService.determine_lane_type("FR", "IT") == "EU"

    def test_lane_type_export(self) -> None:
        assert TariffEngineService.determine_lane_type("DE", "US") == "EXPORT"

    # ============================================================================
    # _estimate_toll — pure function
    # ============================================================================

    def test_estimate_toll_light_shipment_returns_zero(self) -> None:
        assert TariffEngineService._estimate_toll(3, Decimal("450"), "DE") == Decimal("0")

    def test_estimate_toll_heavy_de_zone3(self) -> None:
        assert TariffEngineService._estimate_toll(3, Decimal("4000"), "DE") == Decimal("12")

    def test_estimate_toll_unknown_country_returns_zero(self) -> None:
        assert TariffEngineService._estimate_toll(3, Decimal("4000"), "JP") == Decimal("0")

    # ============================================================================
    # calculate_expected_cost — DE domestic base case
    # ============================================================================

    @pytest.mark.asyncio
    async def test_base_cost_de_domestic(self) -> None:
        shipment = _make_shipment()
        self.zone_svc.calculate_zone.return_value = 3

        db = _db_sequence(
            _make_tariff_table(),       # find_applicable_tariff
            None,                        # carrier lookup (no conversion_rules)
            _make_tariff_rate(),         # find_tariff_rate
            None,                        # diesel_floater → fallback
        )
        db.add = MagicMock()
        db.flush = AsyncMock()

        # Patch _get_diesel_floater to return the floater directly
        diesel = _make_diesel_floater()
        tariff = _make_tariff_table()
        rate = _make_tariff_rate()
        carrier_result = MagicMock()
        carrier_result.scalar_one_or_none.return_value = None  # no conversion_rules

        diesel_result = MagicMock()
        diesel_result.scalar_one_or_none.return_value = diesel

        tariff_result = MagicMock()
        tariff_result.scalar_one_or_none.return_value = tariff

        rate_result = MagicMock()
        rate_result.scalar_one_or_none.return_value = rate

        db2 = AsyncMock()
        db2.execute.side_effect = [tariff_result, carrier_result, rate_result, diesel_result]
        db2.add = MagicMock()
        db2.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db2, shipment)

        assert result.expected_base_amount == Decimal("294.3")
        assert result.expected_toll_amount == Decimal("0")
        assert result.expected_diesel_amount == Decimal("54.45")  # 294.3 * 0.185
        assert result.expected_total_amount == Decimal("348.75")
        assert len(result.cost_breakdown) == 3
        assert result.cost_breakdown[0].item == "base_rate"
        assert result.cost_breakdown[0].zone == 3
        assert result.cost_breakdown[0].weight == Decimal("450")
        assert result.cost_breakdown[0].description == "Zone 3 base rate (kg)"
        assert result.cost_breakdown[0].note == "Using actual weight: 450kg"
        assert result.calculation_metadata["lane_type"] == "DE"
        assert result.calculation_metadata["zone_calculated"] == 3
        assert result.calculation_metadata["calc_version"] == "1.4-complete-benchmark"

    # ============================================================================
    # calculate_expected_cost — lane type dispatch
    # ============================================================================

    @pytest.mark.asyncio
    async def test_lane_types_dispatched_correctly(self) -> None:
        """Service queries tariff table with the correct lane_type for each pair."""
        cases = [
            ("DE", "DE", "DE"),
            ("DE", "AT", "AT"),
            ("AT", "DE", "AT"),
            ("DE", "CH", "CH"),
            ("CH", "DE", "CH"),
            ("DE", "FR", "EU"),
            ("FR", "IT", "EU"),
            ("DE", "US", "EXPORT"),
        ]
        self.zone_svc.calculate_zone.return_value = 3

        for origin, dest, expected_lane in cases:
            shipment = _make_shipment(origin_country=origin, dest_country=dest)

            tariff = _make_tariff_table(lane_type=expected_lane)
            tariff_r = MagicMock()
            tariff_r.scalar_one_or_none.return_value = tariff

            carrier_r = MagicMock()
            carrier_r.scalar_one_or_none.return_value = None

            rate_r = MagicMock()
            rate_r.scalar_one_or_none.return_value = _make_tariff_rate()

            diesel_r = MagicMock()
            diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

            db = AsyncMock()
            db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
            db.add = MagicMock()
            db.flush = AsyncMock()

            result = await self.service.calculate_expected_cost(db, shipment)
            assert result.calculation_metadata["lane_type"] == expected_lane, (
                f"Expected lane_type={expected_lane} for {origin}→{dest}"
            )

    # ============================================================================
    # calculate_expected_cost — rate_per_kg
    # ============================================================================

    @pytest.mark.asyncio
    async def test_rate_per_kg(self) -> None:
        shipment = _make_shipment()  # 450kg
        self.zone_svc.calculate_zone.return_value = 3

        # 450 * 0.65 = 292.50
        rate = _make_tariff_rate(rate_per_shipment=None, rate_per_kg=0.65)

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = rate
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_base_amount == Decimal("292.50")
        assert result.cost_breakdown[0].rate == Decimal("0.65")

    # ============================================================================
    # calculate_expected_cost — currency conversion
    # ============================================================================

    @pytest.mark.asyncio
    async def test_currency_conversion_eur_to_chf(self) -> None:
        shipment = _make_shipment(currency="CHF")
        self.zone_svc.calculate_zone.return_value = 3
        self.fx_svc.get_rate.return_value = Decimal("0.985")

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table(currency="EUR")
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        self.fx_svc.get_rate.assert_any_call(db, "EUR", "CHF", TEST_DATE)
        assert result.expected_base_amount == Decimal("289.89")  # 294.30 * 0.985
        assert result.cost_breakdown[0].currency == "CHF"
        assert result.cost_breakdown[0].note == "Using actual weight: 450kg. Converted from EUR using rate 0.985"
        assert result.calculation_metadata["fx_rate_used"] == float(Decimal("0.985"))

    # ============================================================================
    # calculate_expected_cost — zone fallback
    # ============================================================================

    @pytest.mark.asyncio
    async def test_zone_fallback_de_lane_uses_zone_1(self) -> None:
        shipment = _make_shipment()
        self.zone_svc.calculate_zone.side_effect = Exception("Zone not found")

        rate = _make_tariff_rate(zone=1, weight_from=400, weight_to=500, rate_per_shipment=250.0)
        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = rate
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.calculation_metadata["zone_calculated"] == 1
        assert result.expected_base_amount == Decimal("250.0")

    # ============================================================================
    # calculate_expected_cost — no tariff / no rate
    # ============================================================================

    @pytest.mark.asyncio
    async def test_raises_404_when_no_tariff(self) -> None:
        shipment = _make_shipment()
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = None
        db = AsyncMock(); db.execute.return_value = tariff_r

        with pytest.raises(HTTPException) as exc:
            await self.service.calculate_expected_cost(db, shipment)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_no_rate(self) -> None:
        shipment = _make_shipment()
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = None

        db = AsyncMock(); db.execute.side_effect = [tariff_r, carrier_r, rate_r]

        with pytest.raises(HTTPException) as exc:
            await self.service.calculate_expected_cost(db, shipment)
        assert exc.value.status_code == 404

    # ============================================================================
    # calculate_expected_cost — FX failure graceful fallback
    # ============================================================================

    @pytest.mark.asyncio
    async def test_fx_conversion_failure_uses_original_amount(self) -> None:
        shipment = _make_shipment(currency="CHF")
        self.zone_svc.calculate_zone.return_value = 3
        self.fx_svc.get_rate.side_effect = Exception("FX rate not found")

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table(currency="EUR")
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_base_amount == Decimal("294.3")
        assert result.cost_breakdown[0].note == (
            "Using actual weight: 450kg. Conversion failed, using original EUR amount"
        )
        assert result.calculation_metadata["fx_rate_used"] is None

    # ============================================================================
    # calculate_expected_cost — LDM conversion
    # ============================================================================

    @pytest.mark.asyncio
    async def test_ldm_conversion_applied_when_higher(self) -> None:
        """LDM weight (2.5m × 1850) = 4625kg > actual 300kg → use LDM."""
        shipment = _make_shipment(weight_kg=Decimal("300"), length_m=Decimal("2.5"))
        self.zone_svc.calculate_zone.return_value = 3

        carrier_mock = MagicMock()
        carrier_mock.conversion_rules = {"ldm_conversion": {"ldm_to_kg": 1850}}

        rate = _make_tariff_rate(zone=3, weight_from=4000, weight_to=5000, rate_per_shipment=2500.0)

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = carrier_mock
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = rate
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_base_amount == Decimal("2500.0")
        assert result.cost_breakdown[0].description == "Zone 3 base rate (lm)"
        assert result.cost_breakdown[0].weight == Decimal("4625")
        assert result.cost_breakdown[0].note == "LDM weight: 2.5m × 1850kg/m = 4625kg"

    # ============================================================================
    # calculate_expected_cost — pallet minimum weight
    # ============================================================================

    @pytest.mark.asyncio
    async def test_pallet_min_weight_applied_when_higher(self) -> None:
        """3 pallets × 250kg = 750kg > actual 200kg → use pallet weight."""
        shipment = _make_shipment(weight_kg=Decimal("200"), pallets=Decimal("3"))
        self.zone_svc.calculate_zone.return_value = 3

        carrier_mock = MagicMock()
        carrier_mock.conversion_rules = {"min_pallet_weight": {"min_weight_per_pallet_kg": 250}}

        rate = _make_tariff_rate(zone=3, weight_from=650, weight_to=750, rate_per_shipment=400.0)

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = carrier_mock
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = rate
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_base_amount == Decimal("400.0")
        assert result.cost_breakdown[0].description == "Zone 3 base rate (pallet)"
        assert result.cost_breakdown[0].weight == Decimal("750")
        assert result.cost_breakdown[0].note == "Pallet weight: 3 × 250kg/pallet = 750kg"

    # ============================================================================
    # calculate_expected_cost — actual weight wins over conversion rules
    # ============================================================================

    @pytest.mark.asyncio
    async def test_actual_weight_wins_over_lower_conversion_weights(self) -> None:
        """800kg actual > LDM 555kg and pallet 500kg → keep actual."""
        shipment = _make_shipment(
            weight_kg=Decimal("800"),
            length_m=Decimal("0.3"),  # 0.3 × 1850 = 555kg
            pallets=Decimal("2"),     # 2 × 250 = 500kg
        )
        self.zone_svc.calculate_zone.return_value = 3

        carrier_mock = MagicMock()
        carrier_mock.conversion_rules = {
            "ldm_conversion": {"ldm_to_kg": 1850},
            "min_pallet_weight": {"min_weight_per_pallet_kg": 250},
        }

        rate = _make_tariff_rate(zone=3, weight_from=750, weight_to=850, rate_per_shipment=500.0)

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = carrier_mock
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = rate
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_base_amount == Decimal("500.0")
        assert result.cost_breakdown[0].description == "Zone 3 base rate (kg)"
        assert result.cost_breakdown[0].weight == Decimal("800")
        assert "LDM weight 555kg < actual weight, using actual" in result.cost_breakdown[0].note
        assert "Pallet weight 500kg < chargeable weight, using current" in result.cost_breakdown[0].note

    # ============================================================================
    # calculate_expected_cost — diesel surcharge
    # ============================================================================

    @pytest.mark.asyncio
    async def test_diesel_surcharge_on_base(self) -> None:
        shipment = _make_shipment()
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater(18.5, "base")

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_base_amount == Decimal("294.3")
        assert result.expected_toll_amount == Decimal("0")
        assert result.expected_diesel_amount == Decimal("54.45")
        assert result.expected_total_amount == Decimal("348.75")
        bd = result.cost_breakdown[2]
        assert bd.item == "diesel_surcharge"
        assert bd.base == Decimal("294.3")
        assert bd.pct == Decimal("18.5")
        assert bd.value == Decimal("54.45")

    @pytest.mark.asyncio
    async def test_diesel_fallback_when_no_row(self) -> None:
        shipment = _make_shipment()
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = None  # no floater

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_diesel_amount == Decimal("54.45")  # default 18.5%
        assert result.cost_breakdown[2].description == "Diesel surcharge (18.5% on base)"

    @pytest.mark.asyncio
    async def test_diesel_on_base_plus_toll(self) -> None:
        """Heavy shipment: diesel base = base + toll."""
        shipment = _make_shipment(weight_kg=Decimal("4000"))
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate(
            zone=3, weight_from=3500, weight_to=4500, rate_per_shipment=300.0
        )
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater(18.5, "base_plus_toll")

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        # Zone 3 DE toll for 4000kg: 12 EUR; diesel base = 300 + 12 = 312
        # diesel = round(312 * 0.185) = round(57.72) = 57.72
        assert result.expected_toll_amount == Decimal("12")
        assert result.expected_diesel_amount == Decimal("57.72")
        assert result.expected_total_amount == Decimal("369.72")
        assert result.cost_breakdown[2].base == Decimal("312")
        assert result.cost_breakdown[2].description == "Diesel surcharge (18.5% on base_plus_toll)"

    # ============================================================================
    # calculate_expected_cost — toll handling
    # ============================================================================

    @pytest.mark.asyncio
    async def test_invoice_toll_used_when_present(self) -> None:
        shipment = _make_shipment(actual_toll_amount=Decimal("15.5"))
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_toll_amount == Decimal("15.5")
        assert result.cost_breakdown[1].note == "from_invoice"
        assert result.cost_breakdown[1].description == "Toll charges (from_invoice)"

    @pytest.mark.asyncio
    async def test_toll_estimated_for_heavy_shipment(self) -> None:
        """4000kg > 3500kg threshold → zone 3 DE toll = 12 EUR."""
        shipment = _make_shipment(weight_kg=Decimal("4000"))
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate(
            zone=3, weight_from=3500, weight_to=4500, rate_per_shipment=600.0
        )
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_toll_amount == Decimal("12")
        assert result.cost_breakdown[1].note == "estimated_heuristic"

    # ============================================================================
    # calculate_expected_cost — full benchmark with delta classification
    # ============================================================================

    @pytest.mark.asyncio
    async def test_full_benchmark_im_markt(self) -> None:
        shipment = _make_shipment(actual_total_amount=Decimal("348.75"))
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.expected_total_amount == Decimal("348.75")
        assert result.actual_total_amount == Decimal("348.75")
        assert result.delta_amount == Decimal("0")
        assert result.delta_pct == Decimal("0")
        assert result.classification == "im_markt"
        assert result.report_amounts is None
        assert result.calculation_metadata["diesel_basis_used"] == "base"
        assert result.calculation_metadata["diesel_pct_used"] == 18.5
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_classification_drueber(self) -> None:
        """actual 400.00 vs expected 348.75 → +14.7% → drüber."""
        shipment = _make_shipment(actual_total_amount=Decimal("400.0"))
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.delta_amount == Decimal("51.25")
        assert result.delta_pct == Decimal("14.70")
        assert result.classification == "drüber"

    @pytest.mark.asyncio
    async def test_classification_unter(self) -> None:
        """actual 300.00 vs expected 348.75 → -13.98% → unter."""
        shipment = _make_shipment(actual_total_amount=Decimal("300.0"))
        self.zone_svc.calculate_zone.return_value = 3

        tariff_r = MagicMock(); tariff_r.scalar_one_or_none.return_value = _make_tariff_table()
        carrier_r = MagicMock(); carrier_r.scalar_one_or_none.return_value = None
        rate_r = MagicMock(); rate_r.scalar_one_or_none.return_value = _make_tariff_rate()
        diesel_r = MagicMock(); diesel_r.scalar_one_or_none.return_value = _make_diesel_floater()

        db = AsyncMock()
        db.execute.side_effect = [tariff_r, carrier_r, rate_r, diesel_r]
        db.add = MagicMock(); db.flush = AsyncMock()

        result = await self.service.calculate_expected_cost(db, shipment)

        assert result.delta_amount == Decimal("-48.75")
        assert result.delta_pct == Decimal("-13.98")
        assert result.classification == "unter"

    # ============================================================================
    # Singleton
    # ============================================================================

    def test_singleton_returns_same_instance(self) -> None:
        from app.services.tariff_engine_service import get_tariff_engine_service

        a = get_tariff_engine_service()
        b = get_tariff_engine_service()
        assert a is b
