import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { UploadReviewData, ApiResponse, CarrierOption, ParsingIssue } from '../types';

/**
 * UploadReviewPage - Review LLM Analysis & Mappings (Phase 7.2)
 *
 * Human-in-the-loop review interface for:
 * - LLM file type analysis
 * - Suggested column mappings
 * - Data preview
 * - Accept or reject parsing strategy
 */
export const UploadReviewPage: React.FC = () => {
  const { uploadId } = useParams<{ uploadId: string }>();
  const navigate = useNavigate();
  const [reviewData, setReviewData] = useState<UploadReviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [carriers, setCarriers] = useState<CarrierOption[]>([]);
  const [carrierSelections, setCarrierSelections] = useState<Record<string, string>>({});
  const [resolving, setResolving] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (uploadId) {
      loadReviewData();
    }
  }, [uploadId]);

  const loadReviewData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [reviewResponse, carriersResponse] = await Promise.all([
        api.get<ApiResponse<UploadReviewData>>(`/api/uploads/${uploadId}/review`),
        api.get<ApiResponse<CarrierOption[]>>(`/api/uploads/${uploadId}/review/carriers`),
      ]);
      setReviewData(reviewResponse.data.data);
      setCarriers(carriersResponse.data.data);
    } catch (err: any) {
      setError(err.response?.data?.error?.message || 'Failed to load review data');
      console.error('Failed to load review data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleResolveCarrier = async (carrierName: string) => {
    const realCarrierId = carrierSelections[carrierName];
    if (!realCarrierId) return;

    setResolving((prev) => ({ ...prev, [carrierName]: true }));
    try {
      await api.post(`/api/uploads/${uploadId}/review/resolve-carrier`, {
        carrier_name: carrierName,
        real_carrier_id: realCarrierId,
      });
      // Remove resolved issue from local state
      setReviewData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          parsing_issues: (prev.parsing_issues ?? []).filter(
            (i: ParsingIssue) => !(i.type === 'unknown_carrier' && i.carrier_name === carrierName)
          ),
        };
      });
    } catch (err: any) {
      setError(err.response?.data?.error?.message || 'Failed to resolve carrier');
    } finally {
      setResolving((prev) => ({ ...prev, [carrierName]: false }));
    }
  };

  const handleAccept = async () => {
    if (!reviewData) return;

    try {
      setSubmitting(true);
      await api.post(`/api/uploads/${uploadId}/review/accept`, {
        mappings: reviewData.suggested_mappings,
        save_as_template: false,
      });
      // Navigate back to project page
      navigate(`/projects/${reviewData.upload.project_id}`);
    } catch (err: any) {
      setError(err.response?.data?.error?.message || 'Failed to accept mappings');
      console.error('Failed to accept mappings:', err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    if (!reviewData) return;

    try {
      setSubmitting(true);
      await api.post(`/api/uploads/${uploadId}/review/reject`, {
        reason: 'Mappings incorrect',
      });
      // Navigate back to project page
      navigate(`/projects/${reviewData.upload.project_id}`);
    } catch (err: any) {
      setError(err.response?.data?.error?.message || 'Failed to reject upload');
      console.error('Failed to reject upload:', err);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-xl text-gray-600">Loading review data...</div>
      </div>
    );
  }

  if (error && !reviewData) {
    return (
      <div className="container mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <h2 className="text-red-800 font-semibold mb-2">Error</h2>
          <p className="text-red-600">{error}</p>
          <button
            onClick={loadReviewData}
            className="mt-4 bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!reviewData) return null;

  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6 text-gray-900">Review Upload</h1>

      {/* Error Message */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-red-600">{error}</p>
        </div>
      )}

      {/* File Info */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">File Information</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <span className="text-gray-600">Filename:</span>{' '}
            <span className="font-medium">{reviewData.upload.original_filename}</span>
          </div>
          <div>
            <span className="text-gray-600">Status:</span>{' '}
            <span className="font-medium">{reviewData.upload.status}</span>
          </div>
        </div>
      </div>

      {/* Unmapped Carriers */}
      {(reviewData.parsing_issues ?? []).filter((i: ParsingIssue) => i.type === 'unknown_carrier').length > 0 && (
        <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-6 mb-6">
          <h2 className="text-xl font-semibold mb-1 text-yellow-800">Unmapped Carriers</h2>
          <p className="text-sm text-yellow-700 mb-4">
            The following carrier names were not found in the registry. Assign each to a known
            carrier to enable tariff benchmarking.
          </p>
          <div className="space-y-3">
            {(reviewData.parsing_issues ?? [])
              .filter((i: ParsingIssue) => i.type === 'unknown_carrier')
              .map((issue: ParsingIssue) => (
                <div
                  key={issue.carrier_name}
                  className="flex items-center gap-3 bg-white border border-yellow-200 rounded p-3"
                >
                  <span className="font-medium text-gray-900 w-48 shrink-0">
                    {issue.carrier_name}
                  </span>
                  <select
                    className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm"
                    value={carrierSelections[issue.carrier_name!] ?? ''}
                    onChange={(e) =>
                      setCarrierSelections((prev) => ({
                        ...prev,
                        [issue.carrier_name!]: e.target.value,
                      }))
                    }
                  >
                    <option value="">— Select carrier —</option>
                    {carriers.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => handleResolveCarrier(issue.carrier_name!)}
                    disabled={!carrierSelections[issue.carrier_name!] || resolving[issue.carrier_name!]}
                    className="bg-yellow-600 text-white px-4 py-1.5 rounded text-sm hover:bg-yellow-700 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {resolving[issue.carrier_name!] ? 'Saving…' : 'Resolve'}
                  </button>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* LLM Analysis */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">LLM Analysis</h2>
        <div className="space-y-3">
          <div>
            <span className="font-medium text-gray-700">File Type:</span>{' '}
            <span className="text-gray-900">{reviewData.llm_analysis.file_type}</span>
          </div>
          <div>
            <span className="font-medium text-gray-700">Confidence:</span>{' '}
            <span className={`font-semibold ${
              reviewData.llm_analysis.confidence >= 0.8 ? 'text-green-600' :
              reviewData.llm_analysis.confidence >= 0.6 ? 'text-yellow-600' :
              'text-red-600'
            }`}>
              {(reviewData.llm_analysis.confidence * 100).toFixed(0)}%
            </span>
          </div>
          <div>
            <span className="font-medium text-gray-700">Description:</span>{' '}
            <span className="text-gray-900">{reviewData.llm_analysis.description}</span>
          </div>
        </div>
      </div>

      {/* Suggested Mappings */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">Suggested Mappings</h2>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="text-left p-3 font-semibold text-gray-700">Field</th>
                <th className="text-left p-3 font-semibold text-gray-700">Source Column</th>
                <th className="text-left p-3 font-semibold text-gray-700">Confidence</th>
                <th className="text-left p-3 font-semibold text-gray-700">Sample Values</th>
              </tr>
            </thead>
            <tbody>
              {reviewData.suggested_mappings.map((mapping, i) => (
                <tr key={i} className="border-b hover:bg-gray-50">
                  <td className="p-3 font-medium text-gray-900">{mapping.field}</td>
                  <td className="p-3 text-gray-700">{mapping.column}</td>
                  <td className="p-3">
                    <span className={`font-medium ${
                      mapping.confidence >= 0.8 ? 'text-green-600' :
                      mapping.confidence >= 0.6 ? 'text-yellow-600' :
                      'text-red-600'
                    }`}>
                      {(mapping.confidence * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="p-3 text-gray-600 text-sm">
                    {mapping.sample_values.slice(0, 3).join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Data Preview */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">
          Preview (First 10 rows)
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50">
                {reviewData.preview.length > 0 &&
                  Object.keys(reviewData.preview[0]).map((key) => (
                    <th key={key} className="text-left p-2 font-semibold text-gray-700">
                      {key}
                    </th>
                  ))}
              </tr>
            </thead>
            <tbody>
              {reviewData.preview.slice(0, 10).map((row, i) => (
                <tr key={i} className="border-b hover:bg-gray-50">
                  {Object.values(row).map((val, j) => (
                    <td key={j} className="p-2 text-gray-700">
                      {String(val)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-4">
        <button
          onClick={handleAccept}
          disabled={submitting}
          className="bg-green-600 text-white px-6 py-3 rounded hover:bg-green-700 transition disabled:opacity-50 disabled:cursor-not-allowed font-medium"
        >
          {submitting ? 'Processing...' : 'Accept & Parse'}
        </button>
        <button
          onClick={handleReject}
          disabled={submitting}
          className="bg-red-600 text-white px-6 py-3 rounded hover:bg-red-700 transition disabled:opacity-50 disabled:cursor-not-allowed font-medium"
        >
          {submitting ? 'Processing...' : 'Reject'}
        </button>
        <button
          onClick={() => navigate(`/projects/${reviewData.upload.project_id}`)}
          disabled={submitting}
          className="bg-gray-500 text-white px-6 py-3 rounded hover:bg-gray-600 transition disabled:opacity-50 disabled:cursor-not-allowed font-medium"
        >
          Cancel
        </button>
      </div>
    </div>
  );
};
