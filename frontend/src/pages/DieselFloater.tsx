import React, { useEffect, useState, useRef } from 'react';
import { api } from '../api';

interface Carrier {
  id: string;
  name: string;
  codeNorm: string;
}

interface DieselFloaterEntry {
  id: string;
  carrierId: string;
  carrierName: string | null;
  validFrom: string;
  validUntil: string | null;
  floaterPct: string;
  basis: string;
  source: string | null;
}

type Mode = 'list' | 'add' | 'edit' | 'csv';

const BASIS_LABELS: Record<string, string> = {
  base: 'Fracht (base)',
  base_plus_toll: 'Fracht + Maut',
  total: 'Gesamt',
};

export const DieselFloaterPage: React.FC = () => {
  const [entries, setEntries] = useState<DieselFloaterEntry[]>([]);
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('list');
  const [editing, setEditing] = useState<DieselFloaterEntry | null>(null);
  const [filterCarrierId, setFilterCarrierId] = useState('');

  // Form state
  const [form, setForm] = useState({
    carrierId: '',
    validFrom: '',
    validUntil: '',
    floaterPct: '',
    basis: 'base',
    source: '',
  });
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // CSV import state
  const [csvCarrierId, setCsvCarrierId] = useState('');
  const [csvSource, setCsvSource] = useState('');
  const [csvText, setCsvText] = useState('');
  const [csvResult, setCsvResult] = useState<{
    inserted: number; updated: number; skipped: number; errors: string[];
  } | null>(null);
  const [csvImporting, setCsvImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [entriesRes, carriersRes] = await Promise.all([
        api.get<DieselFloaterEntry[]>('/api/diesel-floaters'),
        api.get<Carrier[]>('/api/carriers'),
      ]);
      setEntries(entriesRes.data);
      setCarriers(carriersRes.data);
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Laden fehlgeschlagen');
    } finally {
      setLoading(false);
    }
  };

  const loadEntries = async (carrierId?: string) => {
    const url = carrierId
      ? `/api/diesel-floaters?carrier_id=${carrierId}`
      : '/api/diesel-floaters';
    const res = await api.get<DieselFloaterEntry[]>(url);
    setEntries(res.data);
  };

  const openAdd = () => {
    setForm({ carrierId: filterCarrierId, validFrom: '', validUntil: '', floaterPct: '', basis: 'base', source: '' });
    setFormError(null);
    setEditing(null);
    setMode('add');
  };

  const openEdit = (entry: DieselFloaterEntry) => {
    setForm({
      carrierId: entry.carrierId,
      validFrom: entry.validFrom,
      validUntil: entry.validUntil ?? '',
      floaterPct: entry.floaterPct,
      basis: entry.basis,
      source: entry.source ?? '',
    });
    setFormError(null);
    setEditing(entry);
    setMode('edit');
  };

  const saveForm = async () => {
    if (!form.carrierId || !form.validFrom || !form.floaterPct) {
      setFormError('Spediteur, Gültig ab und Prozentsatz sind Pflichtfelder.');
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const payload = {
        carrierId: form.carrierId,
        validFrom: form.validFrom,
        validUntil: form.validUntil || null,
        floaterPct: parseFloat(form.floaterPct),
        basis: form.basis,
        source: form.source || null,
      };
      if (mode === 'edit' && editing) {
        await api.put(`/api/diesel-floaters/${editing.id}`, payload);
      } else {
        await api.post('/api/diesel-floaters', payload);
      }
      await loadEntries(filterCarrierId || undefined);
      setMode('list');
    } catch (e: any) {
      setFormError(e.response?.data?.detail || 'Speichern fehlgeschlagen');
    } finally {
      setSaving(false);
    }
  };

  const deleteEntry = async (id: string) => {
    if (!confirm('Eintrag löschen?')) return;
    try {
      await api.delete(`/api/diesel-floaters/${id}`);
      setEntries(prev => prev.filter(e => e.id !== id));
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Löschen fehlgeschlagen');
    }
  };

  const handleCsvFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => setCsvText(ev.target?.result as string ?? '');
    reader.readAsText(file);
  };

  const importCsv = async () => {
    if (!csvCarrierId || !csvText.trim()) {
      alert('Bitte Spediteur und CSV-Inhalt angeben.');
      return;
    }
    setCsvImporting(true);
    setCsvResult(null);
    try {
      const res = await api.post<typeof csvResult>('/api/diesel-floaters/import-csv', {
        carrierId: csvCarrierId,
        csvText,
        source: csvSource || null,
      });
      setCsvResult(res.data);
      await loadEntries(filterCarrierId || undefined);
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Import fehlgeschlagen');
    } finally {
      setCsvImporting(false);
    }
  };

  const filteredEntries = filterCarrierId
    ? entries.filter(e => e.carrierId === filterCarrierId)
    : entries;

  if (loading) {
    return <div className="flex items-center justify-center min-h-screen"><div className="text-gray-600">Laden…</div></div>;
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-red-600">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dieselfloater</h1>
          <p className="text-sm text-gray-500 mt-1">Dieselzuschlag-Historie pro Spediteur</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { setMode('csv'); setCsvResult(null); }}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            CSV-Import
          </button>
          <button
            onClick={openAdd}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            + Neuer Eintrag
          </button>
        </div>
      </div>

      {/* Add / Edit form */}
      {(mode === 'add' || mode === 'edit') && (
        <div className="bg-white rounded-lg shadow border border-gray-200 p-6 mb-6">
          <h2 className="text-lg font-semibold mb-4">{mode === 'edit' ? 'Eintrag bearbeiten' : 'Neuer Eintrag'}</h2>
          {formError && <div className="bg-red-50 border border-red-200 rounded p-3 mb-4 text-red-700 text-sm">{formError}</div>}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Spediteur *</label>
              <select
                value={form.carrierId}
                onChange={e => setForm(f => ({ ...f, carrierId: e.target.value }))}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                <option value="">Bitte wählen…</option>
                {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Basis</label>
              <select
                value={form.basis}
                onChange={e => setForm(f => ({ ...f, basis: e.target.value }))}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                {Object.entries(BASIS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Gültig ab *</label>
              <input
                type="date"
                value={form.validFrom}
                onChange={e => setForm(f => ({ ...f, validFrom: e.target.value }))}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Gültig bis</label>
              <input
                type="date"
                value={form.validUntil}
                onChange={e => setForm(f => ({ ...f, validUntil: e.target.value }))}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Prozentsatz (%) *</label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="99.99"
                value={form.floaterPct}
                onChange={e => setForm(f => ({ ...f, floaterPct: e.target.value }))}
                placeholder="z.B. 18.50"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Quelle</label>
              <input
                type="text"
                value={form.source}
                onChange={e => setForm(f => ({ ...f, source: e.target.value }))}
                placeholder="z.B. Spediteursschreiben 01/2023"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>
          </div>
          <div className="flex gap-3 mt-5">
            <button
              onClick={saveForm}
              disabled={saving}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? 'Speichern…' : 'Speichern'}
            </button>
            <button
              onClick={() => setMode('list')}
              className="px-5 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Abbrechen
            </button>
          </div>
        </div>
      )}

      {/* CSV Import panel */}
      {mode === 'csv' && (
        <div className="bg-white rounded-lg shadow border border-gray-200 p-6 mb-6">
          <h2 className="text-lg font-semibold mb-1">CSV-Import</h2>
          <p className="text-sm text-gray-500 mb-4">
            Erwartete Spalten (mit Kopfzeile):{' '}
            <code className="bg-gray-100 px-1 rounded">valid_from, valid_until, floater_pct[, basis][, source]</code>
            <br />
            Datumsformat: TT.MM.JJJJ oder JJJJ-MM-TT. Bei doppeltem Eintrag (Spediteur + Datum) wird aktualisiert.
          </p>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Spediteur *</label>
              <select
                value={csvCarrierId}
                onChange={e => setCsvCarrierId(e.target.value)}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                <option value="">Bitte wählen…</option>
                {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Quelle (Standard)</label>
              <input
                type="text"
                value={csvSource}
                onChange={e => setCsvSource(e.target.value)}
                placeholder="z.B. Spediteursschreiben"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">CSV-Datei</label>
            <input ref={fileInputRef} type="file" accept=".csv,.txt" onChange={handleCsvFile} className="text-sm" />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">CSV-Inhalt (direkt einfügen)</label>
            <textarea
              value={csvText}
              onChange={e => setCsvText(e.target.value)}
              rows={8}
              placeholder="valid_from,valid_until,floater_pct&#10;01.01.2023,31.03.2023,18.50&#10;01.04.2023,,19.00"
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono"
            />
          </div>
          {csvResult && (
            <div className={`rounded p-3 mb-4 text-sm ${csvResult.errors.length > 0 ? 'bg-yellow-50 border border-yellow-200' : 'bg-green-50 border border-green-200'}`}>
              <p className="font-medium">
                Import abgeschlossen: {csvResult.inserted} neu, {csvResult.updated} aktualisiert, {csvResult.skipped} übersprungen
              </p>
              {csvResult.errors.map((e, i) => <p key={i} className="text-red-600 mt-1">{e}</p>)}
            </div>
          )}
          <div className="flex gap-3">
            <button
              onClick={importCsv}
              disabled={csvImporting}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {csvImporting ? 'Importieren…' : 'Importieren'}
            </button>
            <button
              onClick={() => setMode('list')}
              className="px-5 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Zurück
            </button>
          </div>
        </div>
      )}

      {/* Filter */}
      {mode === 'list' && (
        <div className="flex items-center gap-3 mb-4">
          <label className="text-sm font-medium text-gray-700">Filter Spediteur:</label>
          <select
            value={filterCarrierId}
            onChange={e => setFilterCarrierId(e.target.value)}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm"
          >
            <option value="">Alle</option>
            {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <span className="text-sm text-gray-400">{filteredEntries.length} Einträge</span>
        </div>
      )}

      {/* Table */}
      {mode === 'list' && (
        <div className="bg-white rounded-lg shadow overflow-hidden border border-gray-200">
          {filteredEntries.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <p className="text-lg mb-2">Keine Einträge vorhanden</p>
              <p className="text-sm">Fügen Sie Dieselfloater-Daten manuell oder per CSV-Import hinzu.</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Spediteur</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Gültig ab</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Gültig bis</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">%</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Basis</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Quelle</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filteredEntries.map(entry => (
                  <tr key={entry.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-900">{entry.carrierName ?? entry.carrierId}</td>
                    <td className="px-4 py-3 text-gray-700">{entry.validFrom}</td>
                    <td className="px-4 py-3 text-gray-500">{entry.validUntil ?? '—'}</td>
                    <td className="px-4 py-3 text-right font-mono font-medium text-gray-900">
                      {parseFloat(entry.floaterPct).toFixed(2)} %
                    </td>
                    <td className="px-4 py-3 text-gray-600">{BASIS_LABELS[entry.basis] ?? entry.basis}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{entry.source ?? '—'}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2 justify-end">
                        <button
                          onClick={() => openEdit(entry)}
                          className="text-blue-600 hover:text-blue-800 text-xs font-medium"
                        >
                          Bearbeiten
                        </button>
                        <button
                          onClick={() => deleteEntry(entry.id)}
                          className="text-red-500 hover:text-red-700 text-xs font-medium"
                        >
                          Löschen
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
};
