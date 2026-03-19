"""Unit tests for document service, type detector, and hash utility.

No live API calls, no real files required.
"""

import hashlib
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.services.document_type_detector import DocumentTypeDetector
from app.utils.hash import sha256_bytes, sha256_file


# ============================================================================
# Hash utility
# ============================================================================


class TestHash:
    def test_sha256_bytes_matches_stdlib(self):
        data = b"FreightWatch test bytes 12345"
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_bytes(data) == expected

    def test_sha256_bytes_empty(self):
        assert sha256_bytes(b"") == hashlib.sha256(b"").hexdigest()

    def test_sha256_bytes_returns_hex_string(self):
        result = sha256_bytes(b"abc")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_sha256_file(self, tmp_path):
        content = b"invoice data " * 1000
        p = tmp_path / "test.pdf"
        p.write_bytes(content)
        assert sha256_file(str(p)) == hashlib.sha256(content).hexdigest()

    def test_sha256_bytes_matches_nodejs_equivalent(self):
        """SHA-256('hello') must match Node.js createHash('sha256').update('hello').digest('hex')."""
        result = sha256_bytes(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


# ============================================================================
# Document type detector — filename heuristics (step 1)
# ============================================================================


class TestDocumentTypeDetectorFilename:
    def setup_method(self):
        self.detector = DocumentTypeDetector()

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("Tarifblatt_Cosi_2024.xlsx", "tariff"),
            ("entgelttabelle_cosi_at.xlsx", "tariff"),
            ("Frachttabelle_COSI_DE.xlsx", "tariff"),
            ("rate_card_gebrweiss.pdf", "tariff"),
            ("PREISLISTE_2024.xlsx", "tariff"),
            ("Konditionen_AS_Stahl.pdf", "tariff"),
            ("Rechnung_2024_01.pdf", "invoice"),
            ("invoice_117261.pdf", "invoice"),
            ("FAKTURA-ABC-123.pdf", "invoice"),
            ("gutschrift_jan.pdf", "invoice"),
            ("rg_2024_001.pdf", "invoice"),
            ("RG-20240315.PDF", "invoice"),
            # "rg" inside a word must NOT match
            ("programm.pdf", None),
            ("hergang_bericht.pdf", None),
            # Unknown → None
            ("sendungsdaten_export.csv", None),
        ],
    )
    def test_by_filename(self, filename, expected):
        result = self.detector._by_filename(filename)
        assert result == expected, f"{filename!r} → {result!r}, expected {expected!r}"


# ============================================================================
# Document type detector — column matching (step 2)
# ============================================================================


class TestDocumentTypeDetectorColumns:
    def setup_method(self):
        self.detector = DocumentTypeDetector()

    def test_shipment_columns_detected(self):
        cols = ["auftragsnummer", "shipment_date", "dest_zip", "weight_kg", "carrier"]
        assert self.detector._by_columns(cols) == "shipment_csv"

    def test_single_column_not_enough(self):
        assert self.detector._by_columns(["weight_kg", "price"]) is None

    def test_two_matches_triggers_detection(self):
        assert self.detector._by_columns(["origin_zip", "dest_zip"]) == "shipment_csv"

    def test_empty_columns(self):
        assert self.detector._by_columns([]) is None

    def test_german_columns(self):
        cols = ["Empfänger PLZ", "Sendungsnummer", "Gewicht", "Lieferdatum"]
        # "empfänger" and "sendung" both match
        assert self.detector._by_columns(cols) == "shipment_csv"


# ============================================================================
# Document type detector — full pipeline (mocked LLM)
# ============================================================================


class TestDocumentTypeDetectorPipeline:
    def setup_method(self):
        self.detector = DocumentTypeDetector()

    @pytest.mark.asyncio
    async def test_filename_wins_over_columns(self):
        """Filename heuristic (step 1) fires before column check (step 2)."""
        result = await self.detector.detect(
            filename="Tarifblatt_2024.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            column_names=["dest_zip", "weight_kg"],
        )
        assert result == "tariff"

    @pytest.mark.asyncio
    async def test_columns_fire_when_no_filename_match(self):
        result = await self.detector.detect(
            filename="export_2024.csv",
            mime_type="text/csv",
            column_names=["auftragsnummer", "dest_zip", "weight_kg"],
        )
        assert result == "shipment_csv"

    @pytest.mark.asyncio
    async def test_structured_file_defaults_to_shipment_csv(self):
        """Structured file with no column match → shipment_csv (not 'other')."""
        result = await self.detector.detect(
            filename="data_2024.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            column_names=["col_a", "col_b"],
        )
        assert result == "shipment_csv"

    @pytest.mark.asyncio
    async def test_llm_fallback_called_for_ambiguous_pdf(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"type": "invoice"}')]

        with patch.object(
            self.detector,
            "_is_llm_available",
            return_value=True,
        ), patch.object(
            self.detector,
            "_get_client",
            return_value=MagicMock(
                messages=MagicMock(
                    create=AsyncMock(return_value=mock_response)
                )
            ),
        ):
            result = await self.detector.detect(
                filename="document.pdf",
                mime_type="application/pdf",
                text_preview="Rechnung Nr. 117261 vom 15.03.2024",
            )
        assert result == "invoice"

    @pytest.mark.asyncio
    async def test_llm_not_called_when_unavailable(self):
        """When LLM is unavailable and no heuristic matches, return 'other'."""
        with patch.object(self.detector, "_is_llm_available", return_value=False):
            result = await self.detector.detect(
                filename="unknown.pdf",
                mime_type="application/pdf",
                text_preview="some unclassifiable content",
            )
        assert result == "other"

    @pytest.mark.asyncio
    async def test_llm_fallback_handles_parse_error(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json")]

        with patch.object(self.detector, "_is_llm_available", return_value=True), patch.object(
            self.detector,
            "_get_client",
            return_value=MagicMock(
                messages=MagicMock(create=AsyncMock(return_value=mock_response))
            ),
        ):
            result = await self.detector.detect(
                filename="mystery.pdf",
                mime_type="application/pdf",
                text_preview="...",
            )
        assert result == "other"


# ============================================================================
# DocumentService — CSV and XLSX processing (no real files needed)
# ============================================================================


class TestDocumentServiceSpreadsheets:
    """Test CSV/XLSX processing using in-memory bytes."""

    def setup_method(self):
        from app.services.document_service import DocumentService

        self.svc = DocumentService()

    @pytest.mark.asyncio
    async def test_csv_processing(self):
        csv_content = (
            "auftragsnummer,dest_zip,weight_kg,line_total\n"
            "230300073,35463,250.00,48.20\n"
            "230300074,42551,180.00,38.50\n"
        )
        result = await self.svc.process(
            csv_content.encode("utf-8"),
            filename="sendungen.csv",
            mime_type="text/csv",
        )
        assert result.mode == "csv"
        assert result.page_count == 1
        assert len(result.dataframes) == 1
        df = result.dataframes[0]
        assert list(df.columns) == ["auftragsnummer", "dest_zip", "weight_kg", "line_total"]
        assert len(df) == 2
        assert result.text is not None
        assert "35463" in result.text

    @pytest.mark.asyncio
    async def test_csv_hash_matches_sha256(self):
        data = b"col1,col2\n1,2\n3,4\n"
        result = await self.svc.process(data, filename="test.csv")
        assert result.file_hash == sha256_bytes(data)

    @pytest.mark.asyncio
    async def test_xlsx_processing(self):
        # Build a minimal xlsx in memory
        buf = io.BytesIO()
        df_in = pd.DataFrame({"zone": [1, 2, 3], "rate": [10.5, 12.0, 14.5]})
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_in.to_excel(writer, sheet_name="Tariff", index=False)
        xlsx_bytes = buf.getvalue()

        result = await self.svc.process(
            xlsx_bytes,
            filename="tarifblatt.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        assert result.mode == "xlsx"
        assert len(result.dataframes) == 1
        assert list(result.dataframes[0].columns) == ["zone", "rate"]
        assert result.text is not None
        assert "Tariff" in result.text

    @pytest.mark.asyncio
    async def test_unsupported_format_returns_placeholder(self):
        result = await self.svc.process(b"binary", filename="archive.zip")
        assert result.mode == "text"
        assert result.text is not None
        assert "Unsupported" in result.text

    @pytest.mark.asyncio
    async def test_csv_utf8_bom_encoding(self):
        csv_bom = "\ufeffauftrag,plz\n001,35463\n".encode("utf-8-sig")
        result = await self.svc.process(csv_bom, filename="data.csv")
        assert result.mode == "csv"
        assert len(result.dataframes) == 1
