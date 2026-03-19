"""InvoiceMatcherService — Match invoice lines to shipments.

Matching strategy (in order):
  1. Exact reference number match (confidence 1.0)
  2. Multi-criteria fuzzy match:
       - Date window ±3 days       (up to 0.25)
       - Origin zip prefix         (up to 0.20)
       - Destination zip prefix    (up to 0.20)
       - Weight ±20 %              (up to 0.15)
       - Amount ±10 %              (up to 0.20)
  3. Mark unmatched if best score < 0.70

Port of backend_legacy/src/modules/invoice/invoice-matcher.service.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, between, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Shipment
from app.utils.logger import get_logger

logger = get_logger(__name__)

MatchType = Literal["exact", "fuzzy", "manual", "unmatched"]

_FUZZY_THRESHOLD = 0.70
_AMBIGUOUS_DELTA = 0.10
_AMBIGUOUS_MIN = 0.80
_DATE_WINDOW_DAYS = 3
_WEIGHT_TOLERANCE = 0.20
_MAX_CANDIDATES = 10


@dataclass
class MatchResult:
    invoice_line_id: str
    shipment_id: str | None
    confidence: float
    match_type: MatchType
    match_criteria: list[str]
    issues: list[str] | None = None


@dataclass
class MatchingStats:
    total_lines: int
    matched: int
    unmatched: int
    ambiguous: int
    manual: int
    avg_confidence: float


@dataclass
class InvoiceLineInput:
    """Lightweight struct representing an invoice line for matching (no ORM dependency)."""

    id: str
    tenant_id: UUID
    shipment_date: date | None = None
    shipment_reference: str | None = None
    origin_zip: str | None = None
    dest_zip: str | None = None
    weight_kg: float | None = None
    line_total: float | None = None
    service_level: str | None = None
    match_status: str = "unmatched"
    shipment_id: str | None = None
    match_confidence: float = 0.0


class InvoiceMatcherService:
    """Match invoice lines to shipments using reference number, date, route, and weight."""

    async def match_line(
        self,
        db: AsyncSession,
        line: InvoiceLineInput,
        project_id: UUID | None = None,
    ) -> MatchResult:
        """Match a single invoice line to a shipment."""

        # Strategy 1: Exact reference match
        if line.shipment_reference:
            exact = await self._exact_reference_match(
                db, line, project_id=project_id
            )
            if exact is not None:
                return MatchResult(
                    invoice_line_id=line.id,
                    shipment_id=exact,
                    confidence=1.0,
                    match_type="exact",
                    match_criteria=["reference_number"],
                )

        # Strategy 2 + 3: Candidate retrieval + scoring
        candidates = await self._find_candidates(db, line, project_id=project_id)

        if not candidates:
            return MatchResult(
                invoice_line_id=line.id,
                shipment_id=None,
                confidence=0.0,
                match_type="unmatched",
                match_criteria=[],
                issues=["No matching shipments found"],
            )

        scored = sorted(
            [self._score(line, s) for s in candidates],
            key=lambda r: r.confidence,
            reverse=True,
        )

        best = scored[0]

        # Ambiguous: two candidates very close in score
        if (
            len(scored) > 1
            and scored[1].confidence > _AMBIGUOUS_MIN
            and abs(best.confidence - scored[1].confidence) < _AMBIGUOUS_DELTA
        ):
            logger.warning(
                "ambiguous_match",
                invoice_line_id=line.id,
                top_scores=[r.confidence for r in scored[:3]],
            )
            return MatchResult(
                invoice_line_id=line.id,
                shipment_id=best.shipment_id,
                confidence=best.confidence,
                match_type="fuzzy",
                match_criteria=best.match_criteria,
                issues=["Multiple similar matches found"],
            )

        if best.confidence < _FUZZY_THRESHOLD:
            return MatchResult(
                invoice_line_id=line.id,
                shipment_id=None,
                confidence=best.confidence,
                match_type="unmatched",
                match_criteria=best.match_criteria,
                issues=["Confidence below threshold"],
            )

        return best

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _exact_reference_match(
        self,
        db: AsyncSession,
        line: InvoiceLineInput,
        project_id: UUID | None,
    ) -> str | None:
        stmt = select(Shipment.id).where(
            Shipment.tenant_id == line.tenant_id,
            Shipment.reference_number == line.shipment_reference,
        )
        if project_id:
            stmt = stmt.where(Shipment.project_id == project_id)

        result = await db.execute(stmt.limit(1))
        row = result.scalar_one_or_none()
        return str(row) if row is not None else None

    async def _find_candidates(
        self,
        db: AsyncSession,
        line: InvoiceLineInput,
        project_id: UUID | None,
    ) -> list[Shipment]:
        filters = [Shipment.tenant_id == line.tenant_id]

        if project_id:
            filters.append(Shipment.project_id == project_id)

        if line.shipment_date:
            date_from = line.shipment_date - timedelta(days=_DATE_WINDOW_DAYS)
            date_to = line.shipment_date + timedelta(days=_DATE_WINDOW_DAYS)
            filters.append(between(Shipment.date, date_from, date_to))

        if line.origin_zip:
            filters.append(Shipment.origin_zip.like(f"{line.origin_zip[:3]}%"))

        if line.dest_zip:
            filters.append(Shipment.dest_zip.like(f"{line.dest_zip[:3]}%"))

        if line.weight_kg:
            weight_min = line.weight_kg * (1 - _WEIGHT_TOLERANCE)
            weight_max = line.weight_kg * (1 + _WEIGHT_TOLERANCE)
            filters.append(between(Shipment.weight_kg, weight_min, weight_max))

        stmt = select(Shipment).where(and_(*filters)).limit(_MAX_CANDIDATES)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ── scoring ───────────────────────────────────────────────────────────────

    def _score(self, line: InvoiceLineInput, shipment: Shipment) -> MatchResult:
        score = 0.0
        criteria: list[str] = []

        # Date (up to 0.25)
        if line.shipment_date and shipment.date:
            days_diff = abs((line.shipment_date - shipment.date).days)
            if days_diff == 0:
                score += 0.25
                criteria.append("date_exact")
            elif days_diff <= 1:
                score += 0.20
                criteria.append("date_1day")
            elif days_diff <= 3:
                score += 0.15
                criteria.append("date_3days")

        # Origin (up to 0.20)
        if line.origin_zip and shipment.origin_zip:
            if line.origin_zip == shipment.origin_zip:
                score += 0.20
                criteria.append("origin_exact")
            elif line.origin_zip[:3] == shipment.origin_zip[:3]:
                score += 0.15
                criteria.append("origin_prefix")

        # Destination (up to 0.20)
        if line.dest_zip and shipment.dest_zip:
            if line.dest_zip == shipment.dest_zip:
                score += 0.20
                criteria.append("dest_exact")
            elif line.dest_zip[:3] == shipment.dest_zip[:3]:
                score += 0.15
                criteria.append("dest_prefix")

        # Weight (up to 0.15)
        if line.weight_kg and shipment.weight_kg:
            weight_diff = abs(line.weight_kg - float(shipment.weight_kg)) / line.weight_kg
            if weight_diff < 0.05:
                score += 0.15
                criteria.append("weight_exact")
            elif weight_diff < 0.10:
                score += 0.12
                criteria.append("weight_close")
            elif weight_diff < 0.20:
                score += 0.08
                criteria.append("weight_similar")

        # Amount (up to 0.20)
        if line.line_total and shipment.actual_total_amount:
            amount_diff = (
                abs(line.line_total - float(shipment.actual_total_amount)) / line.line_total
            )
            if amount_diff < 0.01:
                score += 0.20
                criteria.append("amount_exact")
            elif amount_diff < 0.05:
                score += 0.15
                criteria.append("amount_close")
            elif amount_diff < 0.10:
                score += 0.10
                criteria.append("amount_similar")

        match_type: MatchType = (
            "exact" if score >= 0.95 else "fuzzy" if score >= _FUZZY_THRESHOLD else "unmatched"
        )

        return MatchResult(
            invoice_line_id=line.id,
            shipment_id=str(shipment.id),
            confidence=min(score, 1.0),
            match_type=match_type,
            match_criteria=criteria,
        )
