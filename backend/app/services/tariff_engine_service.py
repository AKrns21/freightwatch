"""Tariff Engine Service — core freight-cost calculation logic.

Port of backend_legacy/src/modules/tariff/tariff-engine.service.ts
Issue: #47

Key features:
- determine_lane_type(): pure function (DE / AT / CH / EU / EXPORT)
- _calculate_zone(): delegates to ZoneCalculatorService, DE→1 / INT→3 fallback
- _find_applicable_tariff(): SQLAlchemy async, lane + date + carrier
- _calculate_chargeable_weight(): MAX(actual, LDM, pallet) via carrier.conversion_rules JSONB
- _find_tariff_rate(): zone × weight band lookup
- _calculate_base_amount(): rate_per_shipment OR rate_per_kg × weight
- _convert_currency(): delegates to FxService, graceful fallback on failure
- _get_diesel_floater(): date-range query, 18.5% fallback
- _estimate_toll(): pure lookup table (country × zone)
- calculate_expected_cost(): main entry point → BenchmarkResult
- _create_shipment_benchmark(): INSERT into shipment_benchmark
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import (
    Carrier,
    DieselFloater,
    DieselPriceBracket,
    Shipment,
    ShipmentBenchmark,
    TariffRate,
    TariffTable,
)
from app.services.destatis_service import get_destatis_service
from app.services.fx_service import FxService
from app.services.zone_calculator_service import ZoneCalculatorService
from app.utils.round import round_monetary

logger = structlog.get_logger(__name__)

# Heuristic toll threshold (vehicle-class proxy for MVP)
_TOLL_WEIGHT_THRESHOLD_KG = Decimal("3500")

_TOLL_BY_COUNTRY: dict[str, dict[int, Decimal]] = {
    "DE": {1: Decimal("5"), 2: Decimal("8"), 3: Decimal("12"), 4: Decimal("15"), 5: Decimal("18"), 6: Decimal("15")},
    "AT": {1: Decimal("6"), 2: Decimal("10"), 3: Decimal("14"), 4: Decimal("18"), 5: Decimal("22"), 6: Decimal("18")},
    "CH": {1: Decimal("8"), 2: Decimal("12"), 3: Decimal("16"), 4: Decimal("20"), 5: Decimal("24"), 6: Decimal("20")},
    "FR": {1: Decimal("7"), 2: Decimal("11"), 3: Decimal("15"), 4: Decimal("19"), 5: Decimal("23"), 6: Decimal("19")},
}

_EU_COUNTRIES = {"DE", "AT", "CH", "FR", "IT", "NL", "BE", "PL"}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CostBreakdownItem:
    item: str
    amount: Decimal
    currency: str
    description: str | None = None
    zone: int | None = None
    weight: Decimal | None = None
    rate: Decimal | None = None
    base: Decimal | None = None
    pct: Decimal | None = None
    value: Decimal | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"item": self.item, "amount": float(self.amount), "currency": self.currency}
        if self.description is not None:
            d["description"] = self.description
        if self.zone is not None:
            d["zone"] = self.zone
        if self.weight is not None:
            d["weight"] = float(self.weight)
        if self.rate is not None:
            d["rate"] = float(self.rate)
        if self.base is not None:
            d["base"] = float(self.base)
        if self.pct is not None:
            d["pct"] = float(self.pct)
        if self.value is not None:
            d["value"] = float(self.value)
        if self.note is not None:
            d["note"] = self.note
        return d


@dataclass
class BenchmarkResult:
    expected_base_amount: Decimal
    expected_total_amount: Decimal
    cost_breakdown: list[CostBreakdownItem]
    calculation_metadata: dict[str, Any]
    expected_diesel_amount: Decimal | None = None
    expected_toll_amount: Decimal | None = None
    actual_total_amount: Decimal | None = None
    delta_amount: Decimal | None = None
    delta_pct: Decimal | None = None
    classification: str | None = None
    report_amounts: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_base_amount": float(self.expected_base_amount),
            "expected_diesel_amount": float(self.expected_diesel_amount) if self.expected_diesel_amount is not None else None,
            "expected_toll_amount": float(self.expected_toll_amount) if self.expected_toll_amount is not None else None,
            "expected_total_amount": float(self.expected_total_amount),
            "actual_total_amount": float(self.actual_total_amount) if self.actual_total_amount is not None else None,
            "delta_amount": float(self.delta_amount) if self.delta_amount is not None else None,
            "delta_pct": float(self.delta_pct) if self.delta_pct is not None else None,
            "classification": self.classification,
            "cost_breakdown": [item.to_dict() for item in self.cost_breakdown],
            "report_amounts": self.report_amounts,
            "calculation_metadata": self.calculation_metadata,
        }


@dataclass
class _ChargeableWeightResult:
    value: Decimal
    basis: str
    note: str


@dataclass
class _ConvertedAmount:
    amount: Decimal
    fx_rate: Decimal | None = None
    fx_note: str | None = None


@dataclass
class _DieselFloaterResult:
    pct: Decimal
    basis: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TariffEngineService:
    """Core freight-cost calculation engine.

    Orchestrates zone lookup, tariff matching, chargeable-weight calculation,
    FX conversion, diesel surcharge, and toll estimation into a BenchmarkResult.

    Example usage:
        svc = TariffEngineService(zone_svc, fx_svc)
        result = await svc.calculate_expected_cost(db, shipment)
    """

    def __init__(
        self,
        zone_calculator: ZoneCalculatorService | None = None,
        fx_service: FxService | None = None,
    ) -> None:
        self.zone_calculator = zone_calculator or ZoneCalculatorService()
        self.fx_service = fx_service or FxService()
        self.logger = structlog.get_logger(__name__)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def calculate_expected_cost(
        self,
        db: AsyncSession,
        shipment: Shipment,
    ) -> BenchmarkResult:
        """Calculate expected freight cost for a shipment and persist a benchmark.

        Args:
            db: Async DB session (tenant context already set via RLS dependency).
            shipment: ORM Shipment instance.

        Returns:
            BenchmarkResult with expected/actual amounts, delta, classification.

        Raises:
            HTTPException(404): No applicable tariff or tariff rate found.
        """
        self.logger.debug(
            "tariff_engine_started",
            shipment_id=str(shipment.id),
            weight_kg=str(shipment.weight_kg),
            origin=shipment.origin_country,
            dest=shipment.dest_country,
        )

        lane_type = self.determine_lane_type(
            shipment.origin_country or "DE",
            shipment.dest_country or "DE",
        )

        zone = await self._calculate_zone(db, shipment, lane_type)

        applicable_tariff = await self._find_applicable_tariff(
            db,
            tenant_id=shipment.tenant_id,
            carrier_id=shipment.carrier_id,
            lane_type=lane_type,
            shipment_date=shipment.date,
        )

        chargeable = await self._calculate_chargeable_weight(db, shipment)

        tariff_rate = await self._find_tariff_rate(
            db,
            tariff_table_id=applicable_tariff.id,
            zone=zone,
            weight=chargeable.value,
        )

        base_amount = self._calculate_base_amount(tariff_rate, chargeable.value)

        converted = await self._convert_currency(
            db,
            amount=base_amount,
            from_currency=applicable_tariff.currency,
            to_currency=shipment.currency or "EUR",
            shipment_date=shipment.date,
        )

        # Toll: use invoice amount if present, else estimate
        actual_toll = shipment.actual_toll_amount
        if actual_toll and actual_toll > 0:
            toll_amount = round_monetary(actual_toll)
            toll_note = "from_invoice"
        else:
            estimated_toll = self._estimate_toll(
                zone=zone,
                weight_kg=chargeable.value,
                country=shipment.dest_country or "DE",
            )
            # Estimated toll: convert from tariff currency if needed
            converted_toll = await self._convert_currency(
                db,
                amount=estimated_toll,
                from_currency=applicable_tariff.currency,
                to_currency=shipment.currency or "EUR",
                shipment_date=shipment.date,
            )
            toll_amount = round_monetary(converted_toll.amount)
            toll_note = "estimated_heuristic"

        # Diesel floater
        diesel_floater = await self._get_diesel_floater(
            db,
            tenant_id=shipment.tenant_id,
            carrier_id=shipment.carrier_id,
            shipment_date=shipment.date,
        )
        if diesel_floater is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No diesel floater data for carrier {shipment.carrier_id} "
                    f"on {shipment.date} — upload a price bracket table or add a "
                    f"manual rate before benchmarking this shipment"
                ),
            )

        diesel_base: Decimal
        if diesel_floater.basis == "base_plus_toll":
            diesel_base = round_monetary(converted.amount + toll_amount)
        elif diesel_floater.basis == "total":
            diesel_base = round_monetary(converted.amount + toll_amount)
        else:  # "base" (default)
            diesel_base = converted.amount

        diesel_amount = round_monetary(diesel_base * (diesel_floater.pct / Decimal("100")))
        total_amount = round_monetary(converted.amount + toll_amount + diesel_amount)

        # Build cost breakdown
        base_note = chargeable.note
        if converted.fx_note:
            base_note = f"{base_note}. {converted.fx_note}"

        shipment_currency = shipment.currency or "EUR"
        cost_breakdown = [
            CostBreakdownItem(
                item="base_rate",
                description=f"Zone {zone} base rate ({chargeable.basis})",
                zone=zone,
                weight=chargeable.value,
                rate=tariff_rate.rate_per_shipment if tariff_rate.rate_per_shipment else tariff_rate.rate_per_kg,
                amount=converted.amount,
                currency=shipment_currency,
                note=base_note,
            ),
            CostBreakdownItem(
                item="toll",
                description=f"Toll charges ({toll_note})",
                value=toll_amount,
                amount=toll_amount,
                currency=shipment_currency,
                note=toll_note,
            ),
            CostBreakdownItem(
                item="diesel_surcharge",
                description=f"Diesel surcharge ({diesel_floater.pct}% on {diesel_floater.basis})",
                base=diesel_base,
                pct=diesel_floater.pct,
                value=diesel_amount,
                amount=diesel_amount,
                currency=shipment_currency,
            ),
        ]

        # Delta and classification
        actual_total = round_monetary(shipment.actual_total_amount or Decimal("0"))
        delta_amount = round_monetary(actual_total - total_amount)
        delta_pct = (
            round_monetary((delta_amount / total_amount) * Decimal("100"))
            if total_amount > 0
            else Decimal("0")
        )

        if delta_pct < Decimal("-5"):
            classification = "unter"
        elif delta_pct > Decimal("5"):
            classification = "drüber"
        else:
            classification = "im_markt"

        # Reporting currency conversion (tenant default = EUR for MVP)
        tenant_currency = "EUR"
        report_amounts: dict[str, Any] | None = None
        report_fx_rate: Decimal | None = None

        if shipment_currency != tenant_currency:
            try:
                report_fx_rate = await self.fx_service.get_rate(
                    db, shipment_currency, tenant_currency, shipment.date
                )
                report_amounts = {
                    "expected_base_amount": float(round_monetary(converted.amount * report_fx_rate)),
                    "expected_toll_amount": float(round_monetary(toll_amount * report_fx_rate)),
                    "expected_diesel_amount": float(round_monetary(diesel_amount * report_fx_rate)),
                    "expected_total_amount": float(round_monetary(total_amount * report_fx_rate)),
                    "actual_total_amount": float(round_monetary(actual_total * report_fx_rate)),
                    "delta_amount": float(round_monetary(delta_amount * report_fx_rate)),
                    "currency": tenant_currency,
                }
            except Exception as exc:
                self.logger.warning(
                    "report_currency_conversion_failed",
                    from_ccy=shipment_currency,
                    to_ccy=tenant_currency,
                    error=str(exc),
                )

        result = BenchmarkResult(
            expected_base_amount=round_monetary(converted.amount),
            expected_toll_amount=round_monetary(toll_amount),
            expected_diesel_amount=diesel_amount,
            expected_total_amount=total_amount,
            actual_total_amount=actual_total,
            delta_amount=delta_amount,
            delta_pct=delta_pct,
            classification=classification,
            cost_breakdown=cost_breakdown,
            report_amounts=report_amounts,
            calculation_metadata={
                "tariff_table_id": str(applicable_tariff.id),
                "lane_type": lane_type,
                "zone_calculated": zone,
                "fx_rate_used": float(converted.fx_rate) if converted.fx_rate is not None else None,
                "fx_rate_date": shipment.date if converted.fx_rate is not None else None,
                "diesel_basis_used": diesel_floater.basis,
                "diesel_pct_used": float(diesel_floater.pct),
                "calc_version": "1.4-complete-benchmark",
            },
        )

        if shipment.id:
            await self._create_shipment_benchmark(
                db, shipment, result, report_fx_rate, tenant_currency
            )

        self.logger.debug(
            "tariff_engine_completed",
            shipment_id=str(shipment.id),
            expected_total=str(result.expected_total_amount),
            delta_pct=str(result.delta_pct),
            classification=result.classification,
        )

        return result

    # -----------------------------------------------------------------------
    # Pure functions (no DB)
    # -----------------------------------------------------------------------

    @staticmethod
    def determine_lane_type(origin_country: str, dest_country: str) -> str:
        """Map origin/dest ISO codes to a lane type string.

        Returns: 'DE' | 'AT' | 'CH' | 'EU' | 'EXPORT'
        """
        origin = origin_country.strip().upper()
        dest = dest_country.strip().upper()

        if origin == "DE" and dest == "DE":
            return "DE"
        if {origin, dest} == {"DE", "AT"}:
            return "AT"
        if {origin, dest} == {"DE", "CH"}:
            return "CH"
        if origin in _EU_COUNTRIES and dest in _EU_COUNTRIES:
            return "EU"
        return "EXPORT"

    @staticmethod
    def _estimate_toll(zone: int, weight_kg: Decimal, country: str) -> Decimal:
        """Heuristic toll estimate: zero below 3.5t, lookup table above."""
        if weight_kg < _TOLL_WEIGHT_THRESHOLD_KG:
            return Decimal("0")
        country_table = _TOLL_BY_COUNTRY.get(country.upper(), {})
        return country_table.get(zone, Decimal("0"))

    @staticmethod
    def _calculate_base_amount(tariff_rate: TariffRate, weight: Decimal) -> Decimal:
        """Return base amount: flat per-shipment rate OR rate_per_kg × weight."""
        if tariff_rate.rate_per_shipment is not None:
            return Decimal(str(tariff_rate.rate_per_shipment))
        if tariff_rate.rate_per_kg is not None:
            return round_monetary(Decimal(str(tariff_rate.rate_per_kg)) * weight)
        raise HTTPException(
            status_code=500,
            detail=f"Tariff rate {tariff_rate.id} has neither rate_per_shipment nor rate_per_kg",
        )

    # -----------------------------------------------------------------------
    # DB-dependent helpers
    # -----------------------------------------------------------------------

    async def _calculate_zone(
        self,
        db: AsyncSession,
        shipment: Shipment,
        lane_type: str,
    ) -> int:
        try:
            return await self.zone_calculator.calculate_zone(
                db,
                tenant_id=shipment.tenant_id,
                carrier_id=shipment.carrier_id or UUID(int=0),
                country=shipment.dest_country or "DE",
                dest_zip=shipment.dest_zip or "",
                lookup_date=shipment.date,
            )
        except Exception as exc:
            fallback_zone = 1 if lane_type == "DE" else 3
            self.logger.warning(
                "zone_fallback_used",
                shipment_id=str(shipment.id),
                lane_type=lane_type,
                dest_zip=shipment.dest_zip,
                fallback_zone=fallback_zone,
                error=str(exc),
            )
            return fallback_zone

    async def _find_applicable_tariff(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        carrier_id: UUID | None,
        lane_type: str,
        shipment_date: date,
    ) -> TariffTable:
        stmt = (
            select(TariffTable)
            .where(
                TariffTable.tenant_id == tenant_id,
                TariffTable.carrier_id == carrier_id,
                TariffTable.lane_type == lane_type,
                TariffTable.valid_from <= shipment_date,
                or_(
                    TariffTable.valid_until.is_(None),
                    TariffTable.valid_until >= shipment_date,
                ),
            )
            .order_by(TariffTable.valid_from.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No applicable tariff found for tenant {tenant_id}, "
                    f"carrier {carrier_id}, lane {lane_type} on {shipment_date}"
                ),
            )
        return row

    async def _find_tariff_rate(
        self,
        db: AsyncSession,
        tariff_table_id: UUID,
        zone: int,
        weight: Decimal,
    ) -> TariffRate:
        stmt = (
            select(TariffRate)
            .where(
                TariffRate.tariff_table_id == tariff_table_id,
                TariffRate.zone == zone,
                TariffRate.weight_from_kg <= weight,
                TariffRate.weight_to_kg >= weight,
            )
            .order_by(TariffRate.weight_from_kg.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No tariff rate found for zone {zone}, "
                    f"weight {weight}kg in tariff table {tariff_table_id}"
                ),
            )
        return row

    async def _calculate_chargeable_weight(
        self,
        db: AsyncSession,
        shipment: Shipment,
    ) -> _ChargeableWeightResult:
        actual_kg = Decimal(str(shipment.weight_kg or 0))

        if actual_kg == 0:
            return _ChargeableWeightResult(value=Decimal("0"), basis="kg", note="No weight provided")

        max_weight = actual_kg
        basis = "kg"
        notes: list[str] = []

        try:
            conversion_rules: dict[str, Any] = {}
            if shipment.carrier_id:
                carrier_stmt = select(Carrier).where(Carrier.id == shipment.carrier_id).limit(1)
                carrier = (await db.execute(carrier_stmt)).scalar_one_or_none()
                if carrier and carrier.conversion_rules:
                    conversion_rules = carrier.conversion_rules

            # LDM conversion
            ldm_rule = conversion_rules.get("ldm_conversion")
            if ldm_rule and shipment.length_m and Decimal(str(shipment.length_m)) > 0:
                ldm_to_kg = ldm_rule.get("ldm_to_kg")
                if isinstance(ldm_to_kg, (int, float)):
                    length = Decimal(str(shipment.length_m))
                    ldm_weight = length * Decimal(str(ldm_to_kg))
                    if ldm_weight > max_weight:
                        max_weight = ldm_weight
                        basis = "lm"
                        notes.append(
                            f"LDM weight: {length}m × {int(ldm_to_kg)}kg/m = {int(ldm_weight)}kg"
                        )
                    else:
                        notes.append(f"LDM weight {int(ldm_weight)}kg < actual weight, using actual")

            # Minimum pallet weight
            pallet_rule = conversion_rules.get("min_pallet_weight")
            if pallet_rule and shipment.pallets and Decimal(str(shipment.pallets)) > 0:
                min_per_pallet = pallet_rule.get("min_kg_per_pallet") or pallet_rule.get("min_weight_per_pallet_kg")
                if isinstance(min_per_pallet, (int, float)):
                    pallets = Decimal(str(shipment.pallets))
                    pallet_weight = pallets * Decimal(str(min_per_pallet))
                    if pallet_weight > max_weight:
                        max_weight = pallet_weight
                        basis = "pallet"
                        notes.append(
                            f"Pallet weight: {int(pallets)} × {int(min_per_pallet)}kg/pallet = {int(pallet_weight)}kg"
                        )
                    else:
                        notes.append(
                            f"Pallet weight {int(pallet_weight)}kg < chargeable weight, using current"
                        )

            note = "; ".join(notes) if notes else f"Using actual weight: {int(actual_kg)}kg"
            return _ChargeableWeightResult(
                value=round_monetary(max_weight),
                basis=basis,
                note=note,
            )

        except Exception as exc:
            self.logger.error("chargeable_weight_error", error=str(exc))
            return _ChargeableWeightResult(
                value=round_monetary(actual_kg),
                basis="kg",
                note=f"Error calculating chargeable weight, using actual: {actual_kg}kg",
            )

    async def _convert_currency(
        self,
        db: AsyncSession,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        shipment_date: date,
    ) -> _ConvertedAmount:
        if from_currency == to_currency:
            return _ConvertedAmount(amount=amount)
        try:
            fx_rate = await self.fx_service.get_rate(db, from_currency, to_currency, shipment_date)
            return _ConvertedAmount(
                amount=round_monetary(amount * fx_rate),
                fx_rate=fx_rate,
                fx_note=f"Converted from {from_currency} using rate {fx_rate}",
            )
        except Exception as exc:
            self.logger.warning(
                "fx_conversion_failed",
                from_currency=from_currency,
                to_currency=to_currency,
                error=str(exc),
            )
            return _ConvertedAmount(
                amount=amount,
                fx_note=f"Conversion failed, using original {from_currency} amount",
            )

    async def _get_diesel_floater(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        carrier_id: UUID | None,
        shipment_date: date,
    ) -> _DieselFloaterResult | None:
        # ── 1. Bracket-based resolution (preferred) ──────────────────────
        # Look up the Destatis reference price (2-month lag), then find the
        # carrier's bracket row with the lowest price_ct_max >= reference price.
        if carrier_id is not None:
            try:
                destatis = get_destatis_service()
                ref_price = await destatis.resolve_for_date(db, shipment_date)
                if ref_price is not None:
                    bracket = (
                        await db.execute(
                            select(DieselPriceBracket)
                            .where(
                                DieselPriceBracket.tenant_id == tenant_id,
                                DieselPriceBracket.carrier_id == carrier_id,
                                DieselPriceBracket.price_ct_max >= ref_price,
                                DieselPriceBracket.valid_from <= shipment_date,
                                or_(
                                    DieselPriceBracket.valid_until.is_(None),
                                    DieselPriceBracket.valid_until >= shipment_date,
                                ),
                            )
                            .order_by(DieselPriceBracket.price_ct_max.asc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()

                    if bracket is not None:
                        self.logger.info(
                            "diesel_bracket_resolved",
                            ref_price_ct=float(ref_price),
                            bracket_max_ct=float(bracket.price_ct_max),
                            floater_pct=float(bracket.floater_pct),
                            date=shipment_date.isoformat(),
                        )
                        return _DieselFloaterResult(
                            pct=Decimal(str(bracket.floater_pct)),
                            basis=bracket.basis or "base",
                        )
            except Exception as exc:
                self.logger.error("diesel_bracket_lookup_error", error=str(exc))

        # ── 2. Manual flat override (diesel_floater table) ────────────────
        try:
            stmt = (
                select(DieselFloater)
                .where(
                    DieselFloater.tenant_id == tenant_id,
                    DieselFloater.carrier_id == carrier_id,
                    DieselFloater.valid_from <= shipment_date,
                    or_(
                        DieselFloater.valid_until.is_(None),
                        DieselFloater.valid_until >= shipment_date,
                    ),
                )
                .order_by(DieselFloater.valid_from.desc())
                .limit(1)
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row is not None:
                return _DieselFloaterResult(
                    pct=Decimal(str(row.floater_pct)),
                    basis=row.basis or "base",
                )
        except Exception as exc:
            self.logger.error("diesel_floater_lookup_error", error=str(exc))

        self.logger.warning(
            "diesel_floater_missing",
            tenant_id=str(tenant_id),
            carrier_id=str(carrier_id),
            date=shipment_date.isoformat(),
        )
        return None

    async def _create_shipment_benchmark(
        self,
        db: AsyncSession,
        shipment: Shipment,
        result: BenchmarkResult,
        report_fx_rate: Decimal | None,
        tenant_currency: str,
    ) -> None:
        try:
            toll = result.expected_toll_amount
            diesel = result.expected_diesel_amount
            benchmark = ShipmentBenchmark(
                shipment_id=shipment.id,
                tenant_id=shipment.tenant_id,
                tariff_table_id=UUID(result.calculation_metadata["tariff_table_id"]),
                zone_calculated=result.calculation_metadata["zone_calculated"],
                chargeable_weight=result.cost_breakdown[0].weight,
                chargeable_basis=result.cost_breakdown[0].description,
                expected_base_amount=result.expected_base_amount,
                expected_toll_amount=toll if toll else None,
                expected_diesel_amount=diesel if diesel else None,
                expected_total_amount=result.expected_total_amount,
                actual_total_amount=result.actual_total_amount or Decimal("0"),
                delta_amount=result.delta_amount or Decimal("0"),
                delta_pct=result.delta_pct or Decimal("0"),
                classification=result.classification or "im_markt",
                currency=shipment.currency,
                report_currency=(
                    tenant_currency if shipment.currency != tenant_currency else None
                ),
                fx_rate_used=report_fx_rate,
                fx_rate_date=shipment.date if report_fx_rate is not None else None,
                diesel_basis_used=result.calculation_metadata.get("diesel_basis_used"),
                diesel_pct_used=Decimal(str(result.calculation_metadata["diesel_pct_used"])) if result.calculation_metadata.get("diesel_pct_used") is not None else None,
                cost_breakdown=[item.to_dict() for item in result.cost_breakdown],
                report_amounts=result.report_amounts,
                calculation_metadata=result.calculation_metadata,
                calc_version=result.calculation_metadata.get("calc_version"),
            )
            db.add(benchmark)
            await db.flush()
        except Exception as exc:
            self.logger.error("create_benchmark_error", error=str(exc))
            # Non-critical: don't propagate


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tariff_engine: TariffEngineService | None = None


def get_tariff_engine_service() -> TariffEngineService:
    global _tariff_engine
    if _tariff_engine is None:
        _tariff_engine = TariffEngineService()
    return _tariff_engine
