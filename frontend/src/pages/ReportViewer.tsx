import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Report } from '../types';

/**
 * ReportViewerPage - Display Cost Analysis Report (Phase 7.3)
 *
 * Shows versioned report with:
 * - Summary statistics (total shipments, costs, savings potential)
 * - Carrier-level breakdown
 * - Top overpayment opportunities (quick wins)
 * - Data completeness metrics
 */
export const ReportViewerPage: React.FC = () => {
  const { projectId, reportId } = useParams<{ projectId: string; reportId?: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadReport = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      let response;
      if (reportId) {
        response = await api.get<Report>(`/api/reports/${reportId}`);
      } else {
        response = await api.get<Report>(`/api/reports/latest?projectId=${projectId}`);
      }

      setReport(response.data);
    } catch (err: unknown) {
      const e = err as { response?: { data?: { error?: { message?: string } } } };
      setError(e.response?.data?.error?.message || 'Failed to load report');
      console.error('Failed to load report:', err);
    } finally {
      setLoading(false);
    }
  }, [projectId, reportId]);

  useEffect(() => {
    if (projectId) {
      loadReport();
    }
  }, [projectId, loadReport]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-xl text-gray-600">Loading report...</div>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="container mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <h2 className="text-red-800 font-semibold mb-2">Error Loading Report</h2>
          <p className="text-red-600">{error || 'Report not found'}</p>
          <div className="flex gap-2 mt-4">
            <button
              onClick={loadReport}
              className="bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700"
            >
              Retry
            </button>
            <button
              onClick={() => navigate(`/projects/${projectId}`)}
              className="bg-gray-500 text-white px-4 py-2 rounded hover:bg-gray-600"
            >
              Back to Project
            </button>
          </div>
        </div>
      </div>
    );
  }

  const dataSnapshot = report.dataSnapshot;
  const statistics = dataSnapshot?.statistics;

  return (
    <div className="container mx-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex justify-between items-start">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">
              {report.title}
            </h1>
            <p className="text-gray-600">
              Version {report.version} • Generated{' '}
              {report.generatedAt ? new Date(report.generatedAt).toLocaleDateString() : '—'} at{' '}
              {report.generatedAt ? new Date(report.generatedAt).toLocaleTimeString() : '—'}
            </p>
            {report.dateRangeStart && report.dateRangeEnd && (
              <p className="text-gray-600">
                Data Period: {new Date(report.dateRangeStart).toLocaleDateString()} -{' '}
                {new Date(report.dateRangeEnd).toLocaleDateString()}
              </p>
            )}
          </div>
          <button
            onClick={() => navigate(`/projects/${projectId}`)}
            className="bg-gray-500 text-white px-4 py-2 rounded hover:bg-gray-600"
          >
            Back to Project
          </button>
        </div>
      </div>

      {/* Summary Statistics */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">Summary</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Total Shipments</div>
            <div className="text-2xl font-bold text-gray-900">
              {statistics?.totalShipments.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Total Actual</div>
            <div className="text-2xl font-bold text-gray-900">
              €{statistics?.totalActualCost.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Total Expected</div>
            <div className="text-2xl font-bold text-gray-900">
              €{statistics?.totalExpectedCost.toLocaleString()}
            </div>
          </div>
          <div className="bg-green-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Savings Potential</div>
            <div className="text-2xl font-bold text-green-600">
              €{statistics?.totalSavingsPotential.toLocaleString()}
            </div>
          </div>
        </div>

        {/* Data Quality Metrics */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
          <div className="bg-blue-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Data Completeness</div>
            <div className="text-2xl font-bold text-blue-600">
              {report.dataCompleteness != null ? (report.dataCompleteness * 100).toFixed(0) : '—'}%
            </div>
          </div>
          <div className="bg-purple-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Overpay Rate</div>
            <div className="text-2xl font-bold text-purple-600">
              {statistics?.overpayRate.toFixed(1)}%
            </div>
          </div>
          <div className="bg-yellow-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Benchmarked</div>
            <div className="text-2xl font-bold text-yellow-600">
              {statistics?.benchmarkedShipments.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Complete Data</div>
            <div className="text-2xl font-bold text-gray-900">
              {statistics?.completeShipments.toLocaleString()}
            </div>
          </div>
        </div>
      </div>

      {/* Carrier Breakdown */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4 text-gray-900">Breakdown by Carrier</h2>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b bg-gray-50">
                <th className="text-left p-3 font-semibold text-gray-700">Carrier</th>
                <th className="text-right p-3 font-semibold text-gray-700">Shipments</th>
                <th className="text-right p-3 font-semibold text-gray-700">Actual</th>
                <th className="text-right p-3 font-semibold text-gray-700">Expected</th>
                <th className="text-right p-3 font-semibold text-gray-700">Delta</th>
                <th className="text-right p-3 font-semibold text-gray-700">Avg %</th>
                <th className="text-right p-3 font-semibold text-gray-700">Overpays</th>
              </tr>
            </thead>
            <tbody>
              {statistics?.carriers.map((carrier, i) => (
                <tr key={i} className="border-b hover:bg-gray-50">
                  <td className="p-3 font-medium text-gray-900">{carrier.carrierName}</td>
                  <td className="text-right p-3 text-gray-700">
                    {carrier.shipmentCount.toLocaleString()}
                  </td>
                  <td className="text-right p-3 text-gray-700">
                    €{carrier.totalActualCost.toLocaleString()}
                  </td>
                  <td className="text-right p-3 text-gray-700">
                    €{carrier.totalExpectedCost.toLocaleString()}
                  </td>
                  <td
                    className={`text-right p-3 font-medium ${
                      carrier.totalDelta > 0 ? 'text-red-600' : 'text-green-600'
                    }`}
                  >
                    €{carrier.totalDelta.toLocaleString()}
                  </td>
                  <td
                    className={`text-right p-3 font-medium ${
                      carrier.avgDeltaPct > 0 ? 'text-red-600' : 'text-green-600'
                    }`}
                  >
                    {carrier.avgDeltaPct > 0 ? '+' : ''}
                    {carrier.avgDeltaPct.toFixed(1)}%
                  </td>
                  <td className="text-right p-3 text-gray-700">
                    {carrier.overpayCount}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Top Overpayments (Quick Wins) */}
      {dataSnapshot?.topOverpays && dataSnapshot.topOverpays.length > 0 && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold mb-4 text-gray-900">
            Top Overpayment Opportunities
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50">
                  <th className="text-left p-3 font-semibold text-gray-700">Date</th>
                  <th className="text-left p-3 font-semibold text-gray-700">Carrier</th>
                  <th className="text-left p-3 font-semibold text-gray-700">Route</th>
                  <th className="text-right p-3 font-semibold text-gray-700">Actual</th>
                  <th className="text-right p-3 font-semibold text-gray-700">Expected</th>
                  <th className="text-right p-3 font-semibold text-gray-700">Delta</th>
                  <th className="text-right p-3 font-semibold text-gray-700">%</th>
                </tr>
              </thead>
              <tbody>
                {dataSnapshot.topOverpays!.map((overpay, i) => (
                  <tr key={i} className="border-b hover:bg-gray-50">
                    <td className="p-3 text-gray-700">
                      {new Date(overpay.date).toLocaleDateString()}
                    </td>
                    <td className="p-3 text-gray-700">{overpay.carrier}</td>
                    <td className="p-3 text-gray-700">
                      {overpay.originZip} → {overpay.destZip}
                    </td>
                    <td className="text-right p-3 text-gray-700">
                      €{overpay.actualCost.toFixed(2)}
                    </td>
                    <td className="text-right p-3 text-gray-700">
                      €{overpay.expectedCost.toFixed(2)}
                    </td>
                    <td className="text-right p-3 font-medium text-red-600">
                      €{overpay.delta.toFixed(2)}
                    </td>
                    <td className="text-right p-3 font-medium text-red-600">
                      +{overpay.deltaPct.toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
