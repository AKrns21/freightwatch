import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { ShipmentSummary } from '../types';

interface DieselBracket {
  id: string;
  carrierId: string;
  carrierName: string | null;
  priceCtMax: string;
  floaterPct: string;
  basis: string;
  validFrom: string;
  validUntil: string | null;
}

const DieselBracketView: React.FC<{ brackets: DieselBracket[] }> = ({ brackets }) => {
  if (brackets.length === 0) return null;
  const carrierName = brackets[0].carrierName ?? '—';
  const basis = brackets[0].basis;
  const validFrom = brackets[0].validFrom;

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-gray-900">Dieselfloater — Preisklassen ({brackets.length})</h2>
        <p className="text-sm text-gray-500 mt-0.5">
          Spediteur: <span className="font-medium text-gray-700">{carrierName}</span>
          {' · '}Basis: <span className="font-medium text-gray-700">{basis}</span>
          {' · '}Gültig ab: <span className="font-medium text-gray-700">{validFrom}</span>
        </p>
      </div>
      <table className="text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-300">
            <th className="text-left pb-2 pr-16 font-semibold text-gray-700">Dieselpreis Ct je Liter</th>
            <th className="text-right pb-2 font-semibold text-gray-700">Zuschlag</th>
          </tr>
        </thead>
        <tbody>
          {brackets.map(b => (
            <tr key={b.id} className="border-b border-gray-100">
              <td className="py-1.5 pr-16 text-gray-700">≤ {parseFloat(b.priceCtMax).toFixed(0)}</td>
              <td className={`py-1.5 text-right font-mono font-medium ${parseFloat(b.floaterPct) === 0 ? 'text-gray-400' : 'text-gray-900'}`}>
                {parseFloat(b.floaterPct).toFixed(2)} %
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

interface TariffRate {
  id: string;
  zone: number;
  weightFromKg: number;
  weightToKg: number;
  ratePerShipment: number | null;
  ratePerKg: number | null;
}

interface TariffZoneMap {
  id: string;
  countryCode: string;
  plzPrefix: string;
  matchType: string;
  zone: number;
}

interface TariffDetail {
  id: string;
  name: string | null;
  carrierId: string;
  uploadId: string | null;
  laneType: string;
  currency: string;
  validFrom: string;
  validUntil: string | null;
  confidence: number | null;
  rates: TariffRate[];
  zoneMaps: TariffZoneMap[];
}

interface UploadDetail {
  id: string;
  tenantId: string;
  projectId: string | null;
  filename: string;
  fileHash: string;
  rawTextHash: string | null;
  mimeType: string | null;
  docType: string | null;
  storageUrl: string | null;
  status: string | null;
  parseMethod: string | null;
  confidence: number | null;
  llmAnalysis: Record<string, any> | null;
  parseErrors: Record<string, any> | null;
  parsingIssues: any[] | null;
  suggestedMappings: Record<string, any> | null;
  meta: Record<string, any> | null;
  reviewedBy: string | null;
  reviewedAt: string | null;
  receivedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  shipmentCount: number;
}


const STATUS_COLORS: Record<string, string> = {
  parsed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  needs_review: 'bg-yellow-100 text-yellow-700',
  needs_manual_review: 'bg-orange-100 text-orange-700',
  partial_success: 'bg-blue-100 text-blue-700',
  pending: 'bg-gray-100 text-gray-600',
  parsing: 'bg-blue-100 text-blue-600',
};

const fmt = (n: number | null, decimals = 2) =>
  n != null ? n.toLocaleString('de-DE', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : '—';

/** Compact cross-tab: weight bands as rows, zones as columns */
const RateMatrix: React.FC<{
  rates: TariffRate[];
  currency: string;
  /** For Hauptlauf (zone=0): rate_per_kg is actually per km */
  perKgLabel?: string;
}> = ({ rates, currency, perKgLabel = '/kg' }) => {
  const zones = Array.from(new Set(rates.map((r) => r.zone))).sort((a, b) => a - b);
  const bands = Array.from(
    new Map(rates.map((r) => [`${r.weightFromKg}`, r])).values()
  ).sort((a, b) => a.weightFromKg - b.weightFromKg);
  const rateMap = new Map(rates.map((r) => [`${r.zone}-${r.weightFromKg}`, r]));

  const bandLabel = (r: TariffRate) =>
    r.weightToKg >= 99000 ? `> ${fmt(r.weightFromKg, 0)} kg` : `≤ ${fmt(r.weightToKg, 0)} kg`;

  const cellValue = (cell: TariffRate | undefined) => {
    if (!cell) return '—';
    if (cell.ratePerShipment != null) return `${fmt(cell.ratePerShipment)} ${currency}`;
    if (cell.ratePerKg != null) return `${fmt(cell.ratePerKg, 4)} ${perKgLabel}`;
    return '—';
  };

  if (rates.length === 0) return <p className="text-sm text-gray-400">Keine Einträge</p>;

  return (
    <div className="overflow-x-auto">
      <table className="text-sm border-collapse">
        <thead>
          <tr>
            <th className="text-left text-gray-500 font-normal pr-6 pb-2 whitespace-nowrap">Gewicht</th>
            {zones.map((z) => (
              <th key={z} className="text-right text-gray-700 font-medium px-3 pb-2 whitespace-nowrap">
                {z === 0 ? 'Hauptlauf' : z === -1 ? 'Direkt' : `Zone ${z}`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bands.map((band) => (
            <tr key={band.weightFromKg} className="border-t border-gray-100">
              <td className="pr-6 py-1.5 text-gray-500 whitespace-nowrap">{bandLabel(band)}</td>
              {zones.map((z) => (
                <td key={z} className="px-3 py-1.5 text-right tabular-nums text-gray-900">
                  {cellValue(rateMap.get(`${z}-${band.weightFromKg}`))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const TariffTableView: React.FC<{ tariff: TariffDetail }> = ({ tariff }) => {
  const vorNachlauf = tariff.rates.filter((r) => r.zone > 0);
  const hauptlauf   = tariff.rates.filter((r) => r.zone === 0);
  const direkt      = tariff.rates.filter((r) => r.zone === -1);

  // Group zone_maps by zone for the PLZ table
  const zoneGroups = new Map<number, string[]>();
  for (const zm of tariff.zoneMaps) {
    if (!zoneGroups.has(zm.zone)) zoneGroups.set(zm.zone, []);
    zoneGroups.get(zm.zone)!.push(zm.plzPrefix);
  }

  return (
    <div className="bg-white rounded-lg shadow p-6 space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-gray-900">{tariff.name ?? 'Tarifblatt'}</h2>
        <p className="text-sm text-gray-500 mt-0.5">
          Gültig ab {tariff.validFrom}
          {tariff.validUntil && ` bis ${tariff.validUntil}`}
          {' · '}{tariff.currency} · {tariff.laneType}
          {tariff.confidence != null && (
            <span className="ml-2 text-gray-400">
              ({(tariff.confidence * 100).toFixed(0)} % Konfidenz)
            </span>
          )}
        </p>
      </div>

      {/* Vor-/Nachlauf */}
      {vorNachlauf.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">Vor-/Nachlauf</h3>
          <RateMatrix rates={vorNachlauf} currency={tariff.currency} />

          {/* PLZ zone map */}
          {zoneGroups.size > 0 && (
            <div className="mt-4">
              <p className="text-xs text-gray-500 mb-2">PLZ-Zonenzuordnung</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
                {Array.from(zoneGroups.entries()).sort(([a], [b]) => a - b).map(([zone, prefixes]) => (
                  <div key={zone} className="bg-gray-50 rounded px-3 py-2">
                    <div className="text-xs font-medium text-gray-600 mb-1">Zone {zone}</div>
                    <div className="text-xs text-gray-500 font-mono leading-relaxed">
                      {prefixes.sort().join(', ')}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Hauptlauf — rate_per_kg stores per-km rates for trunk haul */}
      {hauptlauf.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">Hauptlauf</h3>
          <RateMatrix rates={hauptlauf} currency={tariff.currency} perKgLabel="/km" />
        </div>
      )}

      {/* Direktverkehr */}
      {direkt.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-800 mb-3">Direkt</h3>
          <RateMatrix rates={direkt} currency={tariff.currency} />
        </div>
      )}
    </div>
  );
};

const FieldRow: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="flex gap-2 py-1.5 border-b last:border-0">
    <span className="text-gray-500 w-48 shrink-0 text-sm">{label}</span>
    <span className="text-gray-900 text-sm font-mono break-all">{value ?? <em className="text-gray-400 not-italic">null</em>}</span>
  </div>
);

export const UploadDetailPage: React.FC = () => {
  const { uploadId } = useParams<{ uploadId: string }>();
  const navigate = useNavigate();

  const [detail, setDetail] = useState<UploadDetail | null>(null);
  const [shipments, setShipments] = useState<ShipmentSummary[]>([]);
  const [tariff, setTariff] = useState<TariffDetail | null>(null);
  const [brackets, setBrackets] = useState<DieselBracket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reprocessing, setReprocessing] = useState(false);

  useEffect(() => {
    if (!uploadId) return;
    let cancelled = false;

    const pollUntilDone = async () => {
      await loadData();
      for (let i = 0; i < 60; i++) {
        const statusRes = await api.get<{ status: string }>(`/api/uploads/${uploadId}`);
        const status = statusRes.data.status;
        if (status !== 'parsing' && status !== 'pending') break;
        await new Promise((r) => setTimeout(r, 2000));
        if (cancelled) return;
        await loadData();
      }
    };

    pollUntilDone();
    return () => { cancelled = true; };
  }, [uploadId]);

  const handleReprocess = async () => {
    if (!uploadId) return;
    setReprocessing(true);
    setError(null);
    try {
      await api.post(`/api/uploads/${uploadId}/reprocess`);
      // Poll until the pipeline finishes (leaves 'parsing' / 'pending' state)
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        await loadData();
        const currentStatus = (await api.get<{ status: string }>(`/api/uploads/${uploadId}`)).data.status;
        if (currentStatus !== 'parsing' && currentStatus !== 'pending') break;
      }
      await loadData();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Reprocess failed');
    } finally {
      setReprocessing(false);
    }
  };

  const loadData = async () => {
    try {
      setLoading(true);
      const [detailRes, shipmentsRes] = await Promise.all([
        api.get<UploadDetail>(`/api/uploads/${uploadId}/detail`),
        api.get<ShipmentSummary[]>(`/api/uploads/${uploadId}/shipments`),
      ]);
      setDetail(detailRes.data);
      setShipments(shipmentsRes.data);

      const tariffTableId = detailRes.data.llmAnalysis?.tariff_table_id;
      if (detailRes.data.docType === 'tariff' && tariffTableId) {
        const tariffRes = await api.get<TariffDetail>(`/api/tariffs/${tariffTableId}`);
        setTariff(tariffRes.data);
      }
      if (detailRes.data.docType === 'diesel_floater') {
        const bracketsRes = await api.get<DieselBracket[]>('/api/diesel-floaters/brackets');
        setBrackets(bracketsRes.data);
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load upload details');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center min-h-screen text-gray-600">Laden...</div>;
  }

  if (error || !detail) {
    return (
      <div className="container mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-red-600">{error ?? 'Upload not found'}</p>
          <button onClick={() => navigate(-1)} className="mt-3 text-blue-600 hover:underline text-sm">← Zurück</button>
        </div>
      </div>
    );
  }

  const statusColor = STATUS_COLORS[detail.status ?? ''] ?? 'bg-gray-100 text-gray-600';

  return (
    <div className="min-h-screen bg-gray-100 py-8">
      <div className="container mx-auto px-4 max-w-7xl">
        {/* Header */}
        <div className="mb-6">
          <button onClick={() => navigate(-1)} className="text-blue-600 hover:text-blue-700 text-sm mb-3 block">
            ← Zurück
          </button>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">{detail.filename}</h1>
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusColor}`}>
              {detail.status}
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-1">Upload-ID: {detail.id}</p>
        </div>

        <div className="grid grid-cols-1 gap-6">
          {/* DB Record */}
          <div className="bg-white rounded-lg shadow p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Datenbank-Eintrag</h2>
              <div className="flex gap-2">
                <button
                  onClick={handleReprocess}
                  disabled={reprocessing || detail.status === 'parsing'}
                  className="text-sm bg-yellow-600 text-white px-4 py-1.5 rounded hover:bg-yellow-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {reprocessing ? 'Wird verarbeitet…' : 'Erneut verarbeiten'}
                </button>
                <a
                  href={`/api/uploads/${uploadId}/file`}
                  download={detail.filename}
                  className="text-sm bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700"
                >
                  Originaldatei herunterladen
                </a>
              </div>
            </div>

            <FieldRow label="id" value={detail.id} />
            <FieldRow label="tenant_id" value={detail.tenantId} />
            <FieldRow label="project_id" value={detail.projectId} />
            <FieldRow label="filename" value={detail.filename} />
            <FieldRow label="file_hash" value={detail.fileHash} />
            <FieldRow label="raw_text_hash" value={detail.rawTextHash} />
            <FieldRow label="mime_type" value={detail.mimeType} />
            <FieldRow label="doc_type" value={detail.docType} />
            <FieldRow label="storage_url" value={detail.storageUrl} />
            <FieldRow label="status" value={detail.status} />
            <FieldRow label="parse_method" value={detail.parseMethod} />
            <FieldRow
              label="confidence"
              value={detail.confidence !== null ? `${(detail.confidence * 100).toFixed(0)}%` : null}
            />
            <FieldRow label="reviewed_by" value={detail.reviewedBy} />
            <FieldRow label="reviewed_at" value={detail.reviewedAt} />
            <FieldRow label="received_at" value={detail.receivedAt} />
            <FieldRow label="created_at" value={detail.createdAt} />
            <FieldRow label="updated_at" value={detail.updatedAt} />
          </div>

          {/* JSONB Fields */}
          {detail.parsingIssues && detail.parsingIssues.length > 0 && (
            <div className="bg-orange-50 border border-orange-200 rounded-lg p-6">
              <h2 className="text-lg font-semibold text-orange-800 mb-3">parsing_issues ({detail.parsingIssues.length})</h2>
              <pre className="text-xs text-orange-900 overflow-auto bg-orange-100 p-3 rounded">
                {JSON.stringify(detail.parsingIssues, null, 2)}
              </pre>
            </div>
          )}

          {detail.parseErrors && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-6">
              <h2 className="text-lg font-semibold text-red-800 mb-3">parse_errors</h2>
              <pre className="text-xs text-red-900 overflow-auto bg-red-100 p-3 rounded">
                {JSON.stringify(detail.parseErrors, null, 2)}
              </pre>
            </div>
          )}

          {detail.llmAnalysis && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">llm_analysis</h2>
              <pre className="text-xs text-gray-700 overflow-auto bg-gray-50 p-3 rounded">
                {JSON.stringify(detail.llmAnalysis, null, 2)}
              </pre>
            </div>
          )}

          {detail.suggestedMappings && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">suggested_mappings</h2>
              <pre className="text-xs text-gray-700 overflow-auto bg-gray-50 p-3 rounded">
                {JSON.stringify(detail.suggestedMappings, null, 2)}
              </pre>
            </div>
          )}

          {detail.meta && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">meta</h2>
              <pre className="text-xs text-gray-700 overflow-auto bg-gray-50 p-3 rounded">
                {JSON.stringify(detail.meta, null, 2)}
              </pre>
            </div>
          )}

          {/* Tariff Table */}
          {tariff && <TariffTableView tariff={tariff} />}

          {/* Diesel Floater Brackets */}
          {detail.docType === 'diesel_floater' && <DieselBracketView brackets={brackets} />}

          {/* Parsed Shipments */}
          {detail.docType !== 'diesel_floater' && <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">
              Geparste Sendungen ({detail.shipmentCount})
            </h2>
            {shipments.length === 0 ? (
              <p className="text-gray-500 text-sm">Keine Sendungen aus diesem Upload.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-gray-500">
                      <th className="pb-2 pr-3">Rechnung</th>
                      <th className="pb-2 pr-3">Datum</th>
                      <th className="pb-2 pr-3">Referenz</th>
                      <th className="pb-2 pr-3">Von PLZ</th>
                      <th className="pb-2 pr-3">Nach PLZ</th>
                      <th className="pb-2 pr-3 text-right">Gewicht (kg)</th>
                      <th className="pb-2 pr-3 text-right">Betrag</th>
                      <th className="pb-2">Vollständigkeit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shipments.map((s) => (
                      <tr key={s.id} className="border-b last:border-0 hover:bg-gray-50">
                        <td className="py-2 pr-3 font-mono text-xs">{s.invoiceNumber ?? '—'}</td>
                        <td className="py-2 pr-3">
                          {s.shipmentDate ? s.shipmentDate.split('-').reverse().join('.') : '—'}
                        </td>
                        <td className="py-2 pr-3 font-mono text-xs">{s.referenceNumber ?? '—'}</td>
                        <td className="py-2 pr-3">{s.originZip ?? '—'}</td>
                        <td className="py-2 pr-3">{s.destZip ?? '—'}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {s.weightKg != null ? Number(s.weightKg).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {s.actualTotalAmount != null
                            ? `${Number(s.actualTotalAmount).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${s.currency ?? ''}`
                            : '—'}
                        </td>
                        <td className="py-2">
                          {s.completenessScore !== null ? (
                            <span className={
                              Number(s.completenessScore) >= 0.9 ? 'text-green-600' :
                              Number(s.completenessScore) >= 0.7 ? 'text-yellow-600' :
                              'text-red-600'
                            }>
                              {(Number(s.completenessScore) * 100).toFixed(0)}%
                            </span>
                          ) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>}
        </div>
      </div>
    </div>
  );
};
