"""DieselFloaterParser — extract price bracket tables from carrier diesel floater PDFs.

Issue: #63

Key features:
- LLM extraction of (price_ct_max, floater_pct) rows from PDF text
- Returns structured DieselFloaterParseResult with brackets + metadata
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import anthropic
import structlog

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a precise data extraction engine for German freight logistics documents. "
    "Your output MUST be a single valid JSON object — no markdown, no explanation."
)

_PROMPT = """\
Extract the diesel surcharge bracket table from this document.

The table maps diesel price thresholds (in Cent per Liter) to surcharge percentages.
Each row means: if the reference diesel price is ≤ price_ct_max, apply floater_pct.

Return a JSON object with this exact structure:
{{
  "carrier_name": "string or null",
  "valid_from": "YYYY-MM-DD or null",
  "basis": "base | base_plus_toll | total",
  "brackets": [
    {{"price_ct_max": number, "floater_pct": number}},
    ...
  ],
  "issues": ["list of data quality problems, if any"]
}}

Rules:
- valid_from: the date from which this table is effective — use the document/letter date \
(e.g. "Datum: 31. Mai 2023" → "2023-05-31"); convert German month names to numbers; \
if no explicit validity date is stated, use the document date; null only if no date at all
- price_ct_max: the upper price threshold in Cent/Liter \
(e.g. 150 for "≤ 1,50 EUR/l" or "≤ 150 Ct/l")
- floater_pct: the surcharge percentage as a decimal (e.g. 13.5 for "13,50%")
- basis: "base" unless the document explicitly states the surcharge applies to base+toll or total
- Sort brackets ascending by price_ct_max
- If the document uses EUR/liter, multiply by 100 to get Ct/liter
- Include ALL rows, including 0.00% rows
- issues: only genuine extraction problems, not structural observations

Document text:
{text}"""


@dataclass
class DieselBracket:
    price_ct_max: Decimal
    floater_pct: Decimal


@dataclass
class DieselFloaterParseResult:
    brackets: list[DieselBracket]
    carrier_name: str | None
    valid_from: date | None
    basis: str
    issues: list[str] = field(default_factory=list)
    confidence: float = 0.0


class DieselFloaterParser:
    """Extract diesel price bracket tables from PDF text using Claude."""

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    async def parse(self, pdf_text: str, *, filename: str = "") -> DieselFloaterParseResult:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = _PROMPT.format(text=pdf_text[:40000])

        self.logger.info("diesel_floater_parse_start", filename=filename)

        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=60,
            )
            raw = response.content[0].text if response.content else ""
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data: dict = json.loads(cleaned)

            brackets = [
                DieselBracket(
                    price_ct_max=Decimal(str(b["price_ct_max"])),
                    floater_pct=Decimal(str(b["floater_pct"])),
                )
                for b in data.get("brackets") or []
                if b.get("price_ct_max") is not None and b.get("floater_pct") is not None
            ]

            basis = data.get("basis") or "base"
            if basis not in ("base", "base_plus_toll", "total"):
                basis = "base"

            valid_from: date | None = None
            raw_date = data.get("valid_from")
            if raw_date:
                try:
                    valid_from = date.fromisoformat(raw_date)
                except ValueError:
                    pass

            confidence = len(brackets) / max(len(brackets), 1) if brackets else 0.0

            self.logger.info(
                "diesel_floater_parse_complete",
                filename=filename,
                bracket_count=len(brackets),
                carrier_name=data.get("carrier_name"),
                valid_from=str(valid_from),
            )

            return DieselFloaterParseResult(
                brackets=brackets,
                carrier_name=data.get("carrier_name"),
                valid_from=valid_from,
                basis=basis,
                issues=data.get("issues") or [],
                confidence=confidence,
            )

        except Exception as exc:
            self.logger.error("diesel_floater_parse_error", filename=filename, error=str(exc))
            return DieselFloaterParseResult(
                brackets=[],
                carrier_name=None,
                valid_from=None,
                basis="base",
                issues=[f"Extraction failed: {exc}"],
                confidence=0.0,
            )


_parser: DieselFloaterParser | None = None


def get_diesel_floater_parser() -> DieselFloaterParser:
    global _parser
    if _parser is None:
        _parser = DieselFloaterParser()
    return _parser
