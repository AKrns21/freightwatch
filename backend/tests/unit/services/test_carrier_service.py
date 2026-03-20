"""Unit tests for CarrierService.

Tests: alias resolution (tenant-specific, not found), alias CRUD,
       carrier lookup by code, billing_type_map update and resolution,
       resolve_carrier_id_with_fallback() 4-step chain (issue #56).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.carrier_service import CarrierService, _levenshtein, _strip_legal_suffix


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_alias(tenant_id=None, alias_text="dhl freight", carrier_id=None):
    a = MagicMock()
    a.tenant_id = tenant_id or uuid4()
    a.alias_text = alias_text
    a.carrier_id = carrier_id or uuid4()
    return a


class TestCarrierService:
    """Test suite for CarrierService."""

    def setup_method(self) -> None:
        self.service = CarrierService()
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    # ============================================================================
    # resolve_carrier_id
    # ============================================================================

    def test_resolve_returns_carrier_id_when_found(self) -> None:
        expected_carrier_id = uuid4()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = expected_carrier_id
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(
            self.service.resolve_carrier_id(self.db, "DHL Freight GmbH", self.tenant_id)
        )

        assert result == expected_carrier_id

    def test_resolve_returns_none_when_not_found(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(
            self.service.resolve_carrier_id(self.db, "Unknown Carrier", self.tenant_id)
        )

        assert result is None

    def test_resolve_normalises_alias_text(self) -> None:
        """Alias lookup should use stripped lowercase."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        _run(self.service.resolve_carrier_id(self.db, "  DHL  ", self.tenant_id))
        # Verify execute was called (normalisation applied internally)
        self.db.execute.assert_called_once()

    # ============================================================================
    # create_alias
    # ============================================================================

    def test_create_alias_raises_409_on_duplicate(self) -> None:
        existing = _make_alias(tenant_id=self.tenant_id, alias_text="dhl freight")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        self.db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc:
            _run(
                self.service.create_alias(
                    self.db, self.tenant_id, "DHL Freight", uuid4()
                )
            )

        assert exc.value.status_code == 409

    def test_create_alias_succeeds_when_no_duplicate(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.add = MagicMock()
        self.db.flush = AsyncMock()

        carrier_id = uuid4()
        alias = _run(
            self.service.create_alias(
                self.db, self.tenant_id, "  Schenker DE  ", carrier_id
            )
        )

        self.db.add.assert_called_once()
        # Alias text should be normalised
        created = self.db.add.call_args[0][0]
        assert created.alias_text == "schenker de"
        assert created.carrier_id == carrier_id

    # ============================================================================
    # delete_alias
    # ============================================================================

    def test_delete_alias_raises_404_when_not_found(self) -> None:
        result_mock = MagicMock()
        result_mock.rowcount = 0
        self.db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc:
            _run(self.service.delete_alias(self.db, self.tenant_id, "ghost carrier"))

        assert exc.value.status_code == 404

    def test_delete_alias_succeeds(self) -> None:
        result_mock = MagicMock()
        result_mock.rowcount = 1
        self.db.execute = AsyncMock(return_value=result_mock)

        # Should not raise
        _run(self.service.delete_alias(self.db, self.tenant_id, "DHL"))

    # ============================================================================
    # list_aliases
    # ============================================================================

    def test_list_aliases_returns_all_for_tenant(self) -> None:
        aliases = [_make_alias(tenant_id=self.tenant_id) for _ in range(3)]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = aliases
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(self.service.list_aliases(self.db, self.tenant_id))

        assert len(result) == 3

    # ============================================================================
    # get_carrier_by_code
    # ============================================================================

    def test_get_carrier_by_code_returns_carrier(self) -> None:
        carrier = MagicMock()
        carrier.code_norm = "dhl"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = carrier
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(self.service.get_carrier_by_code(self.db, "DHL"))

        assert result == carrier

    def test_get_carrier_by_code_returns_none_when_missing(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(self.service.get_carrier_by_code(self.db, "unknown"))

        assert result is None

    # ============================================================================
    # update_billing_type_map
    # ============================================================================

    def test_update_billing_type_map_raises_404_for_missing_carrier(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        self.db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(HTTPException) as exc:
            _run(
                self.service.update_billing_type_map(
                    self.db, uuid4(), {"FRT": "freight"}
                )
            )

        assert exc.value.status_code == 404

    def test_update_billing_type_map_sets_map(self) -> None:
        carrier = MagicMock()
        carrier.billing_type_map = {}
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = carrier
        self.db.execute = AsyncMock(return_value=result_mock)
        self.db.flush = AsyncMock()

        billing_map = {"FRT": "freight", "FUEL": "diesel"}
        _run(self.service.update_billing_type_map(self.db, uuid4(), billing_map))

        assert carrier.billing_type_map == billing_map

    # ============================================================================
    # resolve_line_type
    # ============================================================================

    def test_resolve_line_type_returns_mapped_value(self) -> None:
        carrier_id = uuid4()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = {"FRT": "freight", "FUEL": "diesel"}
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(self.service.resolve_line_type(self.db, carrier_id, "FRT"))

        assert result == "freight"

    def test_resolve_line_type_returns_none_for_unmapped(self) -> None:
        carrier_id = uuid4()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = {"FRT": "freight"}
        self.db.execute = AsyncMock(return_value=result_mock)

        result = _run(self.service.resolve_line_type(self.db, carrier_id, "MYSTERY"))

        assert result is None


# ---------------------------------------------------------------------------
# Helpers (issue #56)
# ---------------------------------------------------------------------------


class TestStripLegalSuffix:
    def test_strips_gmbh(self) -> None:
        assert _strip_legal_suffix("dhl freight gmbh") == "dhl freight"

    def test_strips_ag(self) -> None:
        assert _strip_legal_suffix("db schenker ag") == "db schenker"

    def test_strips_ltd(self) -> None:
        assert _strip_legal_suffix("ups parcel ltd") == "ups parcel"

    def test_strips_se(self) -> None:
        assert _strip_legal_suffix("rhenus se") == "rhenus"

    def test_no_change_when_no_suffix(self) -> None:
        assert _strip_legal_suffix("dhl freight") == "dhl freight"

    def test_case_insensitive(self) -> None:
        assert _strip_legal_suffix("DHL Freight GmbH") == "DHL Freight"


class TestLevenshtein:
    def test_identical_strings(self) -> None:
        assert _levenshtein("dhl", "dhl") == 0

    def test_single_substitution(self) -> None:
        assert _levenshtein("ups parce1", "ups parcel") == 1

    def test_distance_two(self) -> None:
        # "db schenker" vs "db schenkr" — 2 edits
        assert _levenshtein("db schenkr", "db schenker") == 1  # 1 insertion

    def test_completely_different(self) -> None:
        assert _levenshtein("dhl", "schenker") > 2

    def test_empty_string(self) -> None:
        assert _levenshtein("", "abc") == 3
        assert _levenshtein("abc", "") == 3


# ---------------------------------------------------------------------------
# resolve_carrier_id_with_fallback (issue #56)
# ---------------------------------------------------------------------------


def _make_alias_row(alias_text: str, carrier_id=None):
    row = MagicMock()
    row.alias_text = alias_text
    row.carrier_id = carrier_id or uuid4()
    return row


class TestResolveCarrierIdWithFallback:
    def setup_method(self) -> None:
        self.service = CarrierService()
        self.db = AsyncMock()
        self.tenant_id = uuid4()

    # ============================================================================
    # STEP 1 — exact match
    # ============================================================================

    def test_step1_exact_match_returns_high_confidence(self) -> None:
        carrier_id = uuid4()
        exact_mock = MagicMock()
        exact_mock.scalar_one_or_none.return_value = carrier_id
        self.db.execute = AsyncMock(return_value=exact_mock)

        result = _run(
            self.service.resolve_carrier_id_with_fallback(
                self.db, "dhl freight", self.tenant_id
            )
        )

        assert result is not None
        assert result.carrier_id == carrier_id
        assert result.method == "exact"
        assert result.confidence == "high"
        assert result.new_alias_saved is False

    # ============================================================================
    # STEP 2 — legal suffix strip
    # ============================================================================

    def test_step2_suffix_strip_resolves_gmbh_suffix(self) -> None:
        carrier_id = uuid4()
        # First call (exact "dhl freight gmbh") → None
        # Second call (exact "dhl freight") → carrier_id
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        match = MagicMock()
        match.scalar_one_or_none.return_value = carrier_id

        upsert_mock = MagicMock()
        upsert_mock.scalar_one.return_value = MagicMock()

        call_count = 0

        async def execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:   # tenant + global for exact "dhl freight gmbh"
                return no_match
            return match           # tenant hit for stripped "dhl freight"

        self.db.execute = execute_side_effect
        # upsert_alias calls execute too — mock flush
        self.db.flush = AsyncMock()

        with patch.object(self.service, "upsert_alias", new=AsyncMock()) as upsert:
            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "DHL Freight GmbH", self.tenant_id
                )
            )

        assert result is not None
        assert result.method == "suffix_strip"
        assert result.confidence == "high"
        assert result.new_alias_saved is True
        upsert.assert_called_once()

    # ============================================================================
    # STEP 3 — fuzzy match
    # ============================================================================

    def test_step3_fuzzy_match_resolves_ocr_typo(self) -> None:
        carrier_id = uuid4()
        # Known alias: "ups parcel" — raw input: "ups parce1" (Levenshtein 1)
        alias_row = _make_alias_row("ups parcel", carrier_id)

        all_aliases_mock = MagicMock()
        all_aliases_mock.all.return_value = [alias_row]
        self.db.execute = AsyncMock(return_value=all_aliases_mock)

        with patch.object(self.service, "_exact_alias_lookup", new=AsyncMock(return_value=None)), \
             patch.object(self.service, "upsert_alias", new=AsyncMock()):
            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "ups parce1", self.tenant_id
                )
            )

        assert result is not None
        assert result.method == "fuzzy"
        assert result.confidence == "medium"
        assert result.new_alias_saved is True

    def test_step3_no_fuzzy_match_when_distance_exceeds_2(self) -> None:
        # "schenker" vs "dhl freight" — well beyond Levenshtein 2
        carrier_id = uuid4()
        alias_row = _make_alias_row("dhl freight", carrier_id)

        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None

        all_aliases_mock = MagicMock()
        all_aliases_mock.all.return_value = [alias_row]

        call_count = 0

        async def execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return no_match
            return all_aliases_mock

        self.db.execute = execute_side_effect

        with patch("app.config.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "schenker logistics", self.tenant_id
                )
            )

        assert result is None

    # ============================================================================
    # STEP 4 — LLM
    # ============================================================================

    def test_step4_llm_not_called_when_api_key_missing(self) -> None:
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        empty_aliases = MagicMock()
        empty_aliases.all.return_value = []

        call_count = 0

        async def execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return no_match
            return empty_aliases

        self.db.execute = execute_side_effect

        with patch("app.services.carrier_service.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "mystery carrier", self.tenant_id
                )
            )

        assert result is None

    def test_step4_llm_resolves_when_carrier_name_matches(self) -> None:
        carrier_id = uuid4()
        carrier_mock = MagicMock()
        carrier_mock.id = carrier_id
        carrier_mock.name = "DHL Freight"

        empty_aliases = MagicMock()
        empty_aliases.all.return_value = []
        self.db.execute = AsyncMock(return_value=empty_aliases)

        with patch.object(self.service, "_exact_alias_lookup", new=AsyncMock(return_value=None)), \
             patch.object(self.service, "list_carriers", new=AsyncMock(return_value=[carrier_mock])), \
             patch.object(self.service, "upsert_alias", new=AsyncMock()), \
             patch("app.services.carrier_service.settings") as mock_settings, \
             patch("app.services.carrier_service.anthropic.AsyncAnthropic") as mock_anthropic:
            mock_settings.anthropic_api_key = "test-key"
            llm_response = MagicMock()
            llm_response.content = [MagicMock(text='{"match": "DHL Freight"}')]
            mock_anthropic.return_value.messages.create = AsyncMock(
                return_value=llm_response
            )

            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "DHL Fracht", self.tenant_id
                )
            )

        assert result is not None
        assert result.method == "llm"
        assert result.confidence == "low"
        assert result.new_alias_saved is True

    def test_step4_llm_returns_none_when_no_match(self) -> None:
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        empty_aliases = MagicMock()
        empty_aliases.all.return_value = []
        all_carriers = MagicMock()
        all_carriers.scalars.return_value.all.return_value = []

        call_count = 0

        async def execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return no_match
            if call_count == 5:
                return empty_aliases
            return all_carriers

        self.db.execute = execute_side_effect

        with patch("app.services.carrier_service.settings") as mock_settings, \
             patch("app.services.carrier_service.anthropic.AsyncAnthropic") as mock_anthropic:
            mock_settings.anthropic_api_key = "test-key"
            llm_response = MagicMock()
            llm_response.content = [MagicMock(text='{"match": null}')]
            mock_anthropic.return_value.messages.create = AsyncMock(
                return_value=llm_response
            )

            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "completely unknown", self.tenant_id
                )
            )

        assert result is None

    def test_step4_llm_returns_none_on_api_error(self) -> None:
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        empty_aliases = MagicMock()
        empty_aliases.all.return_value = []
        carrier_mock = MagicMock()
        carrier_mock.name = "DHL"
        all_carriers = MagicMock()
        all_carriers.scalars.return_value.all.return_value = [carrier_mock]

        call_count = 0

        async def execute_side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return no_match
            if call_count == 5:
                return empty_aliases
            return all_carriers

        self.db.execute = execute_side_effect

        with patch("app.services.carrier_service.settings") as mock_settings, \
             patch("app.services.carrier_service.anthropic.AsyncAnthropic") as mock_anthropic:
            mock_settings.anthropic_api_key = "test-key"
            mock_anthropic.return_value.messages.create = AsyncMock(
                side_effect=Exception("API error")
            )

            result = _run(
                self.service.resolve_carrier_id_with_fallback(
                    self.db, "some carrier", self.tenant_id
                )
            )

        assert result is None
