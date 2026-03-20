"""SQLAlchemy 2.0 ORM models for all FreightWatch database tables.

Column names and types mirror the Supabase schema exactly.
All tenant-scoped tables carry a tenant_id column (RLS enforced at DB level).

Table inventory (40 tables):
  Core:     tenant, carrier, carrier_alias, upload, fx_rate, users
  Project:  project, consultant_note, report
  Tariff:   tariff_table, tariff_rate, tariff_zone_map, tariff_nebenkosten,
            tariff_surcharge, tariff_special_condition, tariff_ftl_rate,
            diesel_floater, maut_table, maut_rate, maut_zone_map,
            lsva_table, lsva_rate, city_surcharge
  Invoice:  invoice_header, invoice_line, invoice_dispute_event
  Shipment: shipment, shipment_benchmark
  Fleet:    vehicle, fleet_cost_profile, own_tour, own_tour_stop,
            fleet_vehicle, fleet_driver
  Parsing:  parsing_template, manual_mapping, raw_extraction, extraction_correction
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Re-export Base so callers can do `from app.models.database import Base`
__all__ = ["Base"]


# ============================================================================
# 1. CORE / STAMMDATEN
# ============================================================================


class Tenant(Base):
    """Multi-tenant root. RLS: tenant.id = app.current_tenant."""

    __tablename__ = "tenant"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Added by migration 010
    data_retention_years: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10")
    )
    # Added by migration 014
    freight_delta_threshold_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("5.0")
    )

    # Relationships
    projects: Mapped[list["Project"]] = relationship(back_populates="tenant")
    uploads: Mapped[list["Upload"]] = relationship(back_populates="tenant")


class Carrier(Base):
    """Global carrier master data. No RLS — not tenant-scoped."""

    __tablename__ = "carrier"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code_norm: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    country: Mapped[str | None] = mapped_column(String(2))
    conversion_rules: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    # Added by migration 007
    billing_type_map: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class CarrierAlias(Base):
    """Maps raw carrier name strings → carrier.id, scoped per tenant."""

    __tablename__ = "carrier_alias"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenant.id"), primary_key=True, nullable=False
    )
    alias_text: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    carrier_id: Mapped[UUID] = mapped_column(ForeignKey("carrier.id"), nullable=False)


class Upload(Base):
    """Uploaded source files (tariff sheets, invoices, CSV exports).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "upload"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    source_type: Mapped[str | None] = mapped_column(String(50))
    storage_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(50), server_default=text("'pending'"))
    parse_method: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    llm_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parse_errors: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Added by migration 005
    raw_text_hash: Mapped[str | None] = mapped_column(String(64))
    suggested_mappings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reviewed_by: Mapped[UUID | None] = mapped_column()
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parsing_issues: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Added by migration 015
    doc_type: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (UniqueConstraint("tenant_id", "file_hash"),)

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="uploads")
    project: Mapped["Project | None"] = relationship(back_populates="uploads")


class FxRate(Base):
    """Historical FX rates. No RLS — global reference data."""

    __tablename__ = "fx_rate"

    rate_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    from_ccy: Mapped[str] = mapped_column(String(3), primary_key=True, nullable=False)
    to_ccy: Mapped[str] = mapped_column(String(3), primary_key=True, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str | None] = mapped_column(Text)


class User(Base):
    """Application users, scoped to a tenant.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    roles: Mapped[list[str] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


# ============================================================================
# 2. PROJECT SYSTEM
# ============================================================================


class Project(Base):
    """Consultant project — groups uploads, shipments, and reports.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "project"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    phase: Mapped[str | None] = mapped_column(String(50), server_default=text("'quick_check'"))
    status: Mapped[str | None] = mapped_column(String(50), server_default=text("'draft'"))
    consultant_id: Mapped[UUID | None] = mapped_column()
    project_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    tenant: Mapped["Tenant"] = relationship(back_populates="projects")
    uploads: Mapped[list["Upload"]] = relationship(back_populates="project")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="project")
    consultant_notes: Mapped[list["ConsultantNote"]] = relationship(back_populates="project")
    reports: Mapped[list["Report"]] = relationship(back_populates="project")


class ConsultantNote(Base):
    """Quality issues and observations per project.

    RLS via project FK (project.tenant_id = app.current_tenant).
    """

    __tablename__ = "consultant_note"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    note_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    related_upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("upload.id"))
    related_shipment_id: Mapped[UUID | None] = mapped_column(ForeignKey("shipment.id"))
    priority: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str | None] = mapped_column(String(50), server_default=text("'open'"))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project"] = relationship(back_populates="consultant_notes")


class Report(Base):
    """Generated analysis reports per project.

    RLS via project FK (project.tenant_id = app.current_tenant).
    """

    __tablename__ = "report"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    shipment_count: Mapped[int | None] = mapped_column(Integer)
    date_range_start: Mapped[date | None] = mapped_column(Date)
    date_range_end: Mapped[date | None] = mapped_column(Date)
    generated_by: Mapped[UUID] = mapped_column(nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (UniqueConstraint("project_id", "version"),)

    project: Mapped["Project"] = relationship(back_populates="reports")


# ============================================================================
# 3. TARIFF SYSTEM
# ============================================================================


class TariffTable(Base):
    """Tariff sheet header — one row per Tarifblatt.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "tariff_table"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    carrier_id: Mapped[UUID] = mapped_column(ForeignKey("carrier.id"), nullable=False)
    upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("upload.id"))
    name: Mapped[str | None] = mapped_column(String(255))
    service_type: Mapped[str | None] = mapped_column(String(100))
    lane_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tariff_country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'EUR'"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    origin_info: Mapped[str | None] = mapped_column(String(255))
    delivery_condition: Mapped[str | None] = mapped_column(String(50))
    maut_included: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)
    # Added by migration 004
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    source_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Added by migration 010
    dest_country_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    # Relationships
    rates: Mapped[list["TariffRate"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    zone_maps: Mapped[list["TariffZoneMap"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    nebenkosten: Mapped[list["TariffNebenkosten"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    surcharges: Mapped[list["TariffSurcharge"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    special_conditions: Mapped[list["TariffSpecialCondition"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    ftl_rates: Mapped[list["TariffFtlRate"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    maut_tables: Mapped[list["MautTable"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    lsva_tables: Mapped[list["LsvaTable"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )
    city_surcharges: Mapped[list["CitySurcharge"]] = relationship(
        back_populates="tariff_table", cascade="all, delete-orphan"
    )


class TariffRate(Base):
    """Price matrix row: zone × weight band → price.

    No tenant_id — inherits tenant scope via tariff_table.
    """

    __tablename__ = "tariff_rate"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_from_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    weight_to_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rate_per_shipment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    rate_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    tariff_table: Mapped["TariffTable"] = relationship(back_populates="rates")


class TariffZoneMap(Base):
    """PLZ prefix → zone number mapping per carrier tariff."""

    __tablename__ = "tariff_zone_map"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    plz_prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    match_type: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'prefix'")
    )
    zone: Mapped[int] = mapped_column(Integer, nullable=False)

    tariff_table: Mapped["TariffTable"] = relationship(back_populates="zone_maps")


class TariffNebenkosten(Base):
    """Structured surcharges/conditions block per tariff sheet."""

    __tablename__ = "tariff_nebenkosten"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False
    )
    # Diesel
    diesel_floater_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    eu_mobility_surcharge_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # Weight minimums
    min_weight_pallet_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_weight_cbm_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_weight_ldm_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_weight_small_format_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_weight_medium_format_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    min_weight_large_format_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # Pallet exchange
    pallet_exchange_euro_flat: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    pallet_exchange_euro_mesh: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    pallet_exchange_note: Mapped[str | None] = mapped_column(Text)
    # Miscellaneous
    return_pickup_note: Mapped[str | None] = mapped_column(Text)
    transport_insurance: Mapped[str | None] = mapped_column(Text)
    hazmat_surcharge: Mapped[str | None] = mapped_column(Text)
    liability_surcharge: Mapped[str | None] = mapped_column(Text)
    oversize_note: Mapped[str | None] = mapped_column(Text)
    island_trade_fair_surcharge: Mapped[str | None] = mapped_column(Text)
    legal_basis: Mapped[str | None] = mapped_column(Text)
    # Payment
    payment_terms: Mapped[str | None] = mapped_column(Text)
    payment_days: Mapped[int | None] = mapped_column(Integer)
    raw_items: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    tariff_table: Mapped["TariffTable"] = relationship(back_populates="nebenkosten")


class TariffSurcharge(Base):
    """Flexible catch-all surcharges per tariff (migration 007).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "tariff_surcharge"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    surcharge_type: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    notes: Mapped[str | None] = mapped_column(Text)

    tariff_table: Mapped["TariffTable"] = relationship(
        "TariffTable", back_populates="surcharges", foreign_keys=[tariff_id]
    )


class TariffSpecialCondition(Base):
    """Sonderkonditionen / Vereinbarungspreise per tariff (migration 008).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "tariff_special_condition"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    condition_type: Mapped[str] = mapped_column(Text, nullable=False)
    dest_zip_prefix: Mapped[str | None] = mapped_column(Text)
    weight_from_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    weight_to_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)

    tariff_table: Mapped["TariffTable"] = relationship(
        "TariffTable", back_populates="special_conditions", foreign_keys=[tariff_id]
    )


class TariffFtlRate(Base):
    """FTL/Charter rates (per_km, per_day, flat_tour) (migration 009).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "tariff_ftl_rate"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    rate_basis: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_type: Mapped[str | None] = mapped_column(Text)
    dest_region: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    notes: Mapped[str | None] = mapped_column(Text)

    tariff_table: Mapped["TariffTable"] = relationship(
        "TariffTable", back_populates="ftl_rates", foreign_keys=[tariff_id]
    )


class DieselFloater(Base):
    """Time-series diesel surcharge % per carrier.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "diesel_floater"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    carrier_id: Mapped[UUID] = mapped_column(ForeignKey("carrier.id"), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    floater_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    basis: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'base'")
    )
    source: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (UniqueConstraint("tenant_id", "carrier_id", "valid_from"),)


class DieselPriceBracket(Base):
    """Carrier-specific lookup table: diesel price (ct/liter) → surcharge %.

    For a given shipment date, look up the Destatis reference price (2-month lag),
    find the matching bracket (highest price_ct_max that is >= reference price),
    and apply floater_pct.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "diesel_price_bracket"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    carrier_id: Mapped[UUID] = mapped_column(ForeignKey("carrier.id"), nullable=False)
    price_ct_max: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    floater_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    basis: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'base'"))
    valid_from: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("'2000-01-01'")
    )
    valid_until: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("tenant_id", "carrier_id", "price_ct_max", "valid_from"),
    )


class DestatisDieselPrice(Base):
    """Cached monthly diesel reference prices fetched from Destatis GENESIS.

    Global table (no tenant scope) — same Destatis price applies to all tenants.
    """

    __tablename__ = "destatis_diesel_price"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    price_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_ct: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    series_code: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'61243-0001'")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("price_year", "price_month", "series_code"),
    )


class MautTable(Base):
    """Maut tariff header (distance-range based)."""

    __tablename__ = "maut_table"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(255))
    weight_from_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    weight_limit_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    minimum_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))

    tariff_table: Mapped["TariffTable"] = relationship(back_populates="maut_tables")
    rates: Mapped[list["MautRate"]] = relationship(
        back_populates="maut_table", cascade="all, delete-orphan"
    )
    zone_maps: Mapped[list["MautZoneMap"]] = relationship(
        back_populates="maut_table", cascade="all, delete-orphan"
    )


class MautRate(Base):
    """Maut rate row: weight band × distance range → price."""

    __tablename__ = "maut_rate"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    maut_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("maut_table.id", ondelete="CASCADE"), nullable=False
    )
    weight_from_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    weight_to_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    distance_range: Mapped[str] = mapped_column(String(30), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    maut_table: Mapped["MautTable"] = relationship(back_populates="rates")


class MautZoneMap(Base):
    """Maps PLZ prefix → Maut distance zone."""

    __tablename__ = "maut_zone_map"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    maut_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("maut_table.id", ondelete="CASCADE"), nullable=False
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    plz_prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    match_type: Mapped[str | None] = mapped_column(String(10), server_default=text("'prefix'"))
    distance_zone: Mapped[str] = mapped_column(String(30), nullable=False)

    maut_table: Mapped["MautTable"] = relationship(back_populates="zone_maps")


class LsvaTable(Base):
    """Swiss LSVA (heavy vehicle levy) tariff header."""

    __tablename__ = "lsva_table"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False
    )
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'CHF'"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    weight_threshold_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    billing_unit_above: Mapped[str | None] = mapped_column(String(50))

    tariff_table: Mapped["TariffTable"] = relationship(back_populates="lsva_tables")
    rates: Mapped[list["LsvaRate"]] = relationship(
        back_populates="lsva_table", cascade="all, delete-orphan"
    )


class LsvaRate(Base):
    """LSVA rate row: zone × weight band → price."""

    __tablename__ = "lsva_rate"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    lsva_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("lsva_table.id", ondelete="CASCADE"), nullable=False
    )
    zone: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_from_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    weight_to_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    lsva_table: Mapped["LsvaTable"] = relationship(back_populates="rates")


class CitySurcharge(Base):
    """City/Großstadt surcharges per tariff."""

    __tablename__ = "city_surcharge"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tariff_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tariff_table.id", ondelete="CASCADE"), nullable=False
    )
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2), server_default=text("'DE'"))
    plz_from: Mapped[str | None] = mapped_column(String(10))
    plz_to: Mapped[str | None] = mapped_column(String(10))
    surcharge_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    surcharge_flat: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(Text)

    tariff_table: Mapped["TariffTable"] = relationship(back_populates="city_surcharges")


# ============================================================================
# 4. INVOICE SYSTEM
# ============================================================================


class InvoiceHeader(Base):
    """Invoice header (Rechnungskopf).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "invoice_header"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"))
    upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("upload.id"))
    carrier_id: Mapped[UUID | None] = mapped_column(ForeignKey("carrier.id"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    print_date: Mapped[date | None] = mapped_column(Date)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_number: Mapped[str | None] = mapped_column(String(50))
    customer_vat_id: Mapped[str | None] = mapped_column(String(50))
    total_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    tax_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    payment_terms: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(50), server_default=text("'pending'"))
    erp_document_number: Mapped[str | None] = mapped_column(String(100))
    erp_creditor_number: Mapped[str | None] = mapped_column(String(50))
    erp_barcode: Mapped[str | None] = mapped_column(String(50))
    source_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    parse_issues: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "carrier_id", "invoice_number", "invoice_date"),
    )

    # Relationships
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLine(Base):
    """Individual line on an invoice (one shipment or surcharge charge).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "invoice_line"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("invoice_header.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    # Billing classification
    la_code: Mapped[str | None] = mapped_column(String(10))
    billing_type: Mapped[str | None] = mapped_column(String(50))
    billing_description: Mapped[str | None] = mapped_column(Text)
    # Added by migration 007
    line_type: Mapped[str | None] = mapped_column(Text, index=True)
    # Shipment identification
    auftragsnummer: Mapped[str | None] = mapped_column(String(50), index=True)
    tour_number: Mapped[str | None] = mapped_column(String(50), index=True)
    referenz: Mapped[str | None] = mapped_column(Text)
    shipment_date: Mapped[date | None] = mapped_column(Date, index=True)
    # Route
    origin_address_raw: Mapped[str | None] = mapped_column(Text)
    origin_zip: Mapped[str | None] = mapped_column(String(10))
    origin_country: Mapped[str | None] = mapped_column(String(2), server_default=text("'DE'"))
    dest_address_raw: Mapped[str | None] = mapped_column(Text)
    dest_zip: Mapped[str | None] = mapped_column(String(10), index=True)
    dest_country: Mapped[str | None] = mapped_column(String(2), server_default=text("'DE'"))
    # Quantities
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    unit: Mapped[str | None] = mapped_column(String(20))
    # Amounts
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    # Matching
    shipment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("shipment.id"), index=True
    )
    match_status: Mapped[str | None] = mapped_column(String(20))
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    # Added by migration 011
    dispute_status: Mapped[str | None] = mapped_column(Text, index=True)
    dispute_note: Mapped[str | None] = mapped_column(Text)
    source_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    invoice: Mapped["InvoiceHeader"] = relationship(back_populates="lines")
    dispute_events: Mapped[list["InvoiceDisputeEvent"]] = relationship(
        back_populates="invoice_line"
    )


class InvoiceDisputeEvent(Base):
    """Dispute workflow audit trail (migration 011).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "invoice_dispute_event"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    invoice_line_id: Mapped[UUID] = mapped_column(
        ForeignKey("invoice_line.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount_claimed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    amount_recovered: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    invoice_line: Mapped["InvoiceLine"] = relationship(back_populates="dispute_events")


# ============================================================================
# 5. SHIPMENT SYSTEM
# ============================================================================


class Shipment(Base):
    """Core shipment entity — unified view of what was shipped.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "shipment"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("project.id"), index=True)
    upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("upload.id"))
    carrier_id: Mapped[UUID | None] = mapped_column(ForeignKey("carrier.id"), index=True)
    # Identity
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reference_number: Mapped[str | None] = mapped_column(String(100))
    service_level: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'STANDARD'")
    )
    # Route
    origin_zip: Mapped[str | None] = mapped_column(String(10))
    origin_country: Mapped[str | None] = mapped_column(String(2), server_default=text("'DE'"))
    dest_zip: Mapped[str | None] = mapped_column(String(10), index=True)
    dest_country: Mapped[str | None] = mapped_column(String(2), server_default=text("'DE'"))
    # Dimensions
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    volume_cbm: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    pallets: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    length_m: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pieces: Mapped[int | None] = mapped_column(Integer)
    # Chargeable weight
    chargeable_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    chargeable_basis: Mapped[str | None] = mapped_column(String(20))
    # Actual costs (from invoice)
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    actual_base_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    actual_diesel_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    actual_toll_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    actual_other_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    actual_total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Data quality
    completeness_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    missing_fields: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    data_quality_issues: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Lineage
    source_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_method: Mapped[str | None] = mapped_column(String(50))
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    project: Mapped["Project | None"] = relationship(back_populates="shipments")
    benchmarks: Mapped[list["ShipmentBenchmark"]] = relationship(back_populates="shipment")


class ShipmentBenchmark(Base):
    """Expected vs actual cost comparison per shipment.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "shipment_benchmark"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    shipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("shipment.id"), nullable=False, index=True
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    tariff_table_id: Mapped[UUID | None] = mapped_column(ForeignKey("tariff_table.id"))
    # Zone/weight used
    zone_calculated: Mapped[int | None] = mapped_column(Integer)
    chargeable_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    chargeable_basis: Mapped[str | None] = mapped_column(String(20))
    # Expected costs
    expected_base_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    expected_diesel_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    expected_toll_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    expected_total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Actuals (snapshot)
    actual_total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Delta
    delta_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    delta_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    classification: Mapped[str | None] = mapped_column(String(20))
    # Calculation trace
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    report_currency: Mapped[str | None] = mapped_column(String(3))
    fx_rate_used: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    fx_rate_date: Mapped[date | None] = mapped_column(Date)
    diesel_basis_used: Mapped[str | None] = mapped_column(String(20))
    diesel_pct_used: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    cost_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report_amounts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    calc_version: Mapped[str | None] = mapped_column(String(20))
    calculation_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    shipment: Mapped["Shipment"] = relationship(back_populates="benchmarks")


# ============================================================================
# 6. FLEET SYSTEM
# ============================================================================


class Vehicle(Base):
    """Telemetry vehicle (plate, type). Legacy own-fleet table.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "vehicle"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    vehicle_type: Mapped[str | None] = mapped_column(String(100))
    plate_number: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (UniqueConstraint("tenant_id", "plate_number"),)


class FleetCostProfile(Base):
    """Cost profile (€/km, €/h) per vehicle over time.

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "fleet_cost_profile"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False)
    vehicle_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicle.id"))
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    euro_per_km: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    euro_per_hour_drive: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    euro_per_hour_idle: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    fixed_monthly_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("tenant_id", "vehicle_id", "valid_from"),)


class OwnTour(Base):
    """Own-fleet tour (renamed from route_trip in migration 006).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "own_tour"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    upload_id: Mapped[UUID | None] = mapped_column(ForeignKey("upload.id"))
    vehicle_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicle.id"), index=True)
    trip_date: Mapped[date] = mapped_column(Date, nullable=False)
    departure_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Aggregated metrics
    total_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_drive_min: Mapped[int | None] = mapped_column(Integer)
    total_idle_min: Mapped[int | None] = mapped_column(Integer)
    stop_count: Mapped[int | None] = mapped_column(Integer)
    base_address: Mapped[str | None] = mapped_column(Text)
    # Calculated costs
    cost_km: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost_time: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost_per_stop: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    # Added by migration 013
    tour_id: Mapped[str | None] = mapped_column(Text)
    driver_id: Mapped[UUID | None] = mapped_column(ForeignKey("fleet_driver.id"))
    depot_zip: Mapped[str | None] = mapped_column(String(5))
    total_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    stops: Mapped[list["OwnTourStop"]] = relationship(
        back_populates="tour", cascade="all, delete-orphan"
    )


class OwnTourStop(Base):
    """Individual stop within an own-fleet tour (renamed from route_stop in migration 006)."""

    __tablename__ = "own_tour_stop"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    # FK column renamed from trip_id to tour_id in migration 006
    tour_id: Mapped[UUID] = mapped_column(
        ForeignKey("own_tour.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stop_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Addresses
    departure_address: Mapped[str | None] = mapped_column(Text)
    departure_locality: Mapped[str | None] = mapped_column(String(100))
    arrival_address: Mapped[str | None] = mapped_column(Text)
    arrival_locality: Mapped[str | None] = mapped_column(String(100))
    departure_zip: Mapped[str | None] = mapped_column(String(10))
    arrival_zip: Mapped[str | None] = mapped_column(String(10))
    # Timing
    departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drive_min: Mapped[int | None] = mapped_column(Integer)
    idle_before_min: Mapped[int | None] = mapped_column(Integer)
    idle_after_min: Mapped[int | None] = mapped_column(Integer)
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    is_delivery: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Added by migration 013
    shipment_ref: Mapped[str | None] = mapped_column(Text, index=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    packages: Mapped[int | None] = mapped_column(Integer)

    tour: Mapped["OwnTour"] = relationship(back_populates="stops")


class FleetVehicle(Base):
    """Cost-model vehicle for own-fleet benchmark (migration 013).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "fleet_vehicle"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    license_plate: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_type: Mapped[str | None] = mapped_column(Text)
    payload_kg: Mapped[int | None] = mapped_column(Integer)
    fixed_cost_per_day: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    variable_cost_per_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (UniqueConstraint("tenant_id", "license_plate"),)


class FleetDriver(Base):
    """Driver hourly rate for own-fleet cost model (migration 013).

    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "fleet_driver"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    currency: Mapped[str | None] = mapped_column(String(3), server_default=text("'EUR'"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


# ============================================================================
# 7. PARSING / TEMPLATE SYSTEM
# ============================================================================


class ParsingTemplate(Base):
    """LLM/rule-based parsing templates per carrier/format.

    RLS: tenant_id IS NULL (global) OR tenant_id = app.current_tenant.
    """

    __tablename__ = "parsing_template"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    template_category: Mapped[str | None] = mapped_column(String(50))
    detection: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mappings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), server_default=text("'manual'"))
    usage_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManualMapping(Base):
    """Human-reviewed field-level mapping corrections."""

    __tablename__ = "manual_mapping"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    upload_id: Mapped[UUID] = mapped_column(ForeignKey("upload.id"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_column: Mapped[str | None] = mapped_column(String(100))
    mapping_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RawExtraction(Base):
    """Raw LLM/parser output before normalization (migration 010).

    GoBD: retain_until must never be set in the past.
    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "raw_extraction"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    upload_id: Mapped[UUID] = mapped_column(ForeignKey("upload.id"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    extractor: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    issues: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    normalized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), index=True
    )
    normalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retain_until: Mapped[date] = mapped_column(Date, nullable=False)


class ExtractionCorrection(Base):
    """Field-level OCR corrections by consultant (migration 012).

    GoBD audit trail.
    RLS: tenant_id = app.current_tenant.
    """

    __tablename__ = "extraction_correction"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    upload_id: Mapped[UUID] = mapped_column(ForeignKey("upload.id"), nullable=False, index=True)
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by: Mapped[UUID | None] = mapped_column()
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
