import React, { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { api } from '../api';

interface UploadDetail {
  id: string;
  tenantId: string;
  projectId: string | null;
  filename: string;
  fileHash: string;
  rawTextHash: string | null;
  mimeType: string | null;
  sourceType: string | null;
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

interface ShipmentSummary {
  id: string;
  shipmentDate: string | null;
  referenceNumber: string | null;
  originZip: string | null;
  destZip: string | null;
  weightKg: number | null;
  currency: string | null;
  actualTotalAmount: number | null;
  completenessScore: number | null;
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reprocessing, setReprocessing] = useState(false);

  useEffect(() => {
    if (uploadId) loadData();
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
      <div className="container mx-auto px-4 max-w-5xl">
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
            <FieldRow label="source_type" value={detail.sourceType} />
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

          {/* Parsed Shipments */}
          <div className="bg-white rounded-lg shadow p-6">
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
                      <th className="pb-2 pr-3">Datum</th>
                      <th className="pb-2 pr-3">Referenz</th>
                      <th className="pb-2 pr-3">Von PLZ</th>
                      <th className="pb-2 pr-3">Nach PLZ</th>
                      <th className="pb-2 pr-3">Gewicht (kg)</th>
                      <th className="pb-2 pr-3">Betrag</th>
                      <th className="pb-2">Vollständigkeit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shipments.map((s) => (
                      <tr key={s.id} className="border-b last:border-0 hover:bg-gray-50">
                        <td className="py-2 pr-3">{s.shipmentDate ?? '—'}</td>
                        <td className="py-2 pr-3 font-mono text-xs">{s.referenceNumber ?? '—'}</td>
                        <td className="py-2 pr-3">{s.originZip ?? '—'}</td>
                        <td className="py-2 pr-3">{s.destZip ?? '—'}</td>
                        <td className="py-2 pr-3">{s.weightKg ?? '—'}</td>
                        <td className="py-2 pr-3">
                          {s.actualTotalAmount !== null
                            ? `${s.currency ?? ''} ${Number(s.actualTotalAmount).toFixed(2)}`
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
          </div>
        </div>
      </div>
    </div>
  );
};
