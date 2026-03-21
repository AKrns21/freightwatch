import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { api } from '../api';
import type { Project, Upload, UploadCreatedResponse } from '../types';

export const ProjectDetailPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<Project | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [docType, setDocType] = useState<string>('auto');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [projectRes, uploadsRes] = await Promise.all([
        api.get<Project>(`/api/projects/${projectId}`),
        api.get<Upload[]>(`/api/uploads?project_id=${projectId}`),
      ]);
      setProject(projectRes.data);
      setUploads(uploadsRes.data);
    } catch (err: unknown) {
      console.error('Failed to load project', err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleDeleteUpload = async (upload: Upload) => {
    if (!window.confirm(`"${upload.filename}" wirklich löschen?`)) return;
    try {
      await api.delete(`/api/uploads/${upload.id}`);
      setUploads((prev) => prev.filter((u) => u.id !== upload.id));
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      alert(e.response?.data?.detail || 'Löschen fehlgeschlagen');
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setUploadError(null);
    setUploadSuccess(null);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('project_id', projectId!);
    if (docType !== 'auto') formData.append('doc_type', docType);

    try {
      const res = await api.post<UploadCreatedResponse>('/api/uploads', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadSuccess(`"${file.name}" wurde hochgeladen. Status: ${res.data.status ?? 'pending'}`);
      await loadData();
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string; message?: string } } };
      setUploadError(e.response?.data?.detail || e.response?.data?.message || 'Upload fehlgeschlagen');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center min-h-screen text-gray-600">Laden...</div>;
  }

  if (!project) {
    return <div className="flex items-center justify-center min-h-screen text-red-600">Projekt nicht gefunden</div>;
  }

  return (
    <div className="min-h-screen bg-gray-100 py-8">
      <div className="container mx-auto px-4 max-w-4xl">
        {/* Header */}
        <div className="mb-6">
          <button onClick={() => navigate('/projects')} className="text-blue-600 hover:text-blue-700 mb-4 block">
            ← Zurück zur Übersicht
          </button>
          <h1 className="text-3xl font-bold text-gray-900">{project.name}</h1>
          {project.customerName && (
            <p className="text-gray-500 mt-1">{project.customerName}</p>
          )}
          <div className="flex gap-3 mt-2">
            <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded text-sm">{project.phase}</span>
            <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded text-sm">{project.status}</span>
          </div>
        </div>

        {/* Upload Section */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Datei hochladen</h2>

          <div className="flex gap-4 items-end">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Dokumenttyp
              </label>
              <select
                value={docType}
                onChange={(e) => setDocType(e.target.value)}
                disabled={uploading}
                className="block text-sm border border-gray-300 rounded px-3 py-2 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
              >
                <option value="auto">Automatisch erkennen</option>
                <option value="invoice">Rechnung / Invoice</option>
                <option value="tariff">Tarif / Preisliste</option>
                <option value="shipment_csv">Sendungsliste (CSV/XLSX)</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Datei (PDF, Excel, CSV — max. 10 MB)
              </label>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.xls,.xlsx,.csv"
                onChange={handleFileUpload}
                disabled={uploading}
                className="block text-sm text-gray-600 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:bg-blue-600 file:text-white hover:file:bg-blue-700 disabled:opacity-50"
              />
            </div>
          </div>

          {uploading && <p className="mt-3 text-blue-600 text-sm">Wird hochgeladen und verarbeitet...</p>}
          {uploadSuccess && <p className="mt-3 text-green-700 text-sm bg-green-50 p-3 rounded">{uploadSuccess}</p>}
          {uploadError && <p className="mt-3 text-red-700 text-sm bg-red-50 p-3 rounded">{uploadError}</p>}
        </div>

        {/* Uploads List */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Hochgeladene Dateien ({uploads.length})</h2>

          {uploads.length === 0 ? (
            <p className="text-gray-500 text-sm">Noch keine Dateien hochgeladen.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-gray-500">
                  <th className="pb-2 pr-4">Dateiname</th>
                  <th className="pb-2 pr-4">Typ</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {uploads.map((upload) => (
                  <tr key={upload.id} className="border-b last:border-0">
                    <td className="py-3 pr-4 font-medium text-gray-800">{upload.filename}</td>
                    <td className="py-3 pr-4 text-gray-500">{upload.mimeType ?? '—'}</td>
                    <td className="py-3 pr-4">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                        upload.status === 'parsed' ? 'bg-green-100 text-green-700' :
                        upload.status === 'failed' ? 'bg-red-100 text-red-700' :
                        upload.status === 'needs_review' ? 'bg-yellow-100 text-yellow-700' :
                        upload.status === 'needs_manual_review' ? 'bg-orange-100 text-orange-700' :
                        upload.status === 'partial_success' ? 'bg-blue-100 text-blue-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {upload.status}
                      </span>
                    </td>
                    <td className="py-3 flex gap-3 items-center">
                      {upload.status === 'needs_review' && (
                        <Link
                          to={`/uploads/${upload.id}/review`}
                          className="text-blue-600 hover:text-blue-700 text-xs"
                        >
                          Prüfen →
                        </Link>
                      )}
                      <Link
                        to={upload.docType === 'diesel_floater'
                          ? `/uploads/${upload.id}/diesel-floater`
                          : `/uploads/${upload.id}/detail`}
                        className="text-gray-500 hover:text-gray-700 text-xs"
                      >
                        Detail →
                      </Link>
                      <button
                        onClick={() => handleDeleteUpload(upload)}
                        className="text-red-500 hover:text-red-700 text-xs"
                        title="Löschen"
                      >
                        Löschen
                      </button>
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
