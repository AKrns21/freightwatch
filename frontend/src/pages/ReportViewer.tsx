import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Report, ApiResponse } from '../types';

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

  useEffect(() => {
    if (projectId) {
      loadReport();
    }
  }, [projectId, reportId]);

  const loadReport = async () => {
    try {
      setLoading(true);
      setError(null);

      let response;
      if (reportId) {
        // Load specific report by ID
        response = await api.get<ApiResponse<Report>>(
          `/api/reports/${reportId}`
        );
      } else {
        // Load latest report for project
        response = await api.get<ApiResponse<Report>>(
          `/api/reports/latest?projectId=${projectId}`
        );
      }

      setReport(response.data.data);
    } catch (err: any) {
      setError(err.response?.data?.error?.message || 'Failed to load report');
      console.error('Failed to load report:', err);
    } finally {
      setLoading(false);
    }
  };

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

  const { data_snapshot } = report;
  const statistics = data_snapshot.statistics;

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
              {new Date(report.generated_at).toLocaleDateString()} at{' '}
              {new Date(report.generated_at).toLocaleTimeString()}
            </p>
            {report.date_range_start && report.date_range_end && (
              <p className="text-gray-600">
                Data Period: {new Date(report.date_range_start).toLocaleDateString()} -{' '}
                {new Date(report.date_range_end).toLocaleDateString()}
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
              {statistics.total_shipments.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Total Actual</div>
            <div className="text-2xl font-bold text-gray-900">
              €{statistics.total_actual_cost.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Total Expected</div>
            <div className="text-2xl font-bold text-gray-900">
              €{statistics.total_expected_cost.toLocaleString()}
            </div>
          </div>
          <div className="bg-green-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Savings Potential</div>
            <div className="text-2xl font-bold text-green-600">
              €{statistics.total_savings_potential.toLocaleString()}
            </div>
          </div>
        </div>

        {/* Data Quality Metrics */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
          <div className="bg-blue-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Data Completeness</div>
            <div className="text-2xl font-bold text-blue-600">
              {(report.data_completeness * 100).toFixed(0)}%
            </div>
          </div>
          <div className="bg-purple-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Overpay Rate</div>
            <div className="text-2xl font-bold text-purple-600">
              {statistics.overpay_rate.toFixed(1)}%
            </div>
          </div>
          <div className="bg-yellow-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Benchmarked</div>
            <div className="text-2xl font-bold text-yellow-600">
              {statistics.benchmarked_shipments.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-50 p-4 rounded">
            <div className="text-gray-600 text-sm mb-1">Complete Data</div>
            <div className="text-2xl font-bold text-gray-900">
              {statistics.complete_shipments.toLocaleString()}
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
              {statistics.carriers.map((carrier, i) => (
                <tr key={i} className="border-b hover:bg-gray-50">
                  <td className="p-3 font-medium text-gray-900">{carrier.carrier_name}</td>
                  <td className="text-right p-3 text-gray-700">
                    {carrier.shipment_count.toLocaleString()}
                  </td>
                  <td className="text-right p-3 text-gray-700">
                    €{carrier.total_actual_cost.toLocaleString()}
                  </td>
                  <td className="text-right p-3 text-gray-700">
                    €{carrier.total_expected_cost.toLocaleString()}
                  </td>
                  <td
                    className={`text-right p-3 font-medium ${
                      carrier.total_delta > 0 ? 'text-red-600' : 'text-green-600'
                    }`}
                  >
                    €{carrier.total_delta.toLocaleString()}
                  </td>
                  <td
                    className={`text-right p-3 font-medium ${
                      carrier.avg_delta_pct > 0 ? 'text-red-600' : 'text-green-600'
                    }`}
                  >
                    {carrier.avg_delta_pct > 0 ? '+' : ''}
                    {carrier.avg_delta_pct.toFixed(1)}%
                  </td>
                  <td className="text-right p-3 text-gray-700">
                    {carrier.overpay_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Top Overpayments (Quick Wins) */}
      {data_snapshot.top_overpays && data_snapshot.top_overpays.length > 0 && (
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
                {data_snapshot.top_overpays.map((overpay, i) => (
                  <tr key={i} className="border-b hover:bg-gray-50">
                    <td className="p-3 text-gray-700">
                      {new Date(overpay.date).toLocaleDateString()}
                    </td>
                    <td className="p-3 text-gray-700">{overpay.carrier}</td>
                    <td className="p-3 text-gray-700">
                      {overpay.origin_zip} → {overpay.dest_zip}
                    </td>
                    <td className="text-right p-3 text-gray-700">
                      €{overpay.actual_cost.toFixed(2)}
                    </td>
                    <td className="text-right p-3 text-gray-700">
                      €{overpay.expected_cost.toFixed(2)}
                    </td>
                    <td className="text-right p-3 font-medium text-red-600">
                      €{overpay.delta.toFixed(2)}
                    </td>
                    <td className="text-right p-3 font-medium text-red-600">
                      +{overpay.delta_pct.toFixed(1)}%
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
