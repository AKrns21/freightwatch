"""Tariff PDF parser — LLM-based extraction with template fallback.

Ports tariff-pdf-parser.service.ts to Python/FastAPI.

Strategy:
1. Try DB template matching (fast, deterministic, ≥70% score required)
2. Fall back to Claude LLM for unknown formats

Templates support two extraction strategies:
  - "pre_parsed"  : full canonical JSON already embedded in template.mappings.tariff_structure
  - "text_grid"   : regex-based scanning of PDF text

The LLM prompt uses the canonical FreightWatch tariff JSON schema with
few-shot context so Claude returns structured output.
"""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import anthropic
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import ParsingTemplate
from app.services.parsing import (
    NebenkostenInfo,
    PlzZoneMapping,
    TariffEntry,
    TariffParseResult,
    ZoneInfo,
)
from app.utils.error_handler import handle_service_errors
from app.utils.logger import get_logger
from app.utils.round import round_monetary

logger = get_logger(__name__)

# ── LLM prompts ──────────────────────────────────────────────────────────────

_LLM_SYSTEM = """\
You are a freight tariff extraction specialist for a logistics cost analysis platform.
Extract the complete tariff structure from carrier documents.
Return ONLY valid JSON — no markdown fences, no explanation."""

_LLM_USER_TMPL = """\
Extract the tariff structure from the document below and return a JSON object with \
this exact schema:

{{
  "meta": {{
    "carrier_name": "string",
    "valid_from": "DD.MM.YYYY",
    "valid_until": "DD.MM.YYYY or null",
    "lane_type": "domestic_de|domestic_at|domestic_ch|de_to_ch|de_to_at",
    "currency": "EUR"
  }},
  "tariff": {{
    "zones": [
      {{"zone_number": 1, "label": "Zone 1", "plz_description": "DE 75000-77999"}}
    ],
    "matrix": [
      {{
        "band_label": "bis 50 kg",
        "weight_from": 1,
        "weight_to": 50,
        "prices": {{"zone_1": 19.10, "zone_2": 19.62}}
      }}
    ],
    "plz_zone_map": [
      {{"country_code": "DE", "plz_prefix": "75", "match_type": "prefix", "zone": 1}}
    ]
  }},
  "nebenkosten": {{
    "diesel_floater_pct": null,
    "eu_mobility_surcharge_pct": 5.0,
    "maut_included": true,
    "delivery_condition": "frei Haus",
    "min_weight_pallet_kg": 500,
    "min_weight_per_cbm_kg": 300,
    "min_weight_per_ldm_kg": 1650
  }}
}}

Rules:
- lane_type: "domestic_de"=Germany, "domestic_at"=Austria, "domestic_ch"=Switzerland, \
"de_to_ch"=DE→CH, "de_to_at"=DE→AT
- matrix weight_from: 1 for the first band; previous weight_to + 1 for each subsequent band
- prices keys: "zone_1", "zone_2", ... matching the zone_number values in zones array
- plz_zone_map: one entry per PLZ prefix (2–5 digits), e.g. "75" covers all 75xxx postal codes
- Set null for any field not determinable from the document

Document:
{text}"""


# ── module-level helpers ─────────────────────────────────────────────────────


def _parse_eu_number(value: str) -> float | None:
    """Parse European number format (comma decimal / dot thousands)."""
    if not value:
        return None
    cleaned = value.strip()
    dot_pos = cleaned.rfind(".")
    comma_pos = cleaned.rfind(",")
    if dot_pos > -1 and comma_pos > -1:
        normalized = (
            cleaned.replace(",", "")            # US: 1,234.56
            if dot_pos > comma_pos
            else cleaned.replace(".", "").replace(",", ".")  # EU: 1.234,56
        )
    elif comma_pos > -1:
        normalized = cleaned.replace(",", ".")
    else:
        normalized = cleaned
    try:
        return float(normalized)
    except ValueError:
        return None


def _parse_zone_label(label: str) -> int:
    """Convert zone label (Roman numeral or integer string) to int."""
    trimmed = label.strip().upper()
    try:
        return int(trimmed)
    except ValueError:
        pass
    return {
        "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
        "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    }.get(trimmed, 1)


def _parse_date(value: str) -> date | None:
    """Parse dd.mm.yyyy, dd/mm/yyyy, or yyyy-mm-dd into a date."""
    if not value:
        return None
    trimmed = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", trimmed)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", trimmed)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100:
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def _validate_entries(entries: list[TariffEntry]) -> list[str]:
    """Return list of validation issues."""
    issues: list[str] = []
    for e in entries:
        if e.zone < 0:
            issues.append(f"Invalid zone: {e.zone}")
        if e.weight_min < 0 or e.weight_max < e.weight_min:
            issues.append(f"Invalid weight range: {e.weight_min}–{e.weight_max}")
        if e.base_amount <= 0:
            issues.append(f"Invalid price for zone {e.zone}: {e.base_amount}")
    return issues


def _calculate_confidence(entries: list[TariffEntry], method: str) -> Decimal:
    """Confidence score: baseline × fraction of fully-complete entries."""
    if not entries:
        return Decimal("0")
    baseline = Decimal("0.95") if method == "template" else Decimal("0.85")
    complete = sum(
        1 for e in entries
        if e.zone >= 0
        and e.weight_min is not None
        and e.weight_max is not None
        and e.base_amount > 0
    )
    coverage = Decimal(complete) / Decimal(len(entries))
    return min(Decimal("1.0"), max(Decimal("0.0"), baseline * coverage))


def _to_decimal(val: Any) -> Decimal | None:
    """Safe Any → Decimal conversion; returns None on failure."""
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError):
        return None


# ── structure converters (shared by template + LLM paths) ───────────────────


def _entries_from_structure(
    structure: dict[str, Any], mappings: dict[str, Any]
) -> list[TariffEntry]:
    """Extract TariffEntry list from canonical tariff.matrix structure."""
    entries: list[TariffEntry] = []
    matrix = structure.get("matrix") or []
    currency = mappings.get("currency") or structure.get("currency") or "EUR"
    service_level: str | None = mappings.get("service_level")

    for band in matrix:
        try:
            wf = Decimal(str(band.get("weight_from", 1)))
            wt = Decimal(str(band.get("weight_to")))
        except (InvalidOperation, TypeError):
            continue
        for key, raw_price in (band.get("prices") or {}).items():
            m = re.match(r"^zone_(\d+)$", key)
            if not m or raw_price is None:
                continue
            try:
                price = round_monetary(Decimal(str(raw_price)))
            except (InvalidOperation, TypeError):
                continue
            if price <= 0:
                continue
            entries.append(
                TariffEntry(
                    zone=int(m.group(1)),
                    weight_min=wf,
                    weight_max=wt,
                    base_amount=price,
                    currency=currency,
                    service_level=service_level,
                )
            )
    return entries


def _zones_from_structure(structure: dict[str, Any]) -> list[ZoneInfo]:
    """Extract ZoneInfo list from canonical tariff.zones array."""
    zones: list[ZoneInfo] = []
    for z in (structure.get("zones") or []):
        if not isinstance(z, dict) or z.get("zone_number") is None:
            continue
        zones.append(
            ZoneInfo(
                zone_number=int(z["zone_number"]),
                label=z.get("label") or f"Zone {z['zone_number']}",
                plz_description=z.get("plz_description"),
            )
        )
    return zones


def _zone_maps_from_structure(structure: dict[str, Any]) -> list[PlzZoneMapping]:
    """Extract PlzZoneMapping list from canonical tariff.plz_zone_map array."""
    maps: list[PlzZoneMapping] = []
    for entry in (structure.get("plz_zone_map") or []):
        if not isinstance(entry, dict):
            continue
        maps.append(
            PlzZoneMapping(
                country_code=entry.get("country_code") or "DE",
                plz_prefix=str(entry.get("plz_prefix") or ""),
                match_type=entry.get("match_type") or "prefix",
                zone=int(entry.get("zone") or 1),
            )
        )
    return maps


def _nebenkosten_from_block(nk: dict[str, Any]) -> NebenkostenInfo | None:
    """Convert a nebenkosten dict (flat or {typed: {...}}) to NebenkostenInfo."""
    if not nk:
        return None
    typed: dict[str, Any] = nk.get("typed") or nk  # support both layouts
    info = NebenkostenInfo(
        diesel_floater_pct=_to_decimal(typed.get("diesel_floater_pct")),
        eu_mobility_surcharge_pct=_to_decimal(typed.get("eu_mobility_surcharge_pct")),
        maut_included=typed.get("maut_included"),
        delivery_condition=typed.get("delivery_condition"),
        min_weight_pallet_kg=_to_decimal(typed.get("min_weight_pallet_kg")),
        min_weight_per_cbm_kg=_to_decimal(typed.get("min_weight_per_cbm_kg")),
        min_weight_per_ldm_kg=_to_decimal(typed.get("min_weight_per_ldm_kg")),
        raw_items=nk if "typed" not in nk else {},
    )
    return info


def _extract_from_text_grid(text: str, mappings: dict[str, Any]) -> list[TariffEntry]:
    """Extract entries by scanning raw PDF text with configurable regex patterns.

    Required mappings field:
        zone_count        {int}    Number of zone columns.
    Optional mappings fields:
        zone_header_regex {str}    Override default Zone header pattern.
        weight_row_regex  {str}    Override default weight-row pattern.
        currency          {str}    Default "EUR".
        service_level     {str}
    """
    zone_count = int(mappings.get("zone_count") or 0)
    if zone_count < 1:
        logger.warning("text_grid_strategy_missing_zone_count")
        return []

    currency: str = mappings.get("currency") or "EUR"
    service_level: str | None = mappings.get("service_level")
    lines = text.split("\n")

    # Zone header pattern
    zh_str = mappings.get("zone_header_regex")
    zone_header_re = re.compile(zh_str, re.I) if zh_str else re.compile(
        r"Zone\s+(?:I{1,3}|IV|V?I{0,3}|\d+)", re.I
    )

    # Weight row pattern: "bis NNN kg  P1  P2  P3 ..."
    wr_str = mappings.get("weight_row_regex")
    weight_row_re = re.compile(wr_str, re.I) if wr_str else re.compile(
        r"bis\s+([\d.,]+)\s*kg((?:\s+[\d.,]+)+)", re.I
    )

    # Discover zone numbers from header line
    zone_numbers: list[int] = []
    for line in lines:
        if zone_header_re.search(line):
            for m in re.finditer(r"Zone\s+(\w+)", line, re.I):
                zone_numbers.append(_parse_zone_label(m.group(1)))
            if len(zone_numbers) >= zone_count:
                break

    if not zone_numbers:
        zone_numbers = list(range(1, zone_count + 1))

    entries: list[TariffEntry] = []
    prev_weight_to = 0.0

    for line in lines:
        m = weight_row_re.search(line)
        if not m:
            continue
        wt_val = _parse_eu_number(m.group(1))
        if wt_val is None or wt_val <= 0:
            continue

        weight_from = prev_weight_to + 1 if prev_weight_to > 0 else 1
        prev_weight_to = wt_val

        tokens = (m.group(2) or "").strip().split()
        prices = [_parse_eu_number(t) for t in tokens]
        valid_prices = [p for p in prices if p is not None and p > 0]

        for zone_num, price in zip(zone_numbers, valid_prices):
            entries.append(
                TariffEntry(
                    zone=zone_num,
                    weight_min=Decimal(str(weight_from)),
                    weight_max=Decimal(str(wt_val)),
                    base_amount=round_monetary(Decimal(str(price))),
                    currency=currency,
                    service_level=service_level,
                )
            )

    return entries


def _extract_metadata(text: str, mappings: dict[str, Any]) -> dict[str, Any]:
    """Extract carrier metadata — priority: template static > embedded LLM > text scan."""
    result: dict[str, Any] = {}

    # 1. Static metadata from template mappings
    static: dict[str, Any] = mappings.get("metadata") or {}
    for key in ("carrier_id", "carrier_name", "lane_type"):
        if static.get(key):
            result[key] = str(static[key])
    for key in ("valid_from", "valid_until"):
        if static.get(key):
            d = _parse_date(str(static[key]))
            if d:
                result[key] = d

    # 2. Embedded LLM structure in template
    embedded: dict[str, Any] = (mappings.get("tariff_structure") or {}).get("meta") or {}
    if not result.get("carrier_name") and embedded.get("carrier_name"):
        result["carrier_name"] = str(embedded["carrier_name"])
    for key in ("valid_from", "valid_until"):
        if not result.get(key) and embedded.get(key):
            d = _parse_date(str(embedded[key]))
            if d:
                result[key] = d

    # 3. Regex scan of PDF text for anything still missing
    if not all(result.get(k) for k in ("carrier_name", "valid_from", "lane_type")):
        scanned = _scan_text_for_metadata(text)
        for key, val in scanned.items():
            if not result.get(key):
                result[key] = val

    return result


def _scan_text_for_metadata(text: str) -> dict[str, Any]:
    """Best-effort regex scan of PDF text for carrier name, dates, lane type."""
    result: dict[str, Any] = {}
    if not text:
        return result

    # Validity dates
    for pattern, key in [
        (
            r"(?:Gültig\s+ab|Valid\s+from|Stand|ab\s+dem)[:\s]+"
            r"(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})",
            "valid_from",
        ),
        (
            r"(?:Gültig\s+bis|Valid\s+until|bis\s+zum)[:\s]+"
            r"(\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2})",
            "valid_until",
        ),
    ]:
        if not result.get(key):
            m = re.search(pattern, text, re.I)
            if m:
                d = _parse_date(m.group(1))
                if d:
                    result[key] = d

    # Carrier name (German company suffix)
    m = re.search(
        r"([A-ZÄÖÜ][A-Za-zäöüÄÖÜß\s&.,\-]{3,60}"
        r"(?:GmbH|AG|KG|OHG|e\.K\.|GmbH\s*&\s*Co\.\s*KG)[^\n]{0,40})",
        text,
    )
    if m:
        result["carrier_name"] = m.group(1).strip().replace("  ", " ")

    # Lane type heuristic
    tl = text.lower()
    if "österreich" in tl or "austria" in tl:
        result["lane_type"] = "domestic_at"
    elif "schweiz" in tl or "switzerland" in tl:
        result["lane_type"] = "domestic_ch"
    elif "deutschland" in tl or "germany" in tl or "stückgutversand" in tl:
        result["lane_type"] = "domestic_de"

    return result


def _parse_json_response(text: str) -> dict[str, Any] | None:
    """Extract and parse JSON object from LLM response text."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _llm_response_to_result(data: dict[str, Any]) -> TariffParseResult:
    """Convert validated LLM JSON dict to TariffParseResult."""
    meta: dict[str, Any] = data.get("meta") or {}
    tariff: dict[str, Any] = data.get("tariff") or {}
    nk_raw: dict[str, Any] = data.get("nebenkosten") or {}

    carrier_name = str(meta.get("carrier_name") or "Unknown")
    lane_type = str(meta.get("lane_type") or "domestic_de")
    currency = str(meta.get("currency") or "EUR")
    valid_from = _parse_date(str(meta.get("valid_from") or "")) or date.today().replace(
        month=1, day=1
    )
    valid_until = (
        _parse_date(str(meta.get("valid_until")))
        if meta.get("valid_until")
        else None
    )

    zones = _zones_from_structure(tariff)
    entries = _entries_from_structure(tariff, {"currency": currency})
    zone_maps = _zone_maps_from_structure(tariff)
    nebenkosten = _nebenkosten_from_block(nk_raw) if nk_raw else None

    issues = _validate_entries(entries)
    confidence = _calculate_confidence(entries, "llm")

    return TariffParseResult(
        carrier_name=carrier_name,
        lane_type=lane_type,
        valid_from=valid_from,
        valid_until=valid_until,
        currency=currency,
        entries=entries,
        zones=zones,
        zone_maps=zone_maps,
        nebenkosten=nebenkosten,
        parsing_method="llm",
        confidence=confidence,
        issues=issues,
        raw_structure=data,
    )


# ── parser class ─────────────────────────────────────────────────────────────


class TariffPdfParser:
    """Parse carrier tariff PDFs into structured TariffParseResult.

    Accepts pre-extracted text (from DocumentService) to avoid re-implementing
    PDF extraction.
    """

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @handle_service_errors("tariff_pdf_parse")
    async def parse(
        self,
        text: str,
        filename: str,
        tenant_id: UUID | None = None,
        carrier_id: str | None = None,
        db: AsyncSession | None = None,
    ) -> TariffParseResult:
        """Parse tariff from pre-extracted PDF text.

        Args:
            text:       Full text extracted by DocumentService.
            filename:   Original filename (for template matching).
            tenant_id:  Tenant UUID (for tenant-scoped template lookup).
            carrier_id: Known carrier UUID string (improves template matching).
            db:         Optional async DB session for template lookup.

        Returns:
            TariffParseResult with entries, zone maps, and nebenkosten.
        """
        logger.info(
            "tariff_pdf_parse_start",
            filename=filename,
            text_len=len(text),
            has_db=db is not None,
        )

        # Step 1: Template matching (requires DB session)
        if db is not None:
            template = await self._find_matching_template(filename, carrier_id, tenant_id, db)
            if template is not None:
                try:
                    result = self._parse_with_template(text, template)
                    logger.info(
                        "tariff_template_parse_success",
                        filename=filename,
                        template_id=str(template.id),
                        entry_count=len(result.entries),
                    )
                    return result
                except Exception as exc:
                    logger.warning(
                        "tariff_template_parse_failed",
                        filename=filename,
                        template_id=str(template.id),
                        error=str(exc),
                    )
                    # Fall through to LLM

        # Step 2: LLM fallback
        result = await self._parse_with_llm(text, filename)
        logger.info(
            "tariff_llm_parse_success",
            filename=filename,
            entry_count=len(result.entries),
            confidence=str(result.confidence),
        )
        return result

    # ── template matching ────────────────────────────────────────────────────

    async def _find_matching_template(
        self,
        filename: str,
        carrier_id: str | None,
        tenant_id: UUID | None,
        db: AsyncSession,
    ) -> ParsingTemplate | None:
        """Score all tariff templates and return the best match (≥0.7) or None."""
        stmt = (
            select(ParsingTemplate)
            .where(
                ParsingTemplate.template_category == "tariff",
                ParsingTemplate.deleted_at.is_(None),
                or_(
                    ParsingTemplate.tenant_id == tenant_id,
                    ParsingTemplate.tenant_id.is_(None),
                ),
            )
            .order_by(ParsingTemplate.usage_count.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()

        best: ParsingTemplate | None = None
        best_score = 0.0

        for tpl in rows:
            score = 0.0
            det: dict[str, Any] = tpl.detection or {}

            if carrier_id and det.get("carrier_id") == carrier_id:
                score += 0.4

            pattern = det.get("filename_pattern")
            if pattern:
                try:
                    if re.search(pattern, filename, re.I):
                        score += 0.3
                except re.error:
                    pass

            if tenant_id and tpl.tenant_id == tenant_id:
                score += 0.2

            if (tpl.usage_count or 0) > 5:
                score += 0.1

            if score > best_score:
                best_score = score
                best = tpl

        if best_score >= 0.7:
            return best
        return None

    # ── template-based parsing ───────────────────────────────────────────────

    def _parse_with_template(
        self, text: str, template: ParsingTemplate
    ) -> TariffParseResult:
        """Parse tariff using a DB-stored template (synchronous)."""
        mappings: dict[str, Any] = template.mappings or {}

        if "tariff_structure" in mappings:
            structure: dict[str, Any] = mappings["tariff_structure"]
            entries = _entries_from_structure(structure, mappings)
            zones = _zones_from_structure(structure)
            zone_maps = _zone_maps_from_structure(structure)
            nebenkosten = _nebenkosten_from_block(structure.get("nebenkosten") or {})
        elif mappings.get("strategy") == "text_grid":
            entries = _extract_from_text_grid(text, mappings)
            zones = []
            zone_maps = []
            nebenkosten = None
        else:
            raise ValueError(
                "Template has no recognized strategy "
                "(expected tariff_structure or strategy='text_grid')"
            )

        if not entries:
            raise ValueError("No entries extracted via template")

        meta = _extract_metadata(text, mappings)
        issues = _validate_entries(entries)
        confidence = _calculate_confidence(entries, "template")

        return TariffParseResult(
            carrier_name=meta.get("carrier_name") or "Unknown",
            lane_type=meta.get("lane_type") or "domestic_de",
            valid_from=meta.get("valid_from") or date.today().replace(month=1, day=1),
            valid_until=meta.get("valid_until"),
            currency=mappings.get("currency") or "EUR",
            entries=entries,
            zones=zones,
            zone_maps=zone_maps,
            nebenkosten=nebenkosten,
            parsing_method="template",
            confidence=confidence,
            issues=issues,
        )

    # ── LLM-based parsing ────────────────────────────────────────────────────

    async def _parse_with_llm(self, text: str, filename: str) -> TariffParseResult:
        """Parse tariff using Claude LLM (async)."""
        # Truncate very long documents — keep first 50k chars (covers most PDFs)
        truncated = text[:50_000]

        response = await self._client.messages.create(
            model=settings.vision_model,
            max_tokens=8192,
            system=_LLM_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": _LLM_USER_TMPL.format(text=truncated),
                }
            ],
        )

        raw = response.content[0].text if response.content else ""
        if not raw.strip():
            raise ValueError("LLM returned empty response for tariff extraction")

        data = _parse_json_response(raw)
        if data is None:
            raise ValueError(f"LLM response is not valid JSON: {raw[:300]!r}")

        return _llm_response_to_result(data)
