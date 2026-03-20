"""Carrier Service — alias resolution and billing_type_map management.

Port of backend_legacy/src/modules/upload/entities/carrier-alias.entity.ts
     + carrier management logic
Issue: #48, #56

Key features:
- resolve_carrier_id(): maps raw alias_text → carrier.id (exact, tenant-scoped)
- resolve_carrier_id_with_fallback(): 4-step fallback chain (issue #56)
  1. Exact alias match
  2. Legal-suffix strip (GmbH, AG, SE, …) then exact alias match
  3. Fuzzy match: Levenshtein distance ≤ 2 against all tenant aliases
  4. LLM classification via Claude Haiku
  Successful non-exact matches are auto-saved as new aliases.
- create_alias() / delete_alias() / list_aliases(): alias CRUD
- get_carrier_by_code(): look up carrier by normalized code
- update_billing_type_map(): update carrier JSONB billing_type → line_type map
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import anthropic
import structlog
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import Carrier, CarrierAlias

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers for fallback chain (issue #56)
# ---------------------------------------------------------------------------

_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(?:gmbh|ag|se|ltd|limited|inc|s\.a\.|sa|bv|nv|llc|co\.|co|kg|ohg|ug|plc)\s*$",
    re.IGNORECASE,
)

_LLM_CARRIER_PROMPT = """\
You are matching a raw carrier name from a freight invoice to the closest known carrier.

Raw name: {raw_name}

Known carriers:
{carrier_list}

Reply with a single JSON object only:
{{"match": "<exact carrier name from the list above or null if no reasonable match>"}}\
"""


def _strip_legal_suffix(text: str) -> str:
    """Remove trailing legal-form suffixes (GmbH, AG, Ltd, …)."""
    return _LEGAL_SUFFIX_RE.sub("", text).strip()


def _levenshtein(a: str, b: str) -> int:
    """Classic DP Levenshtein edit distance."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,          # deletion
                curr[j - 1] + 1,      # insertion
                prev[j - 1] + (ca != cb),  # substitution
            )
        prev = curr
    return prev[-1]


@dataclass
class CarrierResolutionResult:
    """Outcome of resolve_carrier_id_with_fallback()."""

    carrier_id: UUID
    method: str  # "exact" | "suffix_strip" | "fuzzy" | "llm"
    confidence: str  # "high" | "medium" | "low"
    new_alias_saved: bool = False


class CarrierService:
    """Carrier alias resolution and billing type management.

    CarrierAlias rows map free-text carrier names (as they appear on invoices)
    to canonical Carrier IDs. They are tenant-scoped so different tenants can
    map the same alias to different carriers.

    Example usage:
        svc = CarrierService()
        carrier_id = await svc.resolve_carrier_id(db, "DHL Freight GmbH", tenant_id)
    """

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)

    # -----------------------------------------------------------------------
    # Alias resolution
    # -----------------------------------------------------------------------

    async def resolve_carrier_id(
        self,
        db: AsyncSession,
        alias_text: str,
        tenant_id: UUID,
    ) -> UUID | None:
        """Resolve a raw carrier name string to a carrier UUID.

        Looks up tenant-specific aliases first; falls back to global aliases
        (tenant_id IS NULL) if no tenant match is found.

        Args:
            db: Async DB session with tenant context set.
            alias_text: Raw carrier name from invoice/CSV.
            tenant_id: Current tenant.

        Returns:
            Carrier UUID if found, else None.
        """
        normalized = alias_text.strip().lower()

        # Tenant-specific alias first
        result = await db.execute(
            select(CarrierAlias.carrier_id).where(
                CarrierAlias.tenant_id == tenant_id,
                CarrierAlias.alias_text == normalized,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            self.logger.debug(
                "carrier_alias_resolved",
                alias_text=alias_text,
                carrier_id=str(row),
                scope="tenant",
            )
            return row

        self.logger.debug(
            "carrier_alias_not_found",
            alias_text=alias_text,
            tenant_id=str(tenant_id),
        )
        return None

    async def _exact_alias_lookup(
        self,
        db: AsyncSession,
        normalized: str,
        tenant_id: UUID,
    ) -> UUID | None:
        """Exact alias lookup: tenant-specific first, then global (tenant_id IS NULL)."""
        result = await db.execute(
            select(CarrierAlias.carrier_id).where(
                CarrierAlias.tenant_id == tenant_id,
                CarrierAlias.alias_text == normalized,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row  # type: ignore[return-value]

        result = await db.execute(
            select(CarrierAlias.carrier_id).where(
                CarrierAlias.tenant_id.is_(None),
                CarrierAlias.alias_text == normalized,
            )
        )
        return result.scalar_one_or_none()  # type: ignore[return-value]

    async def resolve_carrier_id_with_fallback(
        self,
        db: AsyncSession,
        alias_text: str,
        tenant_id: UUID,
    ) -> CarrierResolutionResult | None:
        """Resolve a raw carrier name to a carrier UUID via a 4-step fallback chain.

        Steps (short-circuits on first match):
          1. Exact alias match (calls resolve_carrier_id).
          2. Strip trailing legal suffix (GmbH, AG, …) then exact alias match.
          3. Fuzzy match: Levenshtein ≤ 2 against all known tenant aliases.
          4. LLM classification via Claude Haiku (only if ANTHROPIC_API_KEY set).

        Successful matches from steps 2–4 are auto-saved as new tenant aliases
        so subsequent uploads resolve on step 1.

        Args:
            db: Async DB session with tenant context set.
            alias_text: Raw carrier name from invoice/CSV.
            tenant_id: Current tenant.

        Returns:
            CarrierResolutionResult if a carrier was found, else None.
        """
        normalized = alias_text.strip().lower()

        # Step 1 — exact alias match (tenant-specific then global)
        carrier_id = await self._exact_alias_lookup(db, normalized, tenant_id)
        if carrier_id is not None:
            return CarrierResolutionResult(
                carrier_id=carrier_id, method="exact", confidence="high"
            )

        # Step 2 — strip legal suffix, then exact alias match
        stripped = _strip_legal_suffix(normalized)
        if stripped and stripped != normalized:
            carrier_id = await self._exact_alias_lookup(db, stripped, tenant_id)
            if carrier_id is not None:
                await self.upsert_alias(db, tenant_id, normalized, carrier_id)
                self.logger.info(
                    "carrier_resolved_suffix_strip",
                    raw=alias_text,
                    stripped=stripped,
                    carrier_id=str(carrier_id),
                )
                return CarrierResolutionResult(
                    carrier_id=carrier_id,
                    method="suffix_strip",
                    confidence="high",
                    new_alias_saved=True,
                )

        # Step 3 — fuzzy match: Levenshtein ≤ 2 against all tenant aliases
        aliases_result = await db.execute(
            select(CarrierAlias.alias_text, CarrierAlias.carrier_id).where(
                CarrierAlias.tenant_id == tenant_id,
            )
        )
        all_aliases = aliases_result.all()  # list of (alias_text, carrier_id) rows

        best_dist = 3  # threshold is ≤ 2
        best_carrier_id: UUID | None = None
        best_alias: str | None = None
        for row in all_aliases:
            dist = _levenshtein(normalized, row.alias_text)
            if dist < best_dist:
                best_dist = dist
                best_carrier_id = row.carrier_id
                best_alias = row.alias_text

        if best_carrier_id is not None:
            await self.upsert_alias(db, tenant_id, normalized, best_carrier_id)
            self.logger.info(
                "carrier_resolved_fuzzy",
                raw=alias_text,
                matched_alias=best_alias,
                distance=best_dist,
                carrier_id=str(best_carrier_id),
            )
            return CarrierResolutionResult(
                carrier_id=best_carrier_id,
                method="fuzzy",
                confidence="medium",
                new_alias_saved=True,
            )

        # Step 4 — LLM classification (only when API key is set)
        if not settings.anthropic_api_key:
            return None

        carrier_id = await self._resolve_via_llm(db, alias_text, tenant_id)
        if carrier_id is not None:
            await self.upsert_alias(db, tenant_id, normalized, carrier_id)
            self.logger.info(
                "carrier_resolved_llm",
                raw=alias_text,
                carrier_id=str(carrier_id),
            )
            return CarrierResolutionResult(
                carrier_id=carrier_id,
                method="llm",
                confidence="low",
                new_alias_saved=True,
            )

        return None

    async def _resolve_via_llm(
        self,
        db: AsyncSession,
        alias_text: str,
        tenant_id: UUID,
    ) -> UUID | None:
        """Claude Haiku step — map alias_text to a known carrier name, then resolve.

        Fetches all carrier names from the DB, asks Haiku for the best match,
        then resolves the matched name through the normal alias path.

        Returns:
            Carrier UUID if the LLM returned a valid match, else None.
        """
        carriers = await self.list_carriers(db)
        if not carriers:
            return None

        carrier_names = [c.name for c in carriers]
        prompt = _LLM_CARRIER_PROMPT.format(
            raw_name=alias_text,
            carrier_list="\n".join(f"- {n}" for n in carrier_names),
        )
        try:
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=64,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text if response.content else ""
            raw = re.sub(r"```json\n?|```", "", raw).strip()
            parsed: dict = json.loads(raw)
            matched_name: str | None = parsed.get("match")
        except Exception as exc:
            self.logger.warning(
                "carrier_llm_resolution_failed", alias_text=alias_text, error=str(exc)
            )
            return None

        if not matched_name:
            return None

        # Look up the matched carrier name in the carriers list
        matched_name_lower = matched_name.strip().lower()
        for carrier in carriers:
            if carrier.name.lower() == matched_name_lower:
                return carrier.id

        return None

    # -----------------------------------------------------------------------
    # Alias CRUD
    # -----------------------------------------------------------------------

    async def create_alias(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        alias_text: str,
        carrier_id: UUID,
    ) -> CarrierAlias:
        """Create a tenant-scoped carrier alias.

        Args:
            db: Async DB session with tenant context set.
            tenant_id: Owning tenant.
            alias_text: Raw name to map (stored lowercase-stripped).
            carrier_id: Target carrier UUID.

        Returns:
            Persisted CarrierAlias instance.

        Raises:
            HTTPException(409): Alias already exists for this tenant.
        """
        normalized = alias_text.strip().lower()

        # Check duplicate
        existing = await db.execute(
            select(CarrierAlias).where(
                CarrierAlias.tenant_id == tenant_id,
                CarrierAlias.alias_text == normalized,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Alias '{normalized}' already exists for this tenant",
            )

        alias = CarrierAlias(
            tenant_id=tenant_id,
            alias_text=normalized,
            carrier_id=carrier_id,
        )
        db.add(alias)
        await db.flush()

        self.logger.info(
            "carrier_alias_created",
            tenant_id=str(tenant_id),
            alias_text=normalized,
            carrier_id=str(carrier_id),
        )
        return alias

    async def upsert_alias(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        alias_text: str,
        carrier_id: UUID,
    ) -> CarrierAlias:
        """Create or update a tenant-scoped carrier alias.

        Uses PostgreSQL INSERT … ON CONFLICT DO UPDATE so this is safe
        to call repeatedly.

        Returns:
            The current (inserted or updated) CarrierAlias row.
        """
        normalized = alias_text.strip().lower()

        stmt = (
            insert(CarrierAlias)
            .values(tenant_id=tenant_id, alias_text=normalized, carrier_id=carrier_id)
            .on_conflict_do_update(
                index_elements=["tenant_id", "alias_text"],
                set_={"carrier_id": carrier_id},
            )
        )
        await db.execute(stmt)

        # Re-fetch the row
        result = await db.execute(
            select(CarrierAlias).where(
                CarrierAlias.tenant_id == tenant_id,
                CarrierAlias.alias_text == normalized,
            )
        )
        alias = result.scalar_one()
        self.logger.info(
            "carrier_alias_upserted",
            tenant_id=str(tenant_id),
            alias_text=normalized,
            carrier_id=str(carrier_id),
        )
        return alias

    async def delete_alias(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        alias_text: str,
    ) -> None:
        """Delete a tenant-scoped alias.

        Args:
            db: Async DB session with tenant context set.
            tenant_id: Owning tenant.
            alias_text: Raw alias text to delete.

        Raises:
            HTTPException(404): Alias not found.
        """
        normalized = alias_text.strip().lower()
        result = await db.execute(
            delete(CarrierAlias).where(
                CarrierAlias.tenant_id == tenant_id,
                CarrierAlias.alias_text == normalized,
            )
        )
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Alias '{normalized}' not found for this tenant",
            )
        self.logger.info(
            "carrier_alias_deleted",
            tenant_id=str(tenant_id),
            alias_text=normalized,
        )

    async def list_aliases(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> list[CarrierAlias]:
        """Return all aliases for a tenant.

        Returns:
            List of CarrierAlias rows sorted by alias_text.
        """
        result = await db.execute(
            select(CarrierAlias)
            .where(CarrierAlias.tenant_id == tenant_id)
            .order_by(CarrierAlias.alias_text)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Carrier lookup
    # -----------------------------------------------------------------------

    async def get_carrier_by_code(
        self,
        db: AsyncSession,
        code_norm: str,
    ) -> Carrier | None:
        """Look up a carrier by its normalised code (global, no tenant scope).

        Args:
            db: Async DB session.
            code_norm: Normalised carrier code (e.g. 'dhl', 'ups').

        Returns:
            Carrier ORM instance or None.
        """
        result = await db.execute(
            select(Carrier).where(Carrier.code_norm == code_norm.lower().strip())
        )
        return result.scalar_one_or_none()

    async def list_carriers(self, db: AsyncSession) -> list[Carrier]:
        """Return all carriers (global reference data).

        Returns:
            List of Carrier rows sorted by name.
        """
        result = await db.execute(select(Carrier).order_by(Carrier.name))
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # billing_type_map management
    # -----------------------------------------------------------------------

    async def update_billing_type_map(
        self,
        db: AsyncSession,
        carrier_id: UUID,
        billing_type_map: dict[str, str],
    ) -> Carrier:
        """Replace the billing_type_map JSONB on a carrier.

        The billing_type_map stores a mapping of ``billing_type`` codes
        (as they appear on invoices) to normalised ``line_type`` values used
        in the benchmark engine.

        Args:
            db: Async DB session.
            carrier_id: Carrier to update (global, no tenant scope).
            billing_type_map: Full replacement dict
                e.g. ``{"FRT": "freight", "FUEL": "diesel"}``.

        Returns:
            Updated Carrier instance.

        Raises:
            HTTPException(404): Carrier not found.
        """
        result = await db.execute(
            select(Carrier).where(Carrier.id == carrier_id)
        )
        carrier = result.scalar_one_or_none()
        if carrier is None:
            raise HTTPException(status_code=404, detail=f"Carrier {carrier_id} not found")

        carrier.billing_type_map = billing_type_map
        await db.flush()

        self.logger.info(
            "billing_type_map_updated",
            carrier_id=str(carrier_id),
            keys=list(billing_type_map.keys()),
        )
        return carrier

    async def get_billing_type_map(
        self,
        db: AsyncSession,
        carrier_id: UUID,
    ) -> dict[str, Any]:
        """Return the billing_type_map for a carrier.

        Returns:
            Dict (may be empty if not configured).

        Raises:
            HTTPException(404): Carrier not found.
        """
        result = await db.execute(
            select(Carrier.billing_type_map).where(Carrier.id == carrier_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Carrier {carrier_id} not found")
        return row or {}

    async def resolve_line_type(
        self,
        db: AsyncSession,
        carrier_id: UUID,
        billing_type: str,
    ) -> str | None:
        """Translate a raw billing_type code to a normalised line_type.

        Args:
            db: Async DB session.
            carrier_id: Carrier whose map to use.
            billing_type: Raw code from invoice line.

        Returns:
            Normalised line_type string, or None if not mapped.
        """
        btm = await self.get_billing_type_map(db, carrier_id)
        return btm.get(billing_type)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_carrier_service: CarrierService | None = None


def get_carrier_service() -> CarrierService:
    global _carrier_service
    if _carrier_service is None:
        _carrier_service = CarrierService()
    return _carrier_service
