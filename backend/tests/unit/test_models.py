"""Unit tests: ORM model instantiation and basic serialization.

No database required — tests that models can be constructed and their
attributes are accessible without errors.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.database import (
    Base,
    Carrier,
    CitySurcharge,
    ConsultantNote,
    DieselFloater,
    ExtractionCorrection,
    FleetCostProfile,
    FleetDriver,
    FleetVehicle,
    FxRate,
    InvoiceDisputeEvent,
    InvoiceHeader,
    InvoiceLine,
    LsvaRate,
    LsvaTable,
    ManualMapping,
    MautRate,
    MautTable,
    MautZoneMap,
    OwnTour,
    OwnTourStop,
    ParsingTemplate,
    Project,
    RawExtraction,
    Report,
    Shipment,
    ShipmentBenchmark,
    TariffFtlRate,
    TariffNebenkosten,
    TariffRate,
    TariffSpecialCondition,
    TariffSurcharge,
    TariffTable,
    TariffZoneMap,
    Tenant,
    Upload,
    User,
    Vehicle,
)


TENANT_ID = uuid.uuid4()
CARRIER_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
UPLOAD_ID = uuid.uuid4()
TARIFF_ID = uuid.uuid4()


class TestCoreModels:
    def test_tenant_instantiation(self):
        t = Tenant(name="Test GmbH")
        assert t.name == "Test GmbH"
        assert t.__tablename__ == "tenant"

    def test_carrier_instantiation(self):
        c = Carrier(name="Cosi Stahllogistik GmbH", code_norm="COSI", country="DE")
        assert c.code_norm == "COSI"
        assert c.__tablename__ == "carrier"

    def test_upload_instantiation(self):
        u = Upload(
            tenant_id=TENANT_ID,
            filename="invoice.pdf",
            file_hash="abc123" * 10,
            status="pending",
        )
        assert u.status == "pending"
        assert u.__tablename__ == "upload"

    def test_fx_rate_instantiation(self):
        fx = FxRate(
            rate_date=date(2024, 1, 1),
            from_ccy="EUR",
            to_ccy="CHF",
            rate=Decimal("0.9850"),
        )
        assert fx.from_ccy == "EUR"
        assert fx.rate == Decimal("0.9850")
        assert fx.__tablename__ == "fx_rate"

    def test_user_instantiation(self):
        u = User(
            email="consultant@test.de",
            password_hash="$2b$12$hashed",
            tenant_id=TENANT_ID,
        )
        assert u.email == "consultant@test.de"
        assert u.__tablename__ == "users"


class TestProjectModels:
    def test_project_instantiation(self):
        p = Project(tenant_id=TENANT_ID, name="Mecu Quick Check 2024")
        assert p.name == "Mecu Quick Check 2024"
        assert p.__tablename__ == "project"
        # metadata is exposed as project_metadata (reserved word workaround)
        assert hasattr(p, "project_metadata")

    def test_consultant_note_instantiation(self):
        n = ConsultantNote(
            project_id=PROJECT_ID,
            note_type="data_quality",
            content="Weight field missing on 5 lines",
            created_by=uuid.uuid4(),
        )
        assert n.note_type == "data_quality"
        assert n.__tablename__ == "consultant_note"

    def test_report_instantiation(self):
        r = Report(
            project_id=PROJECT_ID,
            version=1,
            report_type="benchmark_summary",
            data_snapshot={"total_delta_eur": 1250.00},
            generated_by=uuid.uuid4(),
        )
        assert r.version == 1
        assert r.__tablename__ == "report"


class TestTariffModels:
    def test_tariff_table_instantiation(self):
        tt = TariffTable(
            tenant_id=TENANT_ID,
            carrier_id=CARRIER_ID,
            lane_type="DE",
            currency="EUR",
            valid_from=date(2024, 1, 1),
        )
        assert tt.lane_type == "DE"
        assert tt.__tablename__ == "tariff_table"

    def test_tariff_rate_instantiation(self):
        tr = TariffRate(
            tariff_table_id=TARIFF_ID,
            zone=3,
            weight_from_kg=Decimal("0"),
            weight_to_kg=Decimal("50"),
            rate_per_shipment=Decimal("12.50"),
        )
        assert tr.zone == 3
        assert tr.__tablename__ == "tariff_rate"

    def test_tariff_zone_map_instantiation(self):
        zm = TariffZoneMap(
            tariff_table_id=TARIFF_ID,
            country_code="DE",
            plz_prefix="42",
            zone=4,
        )
        assert zm.plz_prefix == "42"
        assert zm.__tablename__ == "tariff_zone_map"

    def test_diesel_floater_instantiation(self):
        df = DieselFloater(
            tenant_id=TENANT_ID,
            carrier_id=CARRIER_ID,
            valid_from=date(2024, 1, 1),
            floater_pct=Decimal("18.5"),
            basis="base",
        )
        assert df.floater_pct == Decimal("18.5")
        assert df.__tablename__ == "diesel_floater"

    def test_tariff_surcharge_instantiation(self):
        ts = TariffSurcharge(
            tariff_id=TARIFF_ID,
            tenant_id=TENANT_ID,
            surcharge_type="avis",
            basis="per_shipment",
            value=Decimal("3.50"),
        )
        assert ts.surcharge_type == "avis"
        assert ts.__tablename__ == "tariff_surcharge"

    def test_tariff_special_condition_instantiation(self):
        sc = TariffSpecialCondition(
            tariff_id=TARIFF_ID,
            tenant_id=TENANT_ID,
            condition_type="flat_tour",
            value=Decimal("530.00"),
            valid_from=date(2024, 1, 1),
        )
        assert sc.condition_type == "flat_tour"
        assert sc.__tablename__ == "tariff_special_condition"

    def test_tariff_ftl_rate_instantiation(self):
        fr = TariffFtlRate(
            tariff_id=TARIFF_ID,
            tenant_id=TENANT_ID,
            rate_basis="per_km",
            price=Decimal("1.85"),
        )
        assert fr.rate_basis == "per_km"
        assert fr.__tablename__ == "tariff_ftl_rate"

    def test_maut_structures(self):
        mt = MautTable(tariff_table_id=TARIFF_ID, label="Maut bis 3t")
        mr = MautRate(
            maut_table_id=uuid.uuid4(),
            weight_from_kg=Decimal("0"),
            weight_to_kg=Decimal("3000"),
            distance_range="001-100 km",
            rate=Decimal("5.50"),
        )
        mz = MautZoneMap(
            maut_table_id=uuid.uuid4(),
            country_code="DE",
            plz_prefix="42",
            distance_zone="001-100 km",
        )
        assert mt.__tablename__ == "maut_table"
        assert mr.__tablename__ == "maut_rate"
        assert mz.__tablename__ == "maut_zone_map"

    def test_city_surcharge_instantiation(self):
        cs = CitySurcharge(tariff_table_id=TARIFF_ID, city="München", surcharge_pct=Decimal("5"))
        assert cs.city == "München"
        assert cs.__tablename__ == "city_surcharge"


class TestInvoiceModels:
    def test_invoice_header_instantiation(self):
        ih = InvoiceHeader(
            tenant_id=TENANT_ID,
            invoice_number="117261",
            invoice_date=date(2024, 3, 15),
            total_gross=Decimal("12450.80"),
            currency="EUR",
        )
        assert ih.invoice_number == "117261"
        assert ih.__tablename__ == "invoice_header"

    def test_invoice_line_instantiation(self):
        il = InvoiceLine(
            tenant_id=TENANT_ID,
            invoice_id=uuid.uuid4(),
            dest_zip="35463",
            weight_kg=Decimal("250"),
            line_total=Decimal("48.20"),
        )
        assert il.dest_zip == "35463"
        assert il.__tablename__ == "invoice_line"

    def test_invoice_dispute_event_instantiation(self):
        de = InvoiceDisputeEvent(
            tenant_id=TENANT_ID,
            invoice_line_id=uuid.uuid4(),
            event_type="flagged",
            amount_claimed=Decimal("25.00"),
        )
        assert de.event_type == "flagged"
        assert de.__tablename__ == "invoice_dispute_event"


class TestShipmentModels:
    def test_shipment_instantiation(self):
        s = Shipment(
            tenant_id=TENANT_ID,
            date=date(2024, 3, 10),
            dest_zip="35463",
            weight_kg=Decimal("250"),
            currency="EUR",
            actual_total_amount=Decimal("48.20"),
        )
        assert s.dest_zip == "35463"
        assert s.__tablename__ == "shipment"

    def test_shipment_benchmark_instantiation(self):
        sb = ShipmentBenchmark(
            shipment_id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            expected_total_amount=Decimal("45.00"),
            actual_total_amount=Decimal("48.20"),
            delta_amount=Decimal("3.20"),
            delta_pct=Decimal("7.11"),
            classification="drüber",
        )
        assert sb.classification == "drüber"
        assert sb.__tablename__ == "shipment_benchmark"


class TestFleetModels:
    def test_vehicle_instantiation(self):
        v = Vehicle(tenant_id=TENANT_ID, plate_number="ME CU 167", vehicle_type="MAN TGL")
        assert v.plate_number == "ME CU 167"
        assert v.__tablename__ == "vehicle"

    def test_own_tour_instantiation(self):
        ot = OwnTour(tenant_id=TENANT_ID, trip_date=date(2024, 3, 10), total_km=Decimal("87.5"))
        assert ot.total_km == Decimal("87.5")
        assert ot.__tablename__ == "own_tour"

    def test_own_tour_stop_instantiation(self):
        ots = OwnTourStop(tour_id=uuid.uuid4(), stop_sequence=1, arrival_zip="35463")
        assert ots.stop_sequence == 1
        assert ots.__tablename__ == "own_tour_stop"

    def test_fleet_vehicle_instantiation(self):
        fv = FleetVehicle(
            tenant_id=TENANT_ID,
            license_plate="ME CU 167",
            fixed_cost_per_day=Decimal("85.00"),
            variable_cost_per_km=Decimal("0.32"),
        )
        assert fv.license_plate == "ME CU 167"
        assert fv.__tablename__ == "fleet_vehicle"

    def test_fleet_driver_instantiation(self):
        fd = FleetDriver(tenant_id=TENANT_ID, name="Hans Müller", hourly_rate=Decimal("22.50"))
        assert fd.hourly_rate == Decimal("22.50")
        assert fd.__tablename__ == "fleet_driver"


class TestParsingModels:
    def test_parsing_template_instantiation(self):
        pt = ParsingTemplate(
            name="Cosi Invoice v2",
            file_type="pdf",
            template_category="invoice",
            detection={"carrier": "COSI"},
            mappings={"invoice_number": "Beleg-Nr"},
        )
        assert pt.file_type == "pdf"
        assert pt.__tablename__ == "parsing_template"

    def test_manual_mapping_instantiation(self):
        mm = ManualMapping(
            upload_id=UPLOAD_ID,
            field_name="dest_zip",
            source_column="PLZ Empfänger",
            created_by=uuid.uuid4(),
        )
        assert mm.field_name == "dest_zip"
        assert mm.__tablename__ == "manual_mapping"

    def test_raw_extraction_instantiation(self):
        re = RawExtraction(
            tenant_id=TENANT_ID,
            upload_id=UPLOAD_ID,
            doc_type="invoice",
            extractor="claude-vision",
            payload={"lines": []},
            retain_until=date(2034, 3, 10),
        )
        assert re.doc_type == "invoice"
        assert re.__tablename__ == "raw_extraction"

    def test_extraction_correction_instantiation(self):
        ec = ExtractionCorrection(
            tenant_id=TENANT_ID,
            upload_id=UPLOAD_ID,
            field_path="lines[3].weight_kg",
            original_value="25",
            corrected_value="250",
        )
        assert ec.field_path == "lines[3].weight_kg"
        assert ec.__tablename__ == "extraction_correction"


class TestBaseMetadata:
    def test_all_expected_tables_registered(self):
        expected = {
            "tenant", "carrier", "carrier_alias", "upload", "fx_rate", "users",
            "project", "consultant_note", "report",
            "tariff_table", "tariff_rate", "tariff_zone_map", "tariff_nebenkosten",
            "tariff_surcharge", "tariff_special_condition", "tariff_ftl_rate",
            "diesel_floater", "maut_table", "maut_rate", "maut_zone_map",
            "lsva_table", "lsva_rate", "city_surcharge",
            "invoice_header", "invoice_line", "invoice_dispute_event",
            "shipment", "shipment_benchmark",
            "vehicle", "fleet_cost_profile", "own_tour", "own_tour_stop",
            "fleet_vehicle", "fleet_driver",
            "parsing_template", "manual_mapping", "raw_extraction", "extraction_correction",
        }
        registered = set(Base.metadata.tables.keys())
        assert expected == registered, f"Missing: {expected - registered}, Extra: {registered - expected}"
