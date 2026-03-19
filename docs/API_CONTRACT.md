# FreightWatch — API Contract

**Version:** 2.0 (Python/FastAPI target)
**Last updated:** 2026-03-19
**Base URL:** `/api`
**Auth:** `Authorization: Bearer <jwt>` on all endpoints except `POST /api/auth/login`

---

## Conventions

### Naming
- **JSON fields:** camelCase (`projectId`, `createdAt`, `dataCompleteness`)
- **URL paths:** kebab-case (`/uploads/:uploadId/review/resolve-carrier`)
- **Query params:** camelCase (`?projectId=...`)

> **Migration note:** The legacy NestJS backend returned snake_case JSON. The new FastAPI backend
> uses camelCase via `model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)`.
> Frontend types must be updated from snake_case → camelCase during Phase 6.

### Response envelope
All endpoints return a JSON envelope:
```json
{ "success": true, "data": <payload> }
```
Exceptions: `POST /api/upload` returns `upload` and `POST /api/auth/login` returns `token` — see
individual sections.

### Errors
```json
{
  "success": false,
  "error": {
    "code": "PROJECT_NOT_FOUND",
    "message": "Project abc-123 not found",
    "details": {}
  }
}
```
Standard HTTP status codes: `400` Bad Request, `401` Unauthorized, `404` Not Found, `422`
Validation Error, `500` Internal Server Error.

### Types
| Type | Format | Example |
|------|--------|---------|
| UUID | string | `"a1b2c3d4-..."` |
| Date | `YYYY-MM-DD` | `"2025-03-19"` |
| DateTime | ISO 8601 UTC | `"2025-03-19T14:30:00.000Z"` |
| Monetary | decimal string, 2dp | `"125.50"` |
| Currency | ISO 4217, 3 chars | `"EUR"` |

---

## 1. Auth

### POST /api/auth/login

**Request:**
```json
{ "email": "user@example.com", "password": "secret" }
```

**Response 200:**
```json
{ "token": "<jwt>" }
```

JWT payload (decoded):
```json
{
  "sub": "uuid",
  "email": "user@example.com",
  "tenantId": "uuid",
  "roles": ["admin"],
  "firstName": "Anna",
  "lastName": "Müller",
  "iat": 1710000000,
  "exp": 1710086400
}
```

**Errors:** `401` Invalid credentials

---

## 2. Projects

### GET /api/projects

**Response 200:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "tenantId": "uuid",
      "name": "Q4 2023 Cost Analysis",
      "customerName": "MECU GmbH",
      "phase": "quick_check",
      "status": "draft",
      "metadata": {},
      "createdAt": "2025-03-19T10:00:00.000Z",
      "updatedAt": "2025-03-19T10:00:00.000Z"
    }
  ]
}
```

### POST /api/projects

**Request:**
```json
{
  "name": "Q4 2023 Cost Analysis",
  "customerName": "MECU GmbH",
  "phase": "quick_check",
  "status": "draft",
  "metadata": {}
}
```
Required: `name`. All others optional.

**Response 201:** `{ "success": true, "data": <Project> }`

### GET /api/projects/:id

**Response 200:** `{ "success": true, "data": <Project> }`

**Errors:** `404`

### PUT /api/projects/:id

**Request:** Any subset of project fields (all optional).

**Response 200:** `{ "success": true, "data": <Project> }`

### DELETE /api/projects/:id

**Response 204:** No content (soft delete via `deletedAt`)

### GET /api/projects/:id/stats

**Response 200:**
```json
{
  "success": true,
  "data": {
    "projectId": "uuid",
    "name": "Q4 2023 Cost Analysis",
    "uploadCount": 3,
    "shipmentCount": 1240,
    "avgCompleteness": 0.87,
    "noteCount": 2,
    "reportCount": 1,
    "phase": "quick_check",
    "status": "in_progress",
    "createdAt": "2025-03-19T10:00:00.000Z"
  }
}
```

### POST /api/projects/:id/notes

**Request:**
```json
{
  "noteType": "data_quality",
  "content": "45% of shipments have NULL toll_amount.",
  "relatedToUploadId": "uuid",
  "relatedToShipmentId": null,
  "priority": "warning",
  "status": "open"
}
```
Required: `noteType`, `content`.

`noteType` enum: `data_quality | missing_info | action_item | clarification | observation`
`priority` enum: `low | medium | high | critical`
`status` enum: `open | in_progress | resolved | closed`

**Response 201:** `{ "success": true, "data": <Note> }`

Note shape:
```json
{
  "id": "uuid",
  "projectId": "uuid",
  "noteType": "data_quality",
  "content": "45% of shipments have NULL toll_amount.",
  "relatedToUploadId": "uuid",
  "relatedToShipmentId": null,
  "priority": "warning",
  "status": "open",
  "createdBy": "uuid",
  "createdAt": "2025-03-19T10:00:00.000Z",
  "resolvedAt": null
}
```

### GET /api/projects/:id/notes

**Response 200:** `{ "success": true, "data": [<Note>, ...] }`

### PUT /api/projects/:id/notes/:noteId

**Request:** Any subset of note fields.
**Response 200:** `{ "success": true, "data": <Note> }`

### POST /api/projects/:id/notes/:noteId/resolve

**Response 200:** `{ "success": true, "data": <Note> }` (with `status: "resolved"`, `resolvedAt` set)

### GET /api/projects/:id/reports

**Response 200:** `{ "success": true, "data": [<Report>, ...] }`

---

## 3. Uploads

### POST /api/upload

**Content-Type:** `multipart/form-data`

**Fields:**
- `file` (required) — `.csv`, `.xls`, `.xlsx`, `.pdf`, max 10 MB
- `sourceType` (optional) — `invoice | rate_card | fleet_log`
- `projectId` (optional) — associate with project at upload time

**Response 200:**
```json
{
  "success": true,
  "upload": {
    "id": "uuid",
    "tenantId": "uuid",
    "projectId": "uuid",
    "filename": "dhl-invoices-q4.csv",
    "fileHash": "sha256hex...",
    "mimeType": "text/csv",
    "sourceType": "invoice",
    "docType": "invoice",
    "status": "pending",
    "parseMethod": null,
    "confidence": null,
    "suggestedMappings": null,
    "llmAnalysis": null,
    "parsingIssues": null,
    "reviewedBy": null,
    "reviewedAt": null,
    "receivedAt": "2025-03-19T10:00:00.000Z",
    "createdAt": "2025-03-19T10:00:00.000Z",
    "updatedAt": "2025-03-19T10:00:00.000Z"
  },
  "alreadyProcessed": false,
  "message": "File uploaded and queued for parsing"
}
```

`status` enum: `pending | processing | parsed | partial_success | needs_review | needs_manual_review | failed | error | reviewed | unmatched`
`parseMethod` enum: `template | llm | manual | hybrid | null`
`docType` enum: `tariff | invoice | shipment_csv | other | null`

**Errors:** `400` if file type not allowed or file missing

### GET /api/upload

List all uploads for the current tenant.

**Response 200:**
```json
{
  "success": true,
  "uploads": [<Upload>, ...]
}
```

Note: top-level key is `uploads`, not `data`.

---

## 4. Upload Review

### GET /api/uploads/:uploadId/review

**Query params:** `previewLines` (optional, default 50)

**Response 200:**
```json
{
  "success": true,
  "data": {
    "upload": {
      "id": "uuid",
      "filename": "dhl-invoices-q4.csv",
      "status": "needs_review",
      "parseMethod": "llm",
      "confidence": 0.72,
      "receivedAt": "2025-03-19T10:00:00.000Z",
      "projectId": "uuid"
    },
    "llmAnalysis": {
      "fileType": "csv",
      "confidence": 0.72,
      "description": "DHL invoice export with 12 columns",
      "structureAnalysis": {}
    },
    "suggestedMappings": [
      {
        "field": "shipmentDate",
        "column": "Versanddatum",
        "confidence": 0.95,
        "sampleValues": ["01.03.2023", "02.03.2023"]
      }
    ],
    "preview": [
      { "Versanddatum": "01.03.2023", "Empfänger-PLZ": "80331", "Gewicht": "12.5" }
    ],
    "qualityScore": 0.84,
    "parsingIssues": [<ParsingIssue>, ...]
  }
}
```

`ParsingIssue` shape:
```json
{
  "type": "missing_carrier",
  "message": "Could not identify carrier from file",
  "timestamp": "2025-03-19T10:00:00.000Z",
  "carrierName": "GW Logistics",
  "placeholderCarrierId": "uuid",
  "row": 42,
  "rawData": {},
  "invoiceNumber": "INV-2023-001",
  "lineNumber": 5,
  "missingFields": ["carrierId"]
}
```

### GET /api/uploads/:uploadId/review/carriers

List available (non-placeholder) carriers for alias resolution.

**Response 200:**
```json
{
  "success": true,
  "data": [
    { "id": "uuid", "name": "DHL Freight", "codeNorm": "DHL" }
  ]
}
```

### POST /api/uploads/:uploadId/review/resolve-carrier

**Request:**
```json
{
  "carrierName": "GW Logistics",
  "realCarrierId": "uuid"
}
```

**Response 200:** `{ "success": true, "message": "Carrier 'GW Logistics' resolved successfully" }`

### POST /api/uploads/:uploadId/review/accept

Accept suggested column mappings and trigger re-parse.

**Request:**
```json
{
  "mappings": { "shipmentDate": "Versanddatum", "destZip": "Empfänger-PLZ" },
  "saveAsTemplate": true,
  "templateName": "DHL Standard Export v2"
}
```

**Response 200:** `{ "success": true, "message": "Mappings applied and upload re-parsed" }`

### POST /api/uploads/:uploadId/review/reject

Provide corrected mappings.

**Request:**
```json
{
  "correctedMappings": { "shipmentDate": "Rechnungsdatum", "destZip": "PLZ" },
  "notes": "Date column was mislabeled",
  "saveAsTemplate": false
}
```

**Response 200:** `{ "success": true, "message": "Corrected mappings applied" }`

### POST /api/uploads/:uploadId/review/approve

Mark upload as reviewed (quality check complete).

**Request:**
```json
{ "notes": "All carriers resolved, data quality acceptable" }
```

**Response 200:** `{ "success": true, "message": "Upload approved" }`

### POST /api/uploads/:uploadId/review/reprocess

**Request:**
```json
{ "reason": "Column mapping was wrong", "forceLlm": true }
```

**Response 200:** `{ "success": true, "message": "Upload queued for re-processing" }`

### GET /api/uploads/:uploadId/review/issues

**Response 200:**
```json
{
  "success": true,
  "data": {
    "issues": [<ParsingIssue>, ...],
    "confidence": 0.72,
    "parseMethod": "llm"
  }
}
```

---

## 5. Reports

### POST /api/reports/generate?projectId=:id

**Request (optional body):**
```json
{
  "includeTopOverpays": true,
  "topOverpaysLimit": 10,
  "notes": "Q4 snapshot after carrier resolution"
}
```

**Response 200:** `{ "success": true, "data": <Report> }`

Report shape:
```json
{
  "id": "uuid",
  "projectId": "uuid",
  "version": 3,
  "reportType": "quick_check",
  "title": null,
  "dataSnapshot": {
    "version": 3,
    "generatedAt": "2025-03-19T10:00:00.000Z",
    "project": {
      "id": "uuid",
      "name": "Q4 2023 Cost Analysis",
      "phase": "quick_check",
      "status": "in_progress"
    },
    "statistics": {
      "totalShipments": 1240,
      "parsedShipments": 1198,
      "benchmarkedShipments": 1021,
      "completeShipments": 980,
      "partialShipments": 218,
      "missingShipments": 42,
      "dataCompletenessAvg": 0.87,
      "totalActualCost": "142350.00",
      "totalExpectedCost": "128900.50",
      "totalSavingsPotential": "13449.50",
      "overpayRate": 0.34,
      "carriers": [
        {
          "carrierId": "uuid",
          "carrierName": "DHL Freight",
          "shipmentCount": 620,
          "totalActualCost": "75200.00",
          "totalExpectedCost": "68400.00",
          "totalDelta": "6800.00",
          "avgDeltaPct": 9.94,
          "overpayCount": 210,
          "underpayCount": 45,
          "marketCount": 365,
          "dataCompletenessAvg": 0.91
        }
      ]
    },
    "dataCompleteness": 0.87,
    "topOverpays": [
      {
        "shipmentId": "uuid",
        "date": "2023-12-01",
        "carrier": "DHL Freight",
        "originZip": "80331",
        "destZip": "10115",
        "actualCost": "285.40",
        "expectedCost": "198.00",
        "delta": "87.40",
        "deltaPct": 44.14
      }
    ]
  },
  "dataCompleteness": 0.87,
  "shipmentCount": 1240,
  "dateRangeStart": "2023-10-01",
  "dateRangeEnd": "2023-12-31",
  "generatedBy": "uuid",
  "generatedAt": "2025-03-19T10:00:00.000Z",
  "createdAt": "2025-03-19T10:00:00.000Z",
  "notes": null
}
```

`reportType` enum: `quick_check | deep_dive | final`

### GET /api/reports/latest?projectId=:id

**Response 200:** `{ "success": true, "data": <Report> }`

**Errors:** `404` No reports found for this project

### GET /api/reports/:reportId

Get specific report by ID.

**Response 200:** `{ "success": true, "data": <Report> }`

**Errors:** `404`

### GET /api/reports/list?projectId=:id

**Response 200:** `{ "success": true, "data": [<Report>, ...] }`

### GET /api/reports/statistics?projectId=:id

Live statistics (not persisted as a report version).

**Response 200:** `{ "success": true, "data": <Statistics> }` — same shape as `dataSnapshot.statistics`

### GET /api/reports/top-overpays?projectId=:id&limit=10

**Response 200:** `{ "success": true, "data": [<TopOverpay>, ...] }`

---

## 6. Enums Reference

### Project Phase
| Value | Meaning |
|-------|---------|
| `quick_check` | Rapid initial analysis |
| `deep_dive` | Detailed investigation |
| `final` | Final deliverable |

### Project Status
| Value | Meaning |
|-------|---------|
| `draft` | Being set up |
| `in_progress` | Analysis underway |
| `review` | Ready for consultant review |
| `completed` | Analysis complete |

### Overpay Classification
| Value | Condition | Action |
|-------|-----------|--------|
| `unter` | `deltaPct < -5%` | Actual below expected |
| `im_markt` | `-5% ≤ deltaPct ≤ 5%` | Within tolerance |
| `drüber` | `deltaPct > 5%` | **Overpayment — flag for review** |

---

## 7. Frontend Migration Notes (Phase 6)

The legacy frontend (`frontend/src/types/index.ts`) was written against snake_case JSON. When
the new FastAPI backend is wired up, the following fields must be renamed in the frontend:

| Old (snake_case) | New (camelCase) |
|------------------|-----------------|
| `tenant_id` | `tenantId` |
| `project_id` | `projectId` |
| `customer_name` | `customerName` |
| `created_at` | `createdAt` |
| `updated_at` | `updatedAt` |
| `upload_count` | `uploadCount` |
| `shipment_count` | `shipmentCount` |
| `avg_completeness` | `avgCompleteness` |
| `note_count` | `noteCount` |
| `report_count` | `reportCount` |
| `data_completeness` | `dataCompleteness` |
| `file_hash` | `fileHash` |
| `mime_type` | `mimeType` |
| `source_type` | `sourceType` |
| `doc_type` | `docType` |
| `parse_method` | `parseMethod` |
| `suggested_mappings` | `suggestedMappings` |
| `llm_analysis` | `llmAnalysis` |
| `parsing_issues` | `parsingIssues` |
| `reviewed_by` | `reviewedBy` |
| `reviewed_at` | `reviewedAt` |
| `received_at` | `receivedAt` |
| `report_type` | `reportType` |
| `data_snapshot` | `dataSnapshot` |
| `date_range_start` | `dateRangeStart` |
| `date_range_end` | `dateRangeEnd` |
| `generated_by` | `generatedBy` |
| `generated_at` | `generatedAt` |
| `note_type` | `noteType` |
| `related_to_upload_id` | `relatedToUploadId` |
| `created_by` | `createdBy` |
| `resolved_at` | `resolvedAt` |
| `carrier_id` | `carrierId` |
| `carrier_name` | `carrierName` |
| `total_actual_cost` | `totalActualCost` |
| `total_expected_cost` | `totalExpectedCost` |
| `total_delta` | `totalDelta` |
| `avg_delta_pct` | `avgDeltaPct` |
| `overpay_count` | `overpayCount` |
| `underpay_count` | `underpayCount` |
| `market_count` | `marketCount` |
| `total_savings_potential` | `totalSavingsPotential` |
| `overpay_rate` | `overpayRate` |
| `origin_zip` | `originZip` |
| `dest_zip` | `destZip` |
| `actual_cost` | `actualCost` |
| `expected_cost` | `expectedCost` |
| `delta_pct` | `deltaPct` |
| `shipment_id` | `shipmentId` |
| `code_norm` | `codeNorm` |
| `quality_score` | `qualityScore` |
| `structure_analysis` | `structureAnalysis` |
| `sample_values` | `sampleValues` |
| `file_type` | `fileType` |
| `carrier_name` | `carrierName` |
| `placeholder_carrier_id` | `placeholderCarrierId` |
| `raw_data` | `rawData` |
| `invoice_number` | `invoiceNumber` |
| `line_number` | `lineNumber` |
| `missing_fields` | `missingFields` |

The `GET /api/upload` response uses the top-level key `uploads` (not `data`) — this is a legacy
quirk and should be normalised to `data` in the Python backend:
```json
{ "success": true, "data": [<Upload>, ...] }
```
Similarly `POST /api/upload` uses `upload` — normalise to `data`.
