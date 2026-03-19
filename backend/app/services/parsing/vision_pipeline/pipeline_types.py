"""Shared types for the 6-stage vision parsing pipeline (Issue #42).

Port of backend_legacy/src/modules/invoice/vision-pipeline/pipeline.types.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Literal, TypeVar

# ─── Stage 2: Page classification ────────────────────────────────────────────

PageType = Literal["cover", "line-item-table", "surcharge-appendix", "continuation"]

VALID_PAGE_TYPES: tuple[PageType, ...] = (
    "cover",
    "line-item-table",
    "surcharge-appendix",
    "continuation",
)


@dataclass
class ClassifiedPage:
    page_number: int          # 0-indexed
    page_type: PageType
    image_base64: str         # processed PNG, base64-encoded
    width: int
    height: int


# ─── Stage 3: Structured extraction ──────────────────────────────────────────

FieldSource = Literal["direct_ocr", "llm_inferred", "missing"]

T = TypeVar("T")


@dataclass
class AnnotatedField(Generic[T]):
    """A single extracted value with its source annotation."""

    value: T
    src: FieldSource


@dataclass
class ExtractedHeader:
    invoice_number: AnnotatedField[str | None]
    invoice_date: AnnotatedField[str | None]       # YYYY-MM-DD
    carrier_name: AnnotatedField[str | None]
    customer_name: AnnotatedField[str | None]
    customer_number: AnnotatedField[str | None]
    total_net_amount: AnnotatedField[float | None]
    total_gross_amount: AnnotatedField[float | None]
    currency: AnnotatedField[str | None]


@dataclass
class ExtractedLine:
    shipment_date: AnnotatedField[str | None]      # YYYY-MM-DD
    shipment_reference: AnnotatedField[str | None]
    tour: AnnotatedField[str | None]
    origin_zip: AnnotatedField[str | None]
    origin_country: AnnotatedField[str | None]
    dest_zip: AnnotatedField[str | None]
    dest_country: AnnotatedField[str | None]
    weight_kg: AnnotatedField[float | None]
    unit_price: AnnotatedField[float | None]
    line_total: AnnotatedField[float | None]
    billing_type: AnnotatedField[str | None]


@dataclass
class PageExtractionResult:
    page_number: int
    page_type: PageType
    header: ExtractedHeader | None = None
    lines: list[ExtractedLine] = field(default_factory=list)
    raw_issues: list[str] = field(default_factory=list)


# ─── Stage 4: Cross-document validation ──────────────────────────────────────

@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]    # blockers
    warnings: list[str]  # non-fatal


# ─── Stage 5: Confidence scoring ─────────────────────────────────────────────

@dataclass
class ConfidenceScore:
    overall: float                          # 0.0 – 1.0
    direct_ocr_ratio: float                 # fraction of fields tagged direct_ocr
    completeness_ratio: float               # fraction of required fields present
    field_breakdown: dict[str, FieldSource] # per-field breakdown for review UI


# ─── Stage 6: Review gate ─────────────────────────────────────────────────────

class ReviewAction(str, Enum):
    AUTO_IMPORT = "auto_import"
    AUTO_IMPORT_FLAG = "auto_import_flag"
    HOLD_FOR_REVIEW = "hold_for_review"
    REJECT = "reject"


# ─── Pipeline result ──────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    header: ExtractedHeader
    lines: list[ExtractedLine]
    confidence: ConfidenceScore
    validation: ValidationResult
    review_action: ReviewAction
    all_issues: list[str]
