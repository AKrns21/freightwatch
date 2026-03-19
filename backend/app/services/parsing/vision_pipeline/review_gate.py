"""Stage 6 — Human review gate.

Confidence thresholds (Architecture §3.2):
  ≥ 0.90          → AUTO_IMPORT
  0.75 – 0.89     → AUTO_IMPORT_FLAG   (imported + flagged for spot check)
  0.50 – 0.74     → HOLD_FOR_REVIEW    (consultant must approve)
  < 0.50          → REJECT             (notify user, do not import)

Validation errors always result in HOLD_FOR_REVIEW (never auto-import broken data).
Also persists the raw extraction payload to `raw_extraction` for GoBD audit trail.

Port of backend_legacy/src/modules/invoice/vision-pipeline/review-gate.service.ts
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import RawExtraction
from app.services.parsing.vision_pipeline.pipeline_types import (
    ConfidenceScore,
    ReviewAction,
    ValidationResult,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_THRESHOLD_AUTO_IMPORT = 0.90
_THRESHOLD_AUTO_FLAG = 0.75
_THRESHOLD_HOLD = 0.50

# GoBD: retain raw extractions for 10 years
_RETAIN_YEARS = 10


class ReviewGate:
    """Stage 6: threshold routing and GoBD audit trail persistence."""

    def decide(
        self, confidence: ConfidenceScore, validation: ValidationResult
    ) -> ReviewAction:
        """Determine routing action.

        Validation errors override confidence thresholds — never auto-import broken data.
        """
        if not validation.valid:
            logger.warning(
                "review_gate_validation_failed",
                errors=validation.errors,
                action=ReviewAction.HOLD_FOR_REVIEW,
            )
            return ReviewAction.HOLD_FOR_REVIEW

        if confidence.overall >= _THRESHOLD_AUTO_IMPORT:
            action = ReviewAction.AUTO_IMPORT
        elif confidence.overall >= _THRESHOLD_AUTO_FLAG:
            action = ReviewAction.AUTO_IMPORT_FLAG
        elif confidence.overall >= _THRESHOLD_HOLD:
            action = ReviewAction.HOLD_FOR_REVIEW
        else:
            action = ReviewAction.REJECT

        logger.info(
            "review_gate_decision",
            confidence=confidence.overall,
            direct_ocr_ratio=confidence.direct_ocr_ratio,
            completeness_ratio=confidence.completeness_ratio,
            action=action,
        )
        return action

    async def persist_raw_extraction(
        self,
        db: AsyncSession,
        *,
        tenant_id: UUID,
        upload_id: UUID,
        payload: dict,
        confidence: float,
        issues: list[str],
    ) -> None:
        """Persist raw extraction payload to `raw_extraction` (GoBD audit trail).

        Non-fatal: a persistence failure must not block the import.
        Caller is responsible for having set the tenant context (SET LOCAL app.current_tenant).
        """
        try:
            retain_until = date.today() + timedelta(days=_RETAIN_YEARS * 365)
            extraction = RawExtraction(
                tenant_id=tenant_id,
                upload_id=upload_id,
                doc_type="invoice",
                extractor="vision-pipeline-v2",
                confidence=confidence,
                payload=payload,
                issues=issues or None,
                normalized=False,
                retain_until=retain_until,
            )
            db.add(extraction)
            await db.flush()
        except Exception as exc:
            logger.error(
                "raw_extraction_persist_failed",
                error=str(exc),
                upload_id=str(upload_id),
            )
