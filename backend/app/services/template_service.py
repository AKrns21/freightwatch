"""Template Service — parsing template management and file-based matching.

Port of backend_legacy/src/modules/parsing/template.service.ts
     + backend_legacy/src/modules/parsing/services/template-matcher.service.ts
Issue: #48

Key features:
- create_from_upload(): create template from upload record with auto-detection
- create_from_mappings(): convenience wrapper for array-format mappings
- update() / delete() / find_all() / find_by_category()
- increment_usage(): update usage_count + last_used_at
- find_match(): score templates against upload file characteristics
- get_statistics(): count by category and most-used list
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ParsingTemplate, Upload

logger = structlog.get_logger(__name__)

_MATCH_CONFIDENCE_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CreateTemplateOptions:
    """Options for creating a parsing template."""

    name: str
    mappings: dict[str, str]
    notes: str | None = None
    detection_rules: dict[str, Any] | None = None


@dataclass
class TemplateMatch:
    """Result of a template-matching attempt."""

    template: ParsingTemplate
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": str(self.template.id),
            "template_name": self.template.name,
            "confidence": self.confidence,
            "reasons": self.reasons,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TemplateService:
    """Parsing template management and file-based template matching.

    Templates are either global (tenant_id IS NULL) or tenant-specific.
    Matching logic scores templates by MIME type, filename pattern,
    and header keywords (confidence threshold: 0.7).

    Example usage:
        svc = TemplateService()
        match = await svc.find_match(db, upload, tenant_id, file_content)
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    # -----------------------------------------------------------------------
    # Create
    # -----------------------------------------------------------------------

    async def create_from_mappings(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        upload_id: UUID,
        mappings: list[dict[str, str]],
        template_name: str,
    ) -> ParsingTemplate:
        """Create a template from a list of ``{field, column}`` dicts.

        Args:
            db: Async DB session with tenant context set.
            tenant_id: Owning tenant.
            upload_id: Source upload (used for filename pattern extraction).
            mappings: List of ``{"field": "dest_zip", "column": "PLZ Empf."}``.
            template_name: Human-readable template name.

        Returns:
            Persisted ParsingTemplate.
        """
        mappings_record: dict[str, str] = {}
        for m in mappings:
            if m.get("field") and m.get("column"):
                mappings_record[m["field"]] = m["column"]

        return await self.create_from_upload(
            db,
            upload_id=upload_id,
            tenant_id=tenant_id,
            options=CreateTemplateOptions(name=template_name, mappings=mappings_record),
        )

    async def create_from_upload(
        self,
        db: AsyncSession,
        upload_id: UUID,
        tenant_id: UUID,
        options: CreateTemplateOptions,
    ) -> ParsingTemplate:
        """Create a template derived from an upload record.

        Extracts filename pattern and header keywords automatically unless
        ``options.detection_rules`` are provided explicitly.

        Args:
            db: Async DB session with tenant context set.
            upload_id: Source upload.
            tenant_id: Owning tenant.
            options: Name, mappings, and optional detection overrides.

        Returns:
            Persisted ParsingTemplate.

        Raises:
            HTTPException(404): Upload not found.
        """
        upload_result = await db.execute(
            select(Upload).where(Upload.id == upload_id, Upload.tenant_id == tenant_id)
        )
        upload = upload_result.scalar_one_or_none()
        if upload is None:
            raise HTTPException(status_code=404, detail=f"Upload {upload_id} not found")

        detection = options.detection_rules or {
            "filename_pattern": self._extract_filename_pattern(upload.filename),
            "header_keywords": self._extract_header_keywords(options.mappings),
            "mime_types": [upload.mime_type] if upload.mime_type else [],
        }

        category = self._detect_category(options.mappings)

        template = ParsingTemplate(
            tenant_id=tenant_id,
            name=options.name,
            description=options.notes,
            file_type=upload.mime_type or "unknown",
            template_category=category,
            detection=detection,
            mappings=options.mappings,
            source="manual",
            usage_count=0,
        )
        db.add(template)
        await db.flush()
        await db.refresh(template)

        self.logger.info(
            "template_created",
            template_id=str(template.id),
            template_name=template.name,
            category=category,
        )
        return template

    # -----------------------------------------------------------------------
    # Update / Delete
    # -----------------------------------------------------------------------

    async def update(
        self,
        db: AsyncSession,
        template_id: UUID,
        tenant_id: UUID,
        updates: dict[str, Any],
    ) -> ParsingTemplate:
        """Update a template's name, mappings, notes, or detection_rules.

        Args:
            db: Async DB session with tenant context set.
            template_id: Template to update.
            tenant_id: Owning tenant.
            updates: Dict with any of: name, mappings, notes, detection_rules.

        Returns:
            Updated ParsingTemplate.

        Raises:
            HTTPException(404): Template not found.
        """
        result = await db.execute(
            select(ParsingTemplate).where(
                ParsingTemplate.id == template_id,
                ParsingTemplate.tenant_id == tenant_id,
                ParsingTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if template is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

        if "name" in updates:
            template.name = updates["name"]
        if "mappings" in updates:
            template.mappings = updates["mappings"]
        if "notes" in updates:
            template.description = updates["notes"]
        if "detection_rules" in updates:
            template.detection = {**(template.detection or {}), **updates["detection_rules"]}

        await db.flush()
        await db.refresh(template)
        return template

    async def delete(
        self,
        db: AsyncSession,
        template_id: UUID,
        tenant_id: UUID,
    ) -> None:
        """Soft-delete a template by setting deleted_at.

        Raises:
            HTTPException(404): Template not found.
        """
        from datetime import datetime

        result = await db.execute(
            select(ParsingTemplate).where(
                ParsingTemplate.id == template_id,
                ParsingTemplate.tenant_id == tenant_id,
                ParsingTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if template is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

        template.deleted_at = datetime.now(tz=UTC)
        await db.flush()

        self.logger.info("template_deleted", template_id=str(template_id))

    # -----------------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------------

    async def find_all(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> list[ParsingTemplate]:
        """Return all active templates (global + tenant-specific).

        Returns:
            List ordered by usage_count DESC, created_at DESC.
        """
        result = await db.execute(
            select(ParsingTemplate)
            .where(
                ParsingTemplate.deleted_at.is_(None),
                (ParsingTemplate.tenant_id == tenant_id)
                | (ParsingTemplate.tenant_id.is_(None)),
            )
            .order_by(ParsingTemplate.usage_count.desc(), ParsingTemplate.created_at.desc())
        )
        return list(result.scalars().all())

    async def find_by_category(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        category: str,
    ) -> list[ParsingTemplate]:
        """Return active templates filtered by category.

        Returns:
            List ordered by usage_count DESC.
        """
        result = await db.execute(
            select(ParsingTemplate)
            .where(
                ParsingTemplate.deleted_at.is_(None),
                ParsingTemplate.template_category == category,
                (ParsingTemplate.tenant_id == tenant_id)
                | (ParsingTemplate.tenant_id.is_(None)),
            )
            .order_by(ParsingTemplate.usage_count.desc())
        )
        return list(result.scalars().all())

    async def increment_usage(
        self,
        db: AsyncSession,
        template_id: UUID,
    ) -> None:
        """Increment usage_count and set last_used_at to now."""
        from datetime import datetime

        await db.execute(
            update(ParsingTemplate)
            .where(ParsingTemplate.id == template_id)
            .values(
                usage_count=ParsingTemplate.usage_count + 1,
                last_used_at=datetime.now(tz=UTC),
            )
        )

    async def clone(
        self,
        db: AsyncSession,
        template_id: UUID,
        tenant_id: UUID,
        new_name: str,
    ) -> ParsingTemplate:
        """Clone a template (any tenant) into the current tenant's namespace.

        Args:
            db: Async DB session with tenant context set.
            template_id: Source template (may be global).
            tenant_id: Owning tenant for the clone.
            new_name: Name for the cloned template.

        Returns:
            New ParsingTemplate.

        Raises:
            HTTPException(404): Source template not found.
        """
        source_result = await db.execute(
            select(ParsingTemplate).where(ParsingTemplate.id == template_id)
        )
        original = source_result.scalar_one_or_none()
        if original is None:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

        cloned = ParsingTemplate(
            tenant_id=tenant_id,
            name=new_name,
            description=f"Cloned from: {original.name}",
            file_type=original.file_type,
            template_category=original.template_category,
            detection={**(original.detection or {})},
            mappings={**(original.mappings or {})},
            source="manual",
            usage_count=0,
        )
        db.add(cloned)
        await db.flush()
        await db.refresh(cloned)

        self.logger.info(
            "template_cloned",
            source_id=str(template_id),
            new_id=str(cloned.id),
        )
        return cloned

    async def get_statistics(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> dict[str, Any]:
        """Return template count by category and the 5 most-used templates.

        Returns:
            Dict with ``total``, ``by_category``, ``most_used`` keys.
        """
        templates = await self.find_all(db, tenant_id)

        by_category: dict[str, int] = {}
        for t in templates:
            cat = t.template_category or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1

        most_used = sorted(
            [t for t in templates if (t.usage_count or 0) > 0],
            key=lambda t: t.usage_count or 0,
            reverse=True,
        )[:5]

        return {
            "total": len(templates),
            "by_category": by_category,
            "most_used": most_used,
        }

    # -----------------------------------------------------------------------
    # Template matching
    # -----------------------------------------------------------------------

    async def find_match(
        self,
        db: AsyncSession,
        upload: Upload,
        tenant_id: UUID,
        file_content: str | None = None,
    ) -> TemplateMatch | None:
        """Find the best matching template for an upload.

        Scores templates by MIME type (30%), filename pattern (20%),
        header keywords (50%), and content patterns (up to 10% bonus).
        Tenant-specific templates receive a 10% confidence boost.
        Frequently-used templates (>10 uses) receive a 5% boost.

        Args:
            db: Async DB session with tenant context set.
            upload: Upload ORM instance.
            tenant_id: Current tenant.
            file_content: Optional raw text content of the file.

        Returns:
            TemplateMatch if confidence ≥ 0.7, else None.
        """
        self.logger.debug(
            "template_match_start",
            upload_id=str(upload.id),
            filename=upload.filename,
        )

        templates = await self._get_applicable_templates(db, tenant_id, upload.mime_type)
        if not templates:
            self.logger.debug("template_match_none_available", upload_id=str(upload.id))
            return None

        characteristics = self._extract_characteristics(upload, file_content)

        scored = [
            (template, self._score_template(template, characteristics))
            for template in templates
        ]
        scored.sort(key=lambda x: x[1]["confidence"], reverse=True)

        best_template, best_score = scored[0]

        if best_score["confidence"] < _MATCH_CONFIDENCE_THRESHOLD:
            self.logger.debug(
                "template_match_low_confidence",
                upload_id=str(upload.id),
                confidence=best_score["confidence"],
            )
            return None

        # Update usage stats
        await self.increment_usage(db, best_template.id)

        self.logger.info(
            "template_match_found",
            upload_id=str(upload.id),
            template_id=str(best_template.id),
            confidence=best_score["confidence"],
        )
        return TemplateMatch(
            template=best_template,
            confidence=best_score["confidence"],
            reasons=best_score["reasons"],
        )

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    async def _get_applicable_templates(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        mime_type: str | None,
    ) -> list[ParsingTemplate]:
        """Load global + tenant templates, then filter by MIME type."""
        result = await db.execute(
            select(ParsingTemplate)
            .where(
                ParsingTemplate.deleted_at.is_(None),
                (ParsingTemplate.tenant_id == tenant_id)
                | (ParsingTemplate.tenant_id.is_(None)),
            )
            .order_by(ParsingTemplate.usage_count.desc())
        )
        templates = list(result.scalars().all())

        if mime_type is None:
            return templates

        return [t for t in templates if self._is_mime_compatible(t, mime_type)]

    def _is_mime_compatible(self, template: ParsingTemplate, mime_type: str) -> bool:
        """Return True if template supports the given MIME type."""
        detection = template.detection or {}
        allowed = detection.get("mime_types")
        if not allowed:
            return True  # No restriction
        for pattern in allowed:
            if "*" in pattern:
                regex = re.compile(pattern.replace("*", ".*"))
                if regex.match(mime_type):
                    return True
            elif mime_type in pattern or pattern in mime_type:
                return True
        return False

    def _extract_characteristics(
        self,
        upload: Upload,
        file_content: str | None,
    ) -> dict[str, Any]:
        """Extract filename, mime_type, headers, and first lines from upload."""
        chars: dict[str, Any] = {
            "filename": upload.filename,
            "mimeType": upload.mime_type,
        }
        if file_content:
            lines = file_content.split("\n")[:10]
            if lines and ("," in lines[0] or ";" in lines[0]):
                sep = ";" if ";" in lines[0] else ","
                chars["headers"] = [h.strip() for h in lines[0].split(sep)]
            chars["firstLines"] = lines
        return chars

    def _score_template(
        self,
        template: ParsingTemplate,
        characteristics: dict[str, Any],
    ) -> dict[str, Any]:
        """Return confidence (0–1) and reasons list for a template match."""
        score = 0.0
        reasons: list[str] = []
        detection = template.detection or {}
        filename: str = characteristics.get("filename", "")
        mime_type: str | None = characteristics.get("mimeType")
        headers: list[str] = characteristics.get("headers", [])
        first_lines: list[str] = characteristics.get("firstLines", [])

        # MIME type check (30%)
        allowed_mimes = detection.get("mime_types", [])
        if mime_type and allowed_mimes and mime_type in allowed_mimes:
            score += 0.3
            reasons.append("MIME type match")

        # Filename pattern check (20%)
        filename_pattern = detection.get("filename_pattern")
        if filename_pattern:
            try:
                if re.search(filename_pattern, filename, re.IGNORECASE):
                    score += 0.2
                    reasons.append("Filename pattern match")
            except re.error:
                self.logger.warning(
                    "invalid_filename_pattern",
                    template_id=str(template.id),
                    pattern=filename_pattern,
                )

        # Header keywords check (50%)
        kws: list[str] = detection.get("header_keywords", [])
        if kws and headers:
            matched = [
                kw for kw in kws
                if any(kw.lower() in h.lower() for h in headers)
            ]
            header_score = (len(matched) / len(kws)) * 0.5
            score += header_score
            if matched:
                reasons.append(f"{len(matched)}/{len(kws)} header keywords matched")

        # Content patterns (bonus up to 10%)
        content_patterns: list[str] = detection.get("content_patterns", [])
        if content_patterns and first_lines:
            content_text = "\n".join(first_lines)
            matched_patterns = 0
            for pattern in content_patterns:
                try:
                    if re.search(pattern, content_text, re.IGNORECASE):
                        matched_patterns += 1
                except re.error:
                    pass
            if matched_patterns:
                score += (matched_patterns / len(content_patterns)) * 0.1
                reasons.append(f"{matched_patterns} content patterns matched")

        # Tenant-specific boost (10%)
        if template.tenant_id is not None:
            score = min(1.0, score * 1.1)
            reasons.append("Tenant-specific template")

        # Frequently-used boost (5%)
        if (template.usage_count or 0) > 10:
            score = min(1.0, score * 1.05)
            reasons.append("Frequently used template")

        return {"confidence": min(score, 1.0), "reasons": reasons}

    def _extract_filename_pattern(self, filename: str) -> str:
        """Generate a regex pattern from a concrete filename."""
        pattern = re.sub(r"\d{4}-\d{2}-\d{2}", r"\\d{4}-\\d{2}-\\d{2}", filename)
        pattern = re.sub(r"\d{8}", r"\\d{8}", pattern)
        pattern = re.sub(r"\d{6}", r"\\d{6}", pattern)
        pattern = re.sub(r"\d+", r"\\d+", pattern)
        pattern = re.sub(r"\s+", r"\\s*", pattern)
        return pattern

    def _extract_header_keywords(self, mappings: dict[str, str]) -> list[str]:
        """Use mapping source column names as header keywords."""
        keywords = [k.lower().strip() for k in mappings.keys()]
        return list(dict.fromkeys(keywords))  # deduplicate, preserve order

    def _detect_category(self, mappings: dict[str, str]) -> str:
        """Infer template category from mapping target fields."""
        fields = [v.lower() for v in mappings.values()]

        if any("invoice" in f or "line" in f for f in fields):
            return "invoice"
        if any(f in ("zone", "weight_band", "price") for f in fields):
            return "tariff"
        if (
            any("origin" in f for f in fields)
            and any("dest" in f for f in fields)
            and any("weight" in f for f in fields)
        ):
            return "shipment_list"
        return "unknown"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_template_service: TemplateService | None = None


def get_template_service() -> TemplateService:
    global _template_service
    if _template_service is None:
        _template_service = TemplateService()
    return _template_service
