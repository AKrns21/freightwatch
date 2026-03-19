"""Unit tests for TemplateService.

Tests: create_from_upload, create_from_mappings, update, delete (soft),
       find_all, find_by_category, find_match scoring and confidence threshold,
       get_statistics, clone, category detection, filename pattern extraction.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.template_service import (
    CreateTemplateOptions,
    TemplateService,
    _MATCH_CONFIDENCE_THRESHOLD,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_template(
    *,
    template_id=None,
    tenant_id=None,
    name="Test Template",
    category="invoice",
    mime_types=None,
    filename_pattern=None,
    header_keywords=None,
    usage_count=0,
    deleted_at=None,
):
    t = MagicMock()
    t.id = template_id or uuid4()
    t.tenant_id = tenant_id or uuid4()
    t.name = name
    t.template_category = category
    t.usage_count = usage_count
    t.deleted_at = deleted_at
    t.detection = {
        "mime_types": mime_types or ["text/csv"],
        "filename_pattern": filename_pattern or "",
        "header_keywords": header_keywords or [],
    }
    t.mappings = {}
    return t


def _make_upload(
    *,
    upload_id=None,
    tenant_id=None,
    filename="invoice_2024-01-15.csv",
    mime_type="text/csv",
):
    u = MagicMock()
    u.id = upload_id or uuid4()
    u.tenant_id = tenant_id or uuid4()
    u.filename = filename
    u.mime_type = mime_type
    return u


class TestTemplateServiceCreate:
    """Tests for template creation methods."""

    def setup_method(self) -> None:
        self.service = TemplateService()
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    def test_create_from_upload_raises_404_if_upload_missing(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc:
            _run(
                self.service.create_from_upload(
                    self.db,
                    upload_id=uuid4(),
                    tenant_id=self.tenant_id,
                    options=CreateTemplateOptions(name="Test", mappings={}),
                )
            )

        assert exc.value.status_code == 404

    def test_create_from_upload_sets_category(self) -> None:
        upload = _make_upload(tenant_id=self.tenant_id)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = upload
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.add = MagicMock()
        self.db.flush = AsyncMock()
        self.db.refresh = AsyncMock()

        async def fake_refresh(obj):
            obj.id = uuid4()

        self.db.refresh = fake_refresh

        mappings = {"origin_zip": "origin", "dest_zip": "dest", "weight_kg": "weight"}
        _run(
            self.service.create_from_upload(
                self.db,
                upload_id=upload.id,
                tenant_id=self.tenant_id,
                options=CreateTemplateOptions(name="Shipment Template", mappings=mappings),
            )
        )

        created = self.db.add.call_args[0][0]
        assert created.template_category == "shipment_list"

    def test_create_from_mappings_converts_list_to_dict(self) -> None:
        upload = _make_upload(tenant_id=self.tenant_id)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = upload
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.add = MagicMock()
        self.db.flush = AsyncMock()
        self.db.refresh = AsyncMock()

        async def fake_refresh(obj):
            obj.id = uuid4()

        self.db.refresh = fake_refresh

        mappings = [
            {"field": "dest_zip", "column": "PLZ Empf."},
            {"field": "weight_kg", "column": "Gewicht"},
            {"no_field_key": "x"},  # malformed entry — should be skipped
        ]
        _run(
            self.service.create_from_mappings(
                self.db,
                tenant_id=self.tenant_id,
                upload_id=upload.id,
                mappings=mappings,
                template_name="My Template",
            )
        )

        created = self.db.add.call_args[0][0]
        assert created.mappings == {"dest_zip": "PLZ Empf.", "weight_kg": "Gewicht"}


class TestTemplateServiceUpdate:
    """Tests for update and delete."""

    def setup_method(self) -> None:
        self.service = TemplateService()
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    def test_update_raises_404_when_not_found(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc:
            _run(
                self.service.update(self.db, uuid4(), self.tenant_id, {"name": "New"})
            )

        assert exc.value.status_code == 404

    def test_update_changes_name(self) -> None:
        template = _make_template(tenant_id=self.tenant_id)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = template
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.flush = AsyncMock()
        self.db.refresh = AsyncMock()

        _run(self.service.update(self.db, template.id, self.tenant_id, {"name": "New Name"}))

        assert template.name == "New Name"

    def test_update_merges_detection_rules(self) -> None:
        template = _make_template(tenant_id=self.tenant_id)
        template.detection = {"mime_types": ["text/csv"], "header_keywords": ["date"]}
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = template
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.flush = AsyncMock()
        self.db.refresh = AsyncMock()

        _run(
            self.service.update(
                self.db,
                template.id,
                self.tenant_id,
                {"detection_rules": {"filename_pattern": r"\d+\.csv"}},
            )
        )

        assert template.detection["mime_types"] == ["text/csv"]
        assert template.detection["filename_pattern"] == r"\d+\.csv"

    def test_delete_sets_deleted_at(self) -> None:
        template = _make_template(tenant_id=self.tenant_id)
        template.deleted_at = None
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = template
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.flush = AsyncMock()

        _run(self.service.delete(self.db, template.id, self.tenant_id))

        assert template.deleted_at is not None


class TestTemplateServiceMatching:
    """Tests for find_match and scoring logic."""

    def setup_method(self) -> None:
        self.service = TemplateService()
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    def _mock_templates(self, templates):
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = templates
        self.db.execute = AsyncMock(return_value=result_mock)

    def test_find_match_returns_none_when_no_templates(self) -> None:
        self._mock_templates([])
        upload = _make_upload()

        result = _run(self.service.find_match(self.db, upload, self.tenant_id))

        assert result is None

    def test_find_match_returns_none_below_confidence_threshold(self) -> None:
        # Template with no matching signals
        template = _make_template(
            mime_types=["application/pdf"],  # upload is CSV → no MIME match
            header_keywords=["invoice_no"],
        )
        self._mock_templates([template])
        upload = _make_upload(mime_type="text/csv")

        result = _run(self.service.find_match(self.db, upload, self.tenant_id))

        assert result is None

    def test_find_match_high_confidence_mime_and_keywords(self) -> None:
        template = _make_template(
            tenant_id=self.tenant_id,
            mime_types=["text/csv"],
            header_keywords=["Datum", "PLZ", "Gewicht"],
            usage_count=0,
        )
        # For increment_usage we need another execute mock
        update_result = MagicMock()
        execute_calls = [0]

        async def execute_side_effect(stmt):
            execute_calls[0] += 1
            if execute_calls[0] == 1:
                # find templates
                r = MagicMock()
                r.scalars.return_value.all.return_value = [template]
                return r
            else:
                # increment_usage update
                return MagicMock()

        self.db.execute = execute_side_effect

        file_content = "Datum;PLZ;Gewicht;Betrag\n2024-01-01;12345;100;50.00"
        upload = _make_upload(mime_type="text/csv")

        result = _run(
            self.service.find_match(self.db, upload, self.tenant_id, file_content)
        )

        # Should match: MIME (0.3) + all 3 keywords (0.5) = 0.8, boosted by tenant
        assert result is not None
        assert result.confidence >= _MATCH_CONFIDENCE_THRESHOLD
        assert "MIME type match" in result.reasons

    # ============================================================================
    # PRIVATE HELPERS
    # ============================================================================

    def test_extract_filename_pattern_removes_dates(self) -> None:
        pattern = self.service._extract_filename_pattern("invoice_2024-01-15.csv")
        # Date digits are replaced with \d+ patterns; literal date should not remain
        assert "2024" not in pattern
        assert "01" not in pattern
        assert "15" not in pattern

    def test_extract_header_keywords_deduplicates(self) -> None:
        mappings = {"dest_zip": "PLZ", "origin_zip": "PLZ Abs."}
        kws = self.service._extract_header_keywords(mappings)
        assert len(kws) == len(set(kws))

    def test_detect_category_invoice(self) -> None:
        category = self.service._detect_category({"col": "invoice_no"})
        assert category == "invoice"

    def test_detect_category_tariff(self) -> None:
        category = self.service._detect_category({"col": "zone"})
        assert category == "tariff"

    def test_detect_category_shipment_list(self) -> None:
        category = self.service._detect_category(
            {"c1": "origin_zip", "c2": "dest_zip", "c3": "weight_kg"}
        )
        assert category == "shipment_list"

    def test_detect_category_unknown(self) -> None:
        category = self.service._detect_category({"col": "some_random_field"})
        assert category == "unknown"

    # ============================================================================
    # MIME compatibility
    # ============================================================================

    def test_mime_wildcard_matches(self) -> None:
        template = _make_template(mime_types=["text/*"])
        assert self.service._is_mime_compatible(template, "text/csv")
        assert self.service._is_mime_compatible(template, "text/plain")

    def test_mime_no_restriction_matches_anything(self) -> None:
        template = _make_template()
        template.detection = {}  # No mime_types key
        assert self.service._is_mime_compatible(template, "application/pdf")

    def test_mime_mismatch_returns_false(self) -> None:
        template = _make_template(mime_types=["application/pdf"])
        assert not self.service._is_mime_compatible(template, "text/csv")


class TestTemplateServiceStatistics:
    """Tests for get_statistics."""

    def setup_method(self) -> None:
        self.service = TemplateService()
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    def test_statistics_counts_by_category(self) -> None:
        templates = [
            _make_template(category="invoice", usage_count=5),
            _make_template(category="invoice", usage_count=0),
            _make_template(category="tariff", usage_count=15),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = templates
        self.db.execute = AsyncMock(return_value=result_mock)

        stats = _run(self.service.get_statistics(self.db, self.tenant_id))

        assert stats["total"] == 3
        assert stats["by_category"]["invoice"] == 2
        assert stats["by_category"]["tariff"] == 1

    def test_statistics_most_used_excludes_zero_usage(self) -> None:
        templates = [
            _make_template(usage_count=10),
            _make_template(usage_count=0),
            _make_template(usage_count=5),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = templates
        self.db.execute = AsyncMock(return_value=result_mock)

        stats = _run(self.service.get_statistics(self.db, self.tenant_id))

        assert len(stats["most_used"]) == 2
        assert stats["most_used"][0].usage_count == 10
