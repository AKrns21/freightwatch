"""TariffParserService — Vision OCR pipeline to extract rate tables from tariff PDFs.

Issue: #59

Key features:
- Uses DocumentService for text/vision extraction from PDF
- Claude LLM extracts carrier name, valid_from, zones (PLZ prefix → zone int),
  weight-band × rate matrix, Hauptlauf (trunk haul) flat rates, billing_conditions
- Carrier resolved via CarrierService 4-step fallback chain
- Persists to tariff_table + tariff_rate + tariff_zone_map + tariff_nebenkosten
- billing_conditions: known keys → typed tariff_nebenkosten columns; unknown keys → raw_items JSONB
- Returns TariffParseResult; caller decides upload status based on review_action
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

import anthropic
import structlog

from app.config import settings
from app.models.database import TariffNebenkosten, TariffRate, TariffTable, TariffZoneMap
from app.services.carrier_service import get_carrier_service
from app.services.document_service import DocumentService
from app.services.prompts.versions import get_prompt_version

logger = structlog.get_logger(__name__)

# Confidence thresholds — mirror ReviewGate
_THRESHOLD_AUTO_IMPORT = 0.85
_THRESHOLD_HOLD = 0.50

# Maps billing_conditions keys (from LLM) to typed tariff_nebenkosten columns.
# Keys absent from this map are stored in tariff_nebenkosten.raw_items (JSONB).
_NEBENKOSTEN_COLUMN_MAP: dict[str, str] = {
    "ldm_to_kg":          "min_weight_ldm_kg",
    "cbm_to_kg":          "min_weight_cbm_kg",
    "europalette_min_kg": "min_weight_pallet_kg",
    "diesel_pct":         "diesel_floater_pct",
    "eu_mobility_pct":    "eu_mobility_surcharge_pct",
    "payment_days":       "payment_days",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class TariffZoneEntry:
    """A single PLZ-prefix → zone mapping extracted from the document."""

    plz_prefix: str
    zone: int


@dataclass
class TariffRateEntry:
    """A single rate cell: zone × weight band → rate."""

    zone: int
    weight_from_kg: Decimal
    weight_to_kg: Decimal
    rate_per_shipment: Decimal | None
    rate_per_kg: Decimal | None


@dataclass
class TariffParseResult:
    """Structured output from TariffParserService.parse()."""

    carrier_name: str | None
    carrier_id: UUID | None
    customer_name: str | None
    valid_from: date | None
    currency: str
    lane_type: str
    zones: list[TariffZoneEntry]
    rates: list[TariffRateEntry]
    tariff_table_id: UUID | None
    confidence: float
    parsing_method: str
    issues: list[str] = field(default_factory=list)
    billing_conditions: dict[str, Any] = field(default_factory=dict)
    # Routing decision: 'auto_import' | 'hold_for_review' | 'needs_manual_review'
    review_action: str = "hold_for_review"

    def to_dict(self) -> dict[str, Any]:
        return {
            "carrier_name": self.carrier_name,
            "carrier_id": str(self.carrier_id) if self.carrier_id else None,
            "customer_name": self.customer_name,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "currency": self.currency,
            "lane_type": self.lane_type,
            "zone_count": len(self.zones),
            "rate_count": len(self.rates),
            "tariff_table_id": str(self.tariff_table_id) if self.tariff_table_id else None,
            "confidence": self.confidence,
            "parsing_method": self.parsing_method,
            "issues": self.issues,
            "billing_conditions": {
                k: float(v) if isinstance(v, Decimal) else v
                for k, v in self.billing_conditions.items()
            },
            "review_action": self.review_action,
            # Full extracted data — enables tariff preview even before DB persistence
            "zones": [
                {"plz_prefix": z.plz_prefix, "zone": z.zone}
                for z in self.zones
            ],
            "rates": [
                {
                    "zone": r.zone,
                    "weight_from_kg": float(r.weight_from_kg),
                    "weight_to_kg": float(r.weight_to_kg),
                    "rate_per_shipment": float(r.rate_per_shipment) if r.rate_per_shipment is not None else None,
                    "rate_per_kg": float(r.rate_per_kg) if r.rate_per_kg is not None else None,
                }
                for r in self.rates
            ],
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TariffParserService:
    """Parse carrier tariff PDFs and persist to tariff_table + tariff_rate + tariff_zone_map.

    Uses DocumentService for text/vision extraction, then Claude Haiku to
    extract the structured rate table, then CarrierService for carrier resolution.

    Example usage:
        svc = TariffParserService()
        result = await svc.parse(file_bytes, filename="AS 04.2022.pdf",
                                 tenant_id=tenant_id, upload_id=upload_id, db=db)
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)
        self._doc_service = DocumentService()
        self._carrier_service = get_carrier_service()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def parse(
        self,
        file_bytes: bytes,
        *,
        filename: str,
        tenant_id: UUID,
        upload_id: UUID | None = None,
        db: Any,  # AsyncSession — avoid import cycle via Any
    ) -> TariffParseResult:
        """Extract tariff rate table from PDF and persist to DB.

        Args:
            file_bytes: Raw PDF bytes.
            filename:   Original filename (used for logging and carrier name hints).
            tenant_id:  Tenant UUID for RLS context.
            upload_id:  Optional upload UUID to link tariff_table.upload_id.
            db:         AsyncSession with tenant context already set.

        Returns:
            TariffParseResult describing what was extracted and persisted.
        """
        self.logger.info("tariff_parse_start", filename=filename)

        # Stage 1: extract text from PDF
        doc = await self._doc_service.process(file_bytes, filename=filename)
        text = doc.text or ""
        self.logger.info(
            "tariff_doc_extracted",
            filename=filename,
            mode=doc.mode,
            text_len=len(text),
        )

        # Stage 2: LLM extraction
        extracted = await self._extract_via_llm(text, filename=filename)
        self.logger.info(
            "tariff_llm_extracted",
            filename=filename,
            carrier_name=extracted.get("carrier_name"),
            zone_count=len(extracted.get("zones", [])),
            rate_count=len(extracted.get("rates", [])),
            confidence=extracted.get("confidence", 0),
        )

        # Stage 3: resolve carrier
        carrier_name = extracted.get("carrier_name") or ""
        carrier_id: UUID | None = None
        carrier_issue: str | None = None
        if carrier_name:
            resolution = await self._carrier_service.resolve_carrier_id_with_fallback(
                db, carrier_name, tenant_id
            )
            if resolution is not None:
                carrier_id = resolution.carrier_id
                self.logger.info(
                    "tariff_carrier_resolved",
                    carrier_name=carrier_name,
                    carrier_id=str(carrier_id),
                    method=resolution.method,
                )
            else:
                carrier_issue = f"Carrier '{carrier_name}' could not be resolved — assign manually"
                self.logger.warning("tariff_carrier_unresolved", carrier_name=carrier_name)
        else:
            carrier_issue = "No carrier name found in document — assign manually"

        # Stage 4: parse structured data
        zones = self._parse_zones(extracted.get("zones", []))
        rates = self._parse_rates(extracted.get("rates", []))
        valid_from = self._parse_date(extracted.get("valid_from"))
        currency = str(extracted.get("currency") or "EUR").upper()
        lane_type = str(extracted.get("lane_type") or "domestic_de")
        confidence = float(extracted.get("confidence") or 0.0)
        issues: list[str] = list(extracted.get("issues") or [])
        if carrier_issue:
            issues.append(carrier_issue)
        billing_conditions = self._parse_billing_conditions(
            extracted.get("billing_conditions") or {}
        )

        # Stage 5: persist (only if carrier resolved and confidence sufficient)
        tariff_table_id: UUID | None = None
        if carrier_id is not None and rates:
            tariff_table_id = await self._persist(
                db,
                tenant_id=tenant_id,
                upload_id=upload_id,
                carrier_id=carrier_id,
                carrier_name=carrier_name,
                customer_name=extracted.get("customer_name"),
                valid_from=valid_from,
                currency=currency,
                lane_type=lane_type,
                confidence=confidence,
                zones=zones,
                rates=rates,
                billing_conditions=billing_conditions,
                source_data=extracted,
            )

        # Stage 6: routing decision
        review_action = self._decide_action(
            confidence=confidence,
            carrier_id=carrier_id,
            rate_count=len(rates),
            zone_count=len(zones),
        )

        result = TariffParseResult(
            carrier_name=carrier_name or None,
            carrier_id=carrier_id,
            customer_name=extracted.get("customer_name"),
            valid_from=valid_from,
            currency=currency,
            lane_type=lane_type,
            zones=zones,
            rates=rates,
            tariff_table_id=tariff_table_id,
            confidence=confidence,
            parsing_method="llm",
            issues=issues,
            billing_conditions=billing_conditions,
            review_action=review_action,
        )

        self.logger.info(
            "tariff_parse_complete",
            filename=filename,
            review_action=review_action,
            tariff_table_id=str(tariff_table_id) if tariff_table_id else None,
        )
        return result

    # -----------------------------------------------------------------------
    # LLM extraction
    # -----------------------------------------------------------------------

    async def _extract_via_llm(self, text: str, *, filename: str) -> dict[str, Any]:
        """Call Claude to extract structured tariff data from document text.

        Uses settings.vision_model (Sonnet) for the large output window — German
        domestic tariffs can produce 600+ rate rows which overflow Haiku's 8 K
        token limit.
        """
        prompt_data = get_prompt_version(
            "tariff_extractor", settings.tariff_extractor_prompt_version
        )
        system_prompt: str = prompt_data["SYSTEM_PROMPT"]
        prompt_template: str = prompt_data["PROMPT_TEMPLATE"]

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = prompt_template.format(text=text[:40000])

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=settings.vision_model,
                    max_tokens=16384,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=120,
            )
            raw = response.content[0].text if response.content else ""
            cleaned = re.sub(r"```json\n?|```", "", raw).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as json_exc:
                # Truncated response safety net: recover whatever was parsed
                recovered = self._recover_partial_json(cleaned)
                if recovered:
                    self.logger.warning(
                        "tariff_llm_json_truncated_recovered",
                        filename=filename,
                        rates=len(recovered.get("rates", [])),
                        zones=len(recovered.get("zones", [])),
                        error=str(json_exc),
                    )
                    recovered.setdefault("issues", []).append(
                        f"LLM response was truncated — partial extraction ({len(recovered.get('rates', []))} rates)"
                    )
                    return recovered
                raise
        except Exception as exc:
            self.logger.error("tariff_llm_extraction_failed", filename=filename, error=str(exc))
            return {"confidence": 0.0, "issues": [f"LLM extraction failed: {exc}"]}

    @staticmethod
    def _recover_partial_json(text: str) -> dict[str, Any] | None:
        """Attempt to recover a partially-truncated JSON object.

        Scans for the last position where a top-level array element was cleanly
        closed (``},`` or ``}`` followed by ``]``), then appends the minimum
        closing tokens to make the fragment valid JSON.

        Returns the parsed dict, or None if recovery fails.
        """
        # Find candidate cut points: positions just after a closing brace that
        # ends an array element.  Try from the end backwards (at most ~20 tries).
        matches = [m.end() for m in re.finditer(r"\}", text)]
        for pos in reversed(matches[-20:]):
            candidate = text[:pos].rstrip().rstrip(",")
            depth_curly = candidate.count("{") - candidate.count("}")
            depth_square = candidate.count("[") - candidate.count("]")
            if depth_curly < 0 or depth_square < 0:
                continue
            closing = "]" * depth_square + "}" * depth_curly
            try:
                result: dict[str, Any] = json.loads(candidate + closing)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue
        return None

    # -----------------------------------------------------------------------
    # Parsing helpers
    # -----------------------------------------------------------------------

    def _parse_zones(self, raw: list[dict]) -> list[TariffZoneEntry]:
        zones: list[TariffZoneEntry] = []
        for item in raw:
            try:
                zone = int(item["zone"])
                plz_str = str(item["plz_prefix"])
                for prefix in self._expand_plz_prefixes(plz_str):
                    zones.append(TariffZoneEntry(plz_prefix=prefix, zone=zone))
            except (KeyError, ValueError, TypeError) as exc:
                self.logger.warning("tariff_zone_parse_error", item=item, error=str(exc))
        return zones

    @staticmethod
    def _expand_plz_prefixes(plz_str: str) -> list[str]:
        """Expand a PLZ prefix string into individual prefix values.

        Handles the following formats produced by LLM extraction:
          - Single:         "80"           → ["80"]
          - Range:          "80-81"        → ["80", "81"]
          - Padded range:   "07-09"        → ["07", "08", "09"]
          - En-dash range:  "80–82"        → ["80", "81", "82"]
          - Comma list:     "07-09, 36"    → ["07", "08", "09", "36"]
          - Mixed:          "80-81, 89"    → ["80", "81", "89"]

        Falls back to returning the raw string as a single entry if no
        numeric tokens are found (e.g. unexpected format).
        """
        result: list[str] = []
        parts = re.split(r"[,;]+", plz_str)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Range: digits [- or –] digits
            range_match = re.match(r"^(\d+)\s*[-\u2013]\s*(\d+)$", part)
            if range_match:
                start_str, end_str = range_match.group(1), range_match.group(2)
                pad = len(start_str)
                for i in range(int(start_str), int(end_str) + 1):
                    result.append(str(i).zfill(pad))
            elif re.match(r"^\d+$", part):
                result.append(part)
            # else: skip unrecognised token
        return result if result else [plz_str]

    def _parse_rates(self, raw: list[dict]) -> list[TariffRateEntry]:
        rates: list[TariffRateEntry] = []
        for item in raw:
            try:
                rps = item.get("rate_per_shipment")
                rpk = item.get("rate_per_kg")
                rates.append(
                    TariffRateEntry(
                        zone=int(item["zone"]),
                        weight_from_kg=Decimal(str(item["weight_from_kg"])),
                        weight_to_kg=Decimal(str(item["weight_to_kg"])),
                        rate_per_shipment=Decimal(str(rps)) if rps is not None else None,
                        rate_per_kg=Decimal(str(rpk)) if rpk is not None else None,
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                self.logger.warning("tariff_rate_parse_error", item=item, error=str(exc))
        return rates

    def _parse_date(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.logger.warning("tariff_date_parse_error", value=value)
            return None

    def _parse_billing_conditions(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validate and coerce billing_conditions from LLM output.

        All values are coerced to Decimal (or int for payment_days).
        Non-numeric values are dropped with a warning.

        Returns a dict of {key: Decimal | int}.
        """
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if value is None:
                continue
            try:
                if key == "payment_days":
                    result[key] = int(value)
                else:
                    result[key] = Decimal(str(value))
            except (ValueError, TypeError, InvalidOperation) as exc:
                self.logger.warning(
                    "billing_condition_parse_error",
                    key=key,
                    value=value,
                    error=str(exc),
                )
        return result

    # -----------------------------------------------------------------------
    # DB persistence
    # -----------------------------------------------------------------------

    async def _persist(
        self,
        db: Any,
        *,
        tenant_id: UUID,
        upload_id: UUID | None,
        carrier_id: UUID,
        carrier_name: str,
        customer_name: str | None,
        valid_from: date | None,
        currency: str,
        lane_type: str,
        confidence: float,
        zones: list[TariffZoneEntry],
        rates: list[TariffRateEntry],
        billing_conditions: dict[str, Any],
        source_data: dict[str, Any],
    ) -> UUID:
        """Persist tariff_table + tariff_rate + tariff_zone_map + tariff_nebenkosten rows."""
        from datetime import datetime

        effective_from = valid_from or date.today()
        name = f"{carrier_name} – {effective_from.strftime('%m/%Y')}"
        if customer_name:
            name = f"{name} ({customer_name})"

        tariff_table = TariffTable(
            id=uuid4(),  # generate client-side so it's available before flush
            tenant_id=tenant_id,
            carrier_id=carrier_id,
            upload_id=upload_id,
            name=name,
            lane_type=lane_type,
            currency=currency,
            valid_from=effective_from,
            confidence=Decimal(str(round(confidence, 2))),
            source_data=source_data,
        )
        db.add(tariff_table)
        await db.flush()  # get tariff_table.id

        for zone_entry in zones:
            db.add(
                TariffZoneMap(
                    tariff_table_id=tariff_table.id,
                    country_code="DE",
                    plz_prefix=zone_entry.plz_prefix,
                    match_type="prefix",
                    zone=zone_entry.zone,
                )
            )

        for rate_entry in rates:
            db.add(
                TariffRate(
                    tariff_table_id=tariff_table.id,
                    zone=rate_entry.zone,
                    weight_from_kg=rate_entry.weight_from_kg,
                    weight_to_kg=rate_entry.weight_to_kg,
                    rate_per_shipment=rate_entry.rate_per_shipment,
                    rate_per_kg=rate_entry.rate_per_kg,
                )
            )

        if billing_conditions:
            self._persist_nebenkosten(db, tariff_table.id, billing_conditions)

        await db.flush()
        self.logger.info(
            "tariff_persisted",
            tariff_table_id=str(tariff_table.id),
            zone_count=len(zones),
            rate_count=len(rates),
            billing_condition_count=len(billing_conditions),
        )
        return tariff_table.id

    def _persist_nebenkosten(
        self,
        db: Any,
        tariff_table_id: UUID,
        billing_conditions: dict[str, Any],
    ) -> None:
        """Create a TariffNebenkosten row from extracted billing_conditions.

        Known keys are written to their typed columns; any remaining keys are
        stored in raw_items JSONB so they are not lost.
        """
        kwargs: dict[str, Any] = {"tariff_table_id": tariff_table_id}
        raw_items: dict[str, Any] = {}

        for key, value in billing_conditions.items():
            col = _NEBENKOSTEN_COLUMN_MAP.get(key)
            if col:
                kwargs[col] = value
            else:
                raw_items[key] = float(value) if isinstance(value, Decimal) else value

        if raw_items:
            kwargs["raw_items"] = raw_items

        db.add(TariffNebenkosten(**kwargs))

    # -----------------------------------------------------------------------
    # Routing
    # -----------------------------------------------------------------------

    def _decide_action(
        self,
        *,
        confidence: float,
        carrier_id: UUID | None,
        rate_count: int,
        zone_count: int,
    ) -> str:
        if carrier_id is None:
            return "needs_manual_review"
        if rate_count == 0:
            return "needs_manual_review"
        if confidence >= _THRESHOLD_AUTO_IMPORT and zone_count > 0:
            return "auto_import"
        if confidence >= _THRESHOLD_HOLD:
            return "hold_for_review"
        return "needs_manual_review"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tariff_parser: TariffParserService | None = None


def get_tariff_parser() -> TariffParserService:
    """Return the module-level TariffParserService singleton."""
    global _tariff_parser
    if _tariff_parser is None:
        _tariff_parser = TariffParserService()
    return _tariff_parser
