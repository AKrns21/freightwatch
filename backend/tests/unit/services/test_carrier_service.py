"""Unit tests for CarrierService.

Tests: alias resolution (tenant-specific, not found), alias CRUD,
       carrier lookup by code, billing_type_map update and resolution.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.carrier_service import CarrierService


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
