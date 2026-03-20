import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';

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

interface UploadInfo {
  id: string;
  filename: string;
  status: string | null;
  parsingIssues: any[] | null;
  createdAt: string | null;
}

const BASIS_LABELS: Record<string, string> = {
  base: 'Fracht (Basis)',
  base_plus_toll: 'Fracht + Maut',
  total: 'Gesamt',
};

export const DieselFloaterDetailPage: React.FC = () => {
  const { uploadId } = useParams<{ uploadId: string }>();
  const navigate = useNavigate();
  const [upload, setUpload] = useState<UploadInfo | null>(null);
  const [brackets, setBrackets] = useState<DieselBracket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uploadId) return;
    load();
  }, [uploadId]);

  const load = async () => {
    setLoading(true);
    try {
      const [uploadRes, bracketsRes] = await Promise.all([
        api.get<UploadInfo>(`/api/uploads/${uploadId}/detail`),
        api.get<DieselBracket[]>('/api/diesel-floaters/brackets'),
      ]);
      setUpload(uploadRes.data);
      // Show brackets for the carrier detected in this upload
      // The summary issue contains the carrier name — filter brackets to that carrier
      const importIssue = uploadRes.data.parsingIssues?.find(
        (i: any) => i.type === 'diesel_floater_imported'
      );
      const carrierName = importIssue?.message?.match(/carrier '([^']+)'/)?.[1];
      const filtered = carrierName
        ? bracketsRes.data.filter(b => b.carrierName === carrierName)
        : bracketsRes.data;
      setBrackets(filtered);
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Laden fehlgeschlagen');
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="flex items-center justify-center min-h-screen text-gray-600">Laden…</div>;
  if (error || !upload) return (
    <div className="container mx-auto p-6">
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-600">{error ?? 'Upload nicht gefunden'}</div>
    </div>
  );

  const importIssue = upload.parsingIssues?.find((i: any) => i.type === 'diesel_floater_imported');
  const carrierName = brackets[0]?.carrierName ?? '—';
  const basis = brackets[0] ? (BASIS_LABELS[brackets[0].basis] ?? brackets[0].basis) : '—';
  const validFrom = brackets[0]?.validFrom ?? '—';

  return (
    <div className="min-h-screen bg-gray-100 py-8">
      <div className="container mx-auto px-4 max-w-2xl">
        <button onClick={() => navigate(-1)} className="text-blue-600 hover:text-blue-700 text-sm mb-4 block">
          ← Zurück
        </button>

        <div className="bg-white rounded-lg shadow p-6">
          {/* Header */}
          <div className="flex items-start justify-between mb-6">
            <div>
              <h1 className="text-xl font-bold text-gray-900">{upload.filename}</h1>
              <p className="text-sm text-gray-500 mt-0.5">
                Spediteur: <span className="font-medium text-gray-700">{carrierName}</span>
                {' · '}Basis: <span className="font-medium text-gray-700">{basis}</span>
                {' · '}Gültig ab: <span className="font-medium text-gray-700">{validFrom}</span>
              </p>
            </div>
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">
              {brackets.length} Stufen
            </span>
          </div>

          {importIssue && (
            <div className="bg-green-50 border border-green-200 rounded p-3 mb-6 text-sm text-green-800">
              {importIssue.message}
            </div>
          )}

          {/* Bracket table — two columns, matching the original PDF layout */}
          {brackets.length === 0 ? (
            <p className="text-gray-500 text-sm">Keine Preisklassen gefunden.</p>
          ) : (
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-gray-300">
                  <th className="text-left pb-2 font-semibold text-gray-700">Dieselpreis Ct je Liter</th>
                  <th className="text-right pb-2 font-semibold text-gray-700">Zuschlag</th>
                </tr>
              </thead>
              <tbody>
                {brackets.map(b => (
                  <tr key={b.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-1.5 text-gray-700">
                      ≤ {parseFloat(b.priceCtMax).toFixed(0)}
                    </td>
                    <td className={`py-1.5 text-right font-mono font-medium ${parseFloat(b.floaterPct) === 0 ? 'text-gray-400' : 'text-gray-900'}`}>
                      {parseFloat(b.floaterPct).toFixed(2)} %
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};
