import React, { useEffect, useState, useRef } from 'react';
import { api } from '../api';

interface Carrier { id: string; name: string; codeNorm: string; }

interface DieselFloaterEntry {
  id: string; carrierId: string; carrierName: string | null;
  validFrom: string; validUntil: string | null;
  floaterPct: string; basis: string; source: string | null;
}

interface DieselBracket {
  id: string; carrierId: string; carrierName: string | null;
  priceCtMax: string; floaterPct: string; basis: string;
  validFrom: string; validUntil: string | null;
}

interface DestatisPriceEntry {
  year: number; month: number; priceCt: string;
  seriesCode: string; fetchedAt: string;
}

type Tab = 'brackets' | 'rates' | 'destatis';

const BASIS_LABELS: Record<string, string> = {
  base: 'Fracht', base_plus_toll: 'Fracht + Maut', total: 'Gesamt',
};

const MONTH_NAMES = ['Jan','Feb','Mär','Apr','Mai','Jun','Jul','Aug','Sep','Okt','Nov','Dez'];

export const DieselFloaterPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('brackets');
  const [entries, setEntries] = useState<DieselFloaterEntry[]>([]);
  const [brackets, setBrackets] = useState<DieselBracket[]>([]);
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [entriesRes, carriersRes, bracketsRes] = await Promise.all([
        api.get<DieselFloaterEntry[]>('/api/diesel-floaters'),
        api.get<Carrier[]>('/api/carriers'),
        api.get<DieselBracket[]>('/api/diesel-floaters/brackets'),
      ]);
      setEntries(entriesRes.data);
      setCarriers(carriersRes.data);
      setBrackets(bracketsRes.data);
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Laden fehlgeschlagen');
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="flex items-center justify-center min-h-screen"><div className="text-gray-600">Laden…</div></div>;
  if (error) return <div className="container mx-auto p-6"><div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-600">{error}</div></div>;

  return (
    <div className="container mx-auto p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dieselfloater</h1>
          <p className="text-sm text-gray-500 mt-1">Preisklassen und manuelle Zuschlagssätze pro Spediteur</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 mb-6">
        {(['brackets', 'rates', 'destatis'] as Tab[]).map(key => {
          const labels: Record<Tab, string> = { brackets: 'Preisklassen', rates: 'Manuelle Sätze', destatis: 'Destatis-Preise' };
          const counts: Record<Tab, number> = { brackets: brackets.length, rates: entries.length, destatis: 0 };
          return (
            <button key={key} onClick={() => setTab(key)}
              className={`px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === key ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
            >
              {labels[key]}
              {counts[key] > 0 && <span className="ml-2 text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full">{counts[key]}</span>}
            </button>
          );
        })}
      </div>

      {tab === 'brackets' && <BracketsTab brackets={brackets} carriers={carriers} />}
      {tab === 'rates'    && <RatesTab entries={entries} carriers={carriers} onRefresh={loadAll} />}
      {tab === 'destatis' && <DestatisPricesTab />}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Brackets tab — grouped by carrier
// ---------------------------------------------------------------------------

const BracketsTab: React.FC<{ brackets: DieselBracket[]; carriers: Carrier[] }> = ({ brackets, carriers }) => {
  const [filterCarrierId, setFilterCarrierId] = useState('');

  const filtered = filterCarrierId ? brackets.filter(b => b.carrierId === filterCarrierId) : brackets;

  // Group by carrier
  const groups = filtered.reduce<Record<string, DieselBracket[]>>((acc, b) => {
    const key = b.carrierId;
    if (!acc[key]) acc[key] = [];
    acc[key].push(b);
    return acc;
  }, {});

  return (
    <>
      <div className="flex items-center gap-3 mb-4">
        <label className="text-sm font-medium text-gray-700">Spediteur:</label>
        <select value={filterCarrierId} onChange={e => setFilterCarrierId(e.target.value)}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm">
          <option value="">Alle</option>
          {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <span className="text-sm text-gray-400">{filtered.length} Zeilen</span>
      </div>

      {Object.keys(groups).length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 text-center py-12 text-gray-500">
          <p className="text-lg mb-2">Keine Preisklassen vorhanden</p>
          <p className="text-sm">Laden Sie ein Dieselfloater-PDF hoch — die Tabelle wird automatisch erkannt und importiert.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(groups).map(([carrierId, rows]) => {
            const carrierName = rows[0].carrierName ?? carrierId;
            const basis = BASIS_LABELS[rows[0].basis] ?? rows[0].basis;
            const validFrom = rows[0].validFrom;
            const validUntil = rows[0].validUntil;
            return (
              <div key={carrierId} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
                  <span className="font-semibold text-gray-900">{carrierName}</span>
                  <div className="flex items-center gap-4 text-xs text-gray-500">
                    <span>Basis: {basis}</span>
                    <span>Gültig ab: {validFrom}</span>
                    {validUntil && <span>bis: {validUntil}</span>}
                    <span className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full font-medium">
                      {rows.length} Stufen
                    </span>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 border-b border-gray-100">
                      <tr>
                        <th className="text-right px-4 py-2 font-medium text-gray-500">Dieselpreis ≤ (Ct/l)</th>
                        <th className="text-right px-4 py-2 font-medium text-gray-500">Zuschlag %</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {rows.map(b => (
                        <tr key={b.id} className="hover:bg-blue-50/30">
                          <td className="px-4 py-1.5 text-right font-mono text-gray-700">
                            {parseFloat(b.priceCtMax).toFixed(0)}
                          </td>
                          <td className={`px-4 py-1.5 text-right font-mono font-semibold ${parseFloat(b.floaterPct) === 0 ? 'text-gray-400' : 'text-gray-900'}`}>
                            {parseFloat(b.floaterPct).toFixed(2)} %
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
};

// ---------------------------------------------------------------------------
// Rates tab — manual date-range overrides
// ---------------------------------------------------------------------------

type RateMode = 'list' | 'add' | 'edit' | 'csv';

const RatesTab: React.FC<{ entries: DieselFloaterEntry[]; carriers: Carrier[]; onRefresh: () => void }> = ({ entries, carriers, onRefresh }) => {
  const [mode, setMode] = useState<RateMode>('list');
  const [editing, setEditing] = useState<DieselFloaterEntry | null>(null);
  const [filterCarrierId, setFilterCarrierId] = useState('');
  const [form, setForm] = useState({ carrierId: '', validFrom: '', validUntil: '', floaterPct: '', basis: 'base', source: '' });
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [csvCarrierId, setCsvCarrierId] = useState('');
  const [csvSource, setCsvSource] = useState('');
  const [csvText, setCsvText] = useState('');
  const [csvResult, setCsvResult] = useState<{ inserted: number; updated: number; skipped: number; errors: string[] } | null>(null);
  const [csvImporting, setCsvImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filtered = filterCarrierId ? entries.filter(e => e.carrierId === filterCarrierId) : entries;

  const openAdd = () => { setForm({ carrierId: filterCarrierId, validFrom: '', validUntil: '', floaterPct: '', basis: 'base', source: '' }); setFormError(null); setEditing(null); setMode('add'); };
  const openEdit = (e: DieselFloaterEntry) => { setForm({ carrierId: e.carrierId, validFrom: e.validFrom, validUntil: e.validUntil ?? '', floaterPct: e.floaterPct, basis: e.basis, source: e.source ?? '' }); setFormError(null); setEditing(e); setMode('edit'); };

  const saveForm = async () => {
    if (!form.carrierId || !form.validFrom || !form.floaterPct) { setFormError('Spediteur, Gültig ab und Prozentsatz sind Pflichtfelder.'); return; }
    setSaving(true); setFormError(null);
    try {
      const payload = { carrierId: form.carrierId, validFrom: form.validFrom, validUntil: form.validUntil || null, floaterPct: parseFloat(form.floaterPct), basis: form.basis, source: form.source || null };
      if (mode === 'edit' && editing) await api.put(`/api/diesel-floaters/${editing.id}`, payload);
      else await api.post('/api/diesel-floaters', payload);
      onRefresh(); setMode('list');
    } catch (e: any) { setFormError(e.response?.data?.detail || 'Speichern fehlgeschlagen'); }
    finally { setSaving(false); }
  };

  const deleteEntry = async (id: string) => {
    if (!confirm('Eintrag löschen?')) return;
    try { await api.delete(`/api/diesel-floaters/${id}`); onRefresh(); }
    catch (e: any) { alert(e.response?.data?.detail || 'Löschen fehlgeschlagen'); }
  };

  const importCsv = async () => {
    if (!csvCarrierId || !csvText.trim()) { alert('Bitte Spediteur und CSV-Inhalt angeben.'); return; }
    setCsvImporting(true); setCsvResult(null);
    try {
      const res = await api.post<typeof csvResult>('/api/diesel-floaters/import-csv', { carrierId: csvCarrierId, csvText, source: csvSource || null });
      setCsvResult(res.data); onRefresh();
    } catch (e: any) { alert(e.response?.data?.detail || 'Import fehlgeschlagen'); }
    finally { setCsvImporting(false); }
  };

  if (mode === 'add' || mode === 'edit') return (
    <div className="bg-white rounded-lg shadow border border-gray-200 p-6">
      <h2 className="text-lg font-semibold mb-4">{mode === 'edit' ? 'Eintrag bearbeiten' : 'Neuer Eintrag'}</h2>
      {formError && <div className="bg-red-50 border border-red-200 rounded p-3 mb-4 text-red-700 text-sm">{formError}</div>}
      <div className="grid grid-cols-2 gap-4">
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Spediteur *</label>
          <select value={form.carrierId} onChange={e => setForm(f => ({ ...f, carrierId: e.target.value }))} className="w-full border border-gray-300 rounded px-3 py-2 text-sm">
            <option value="">Bitte wählen…</option>
            {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select></div>
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Basis</label>
          <select value={form.basis} onChange={e => setForm(f => ({ ...f, basis: e.target.value }))} className="w-full border border-gray-300 rounded px-3 py-2 text-sm">
            {Object.entries(BASIS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Gültig ab *</label>
          <input type="date" value={form.validFrom} onChange={e => setForm(f => ({ ...f, validFrom: e.target.value }))} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" /></div>
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Gültig bis</label>
          <input type="date" value={form.validUntil} onChange={e => setForm(f => ({ ...f, validUntil: e.target.value }))} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" /></div>
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Prozentsatz (%) *</label>
          <input type="number" step="0.01" min="0" max="99.99" value={form.floaterPct} onChange={e => setForm(f => ({ ...f, floaterPct: e.target.value }))} placeholder="z.B. 18.50" className="w-full border border-gray-300 rounded px-3 py-2 text-sm" /></div>
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Quelle</label>
          <input type="text" value={form.source} onChange={e => setForm(f => ({ ...f, source: e.target.value }))} placeholder="z.B. Spediteursschreiben 01/2023" className="w-full border border-gray-300 rounded px-3 py-2 text-sm" /></div>
      </div>
      <div className="flex gap-3 mt-5">
        <button onClick={saveForm} disabled={saving} className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">{saving ? 'Speichern…' : 'Speichern'}</button>
        <button onClick={() => setMode('list')} className="px-5 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">Abbrechen</button>
      </div>
    </div>
  );

  if (mode === 'csv') return (
    <div className="bg-white rounded-lg shadow border border-gray-200 p-6">
      <h2 className="text-lg font-semibold mb-1">CSV-Import</h2>
      <p className="text-sm text-gray-500 mb-4">Spalten: <code className="bg-gray-100 px-1 rounded">valid_from, valid_until, floater_pct[, basis][, source]</code></p>
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Spediteur *</label>
          <select value={csvCarrierId} onChange={e => setCsvCarrierId(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm">
            <option value="">Bitte wählen…</option>
            {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select></div>
        <div><label className="block text-sm font-medium text-gray-700 mb-1">Quelle (Standard)</label>
          <input type="text" value={csvSource} onChange={e => setCsvSource(e.target.value)} className="w-full border border-gray-300 rounded px-3 py-2 text-sm" /></div>
      </div>
      <div className="mb-3"><label className="block text-sm font-medium text-gray-700 mb-1">CSV-Datei</label>
        <input ref={fileInputRef} type="file" accept=".csv,.txt" onChange={e => { const f = e.target.files?.[0]; if (f) { const r = new FileReader(); r.onload = ev => setCsvText(ev.target?.result as string ?? ''); r.readAsText(f); } }} className="text-sm" /></div>
      <div className="mb-4"><label className="block text-sm font-medium text-gray-700 mb-1">Oder direkt einfügen</label>
        <textarea value={csvText} onChange={e => setCsvText(e.target.value)} rows={6} placeholder={"valid_from,valid_until,floater_pct\n01.01.2023,31.03.2023,18.50\n01.04.2023,,19.00"} className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono" /></div>
      {csvResult && (
        <div className={`rounded p-3 mb-4 text-sm ${csvResult.errors.length > 0 ? 'bg-yellow-50 border border-yellow-200' : 'bg-green-50 border border-green-200'}`}>
          <p className="font-medium">{csvResult.inserted} neu, {csvResult.updated} aktualisiert, {csvResult.skipped} übersprungen</p>
          {csvResult.errors.map((e, i) => <p key={i} className="text-red-600 mt-1">{e}</p>)}
        </div>
      )}
      <div className="flex gap-3">
        <button onClick={importCsv} disabled={csvImporting} className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">{csvImporting ? 'Importieren…' : 'Importieren'}</button>
        <button onClick={() => setMode('list')} className="px-5 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">Zurück</button>
      </div>
    </div>
  );

  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium text-gray-700">Spediteur:</label>
          <select value={filterCarrierId} onChange={e => setFilterCarrierId(e.target.value)} className="border border-gray-300 rounded px-3 py-1.5 text-sm">
            <option value="">Alle</option>
            {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <span className="text-sm text-gray-400">{filtered.length} Einträge</span>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setMode('csv')} className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">CSV-Import</button>
          <button onClick={openAdd} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">+ Neuer Eintrag</button>
        </div>
      </div>
      <div className="bg-white rounded-lg shadow overflow-hidden border border-gray-200">
        {filtered.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <p className="text-lg mb-2">Keine manuellen Sätze vorhanden</p>
            <p className="text-sm">Manuelle Sätze überschreiben die Preisklassen-Auflösung für einen bestimmten Zeitraum.</p>
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
              {filtered.map(entry => (
                <tr key={entry.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium text-gray-900">{entry.carrierName ?? entry.carrierId}</td>
                  <td className="px-4 py-3 text-gray-700">{entry.validFrom}</td>
                  <td className="px-4 py-3 text-gray-500">{entry.validUntil ?? '—'}</td>
                  <td className="px-4 py-3 text-right font-mono font-medium text-gray-900">{parseFloat(entry.floaterPct).toFixed(2)} %</td>
                  <td className="px-4 py-3 text-gray-600">{BASIS_LABELS[entry.basis] ?? entry.basis}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{entry.source ?? '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2 justify-end">
                      <button onClick={() => openEdit(entry)} className="text-blue-600 hover:text-blue-800 text-xs font-medium">Bearbeiten</button>
                      <button onClick={() => deleteEntry(entry.id)} className="text-red-500 hover:text-red-700 text-xs font-medium">Löschen</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
};

// ---------------------------------------------------------------------------
// Destatis prices tab
// ---------------------------------------------------------------------------

const DestatisPricesTab: React.FC = () => {
  const [prices, setPrices] = useState<DestatisPriceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [months, setMonths] = useState(36);
  const [result, setResult] = useState<{ fetched: number } | null>(null);

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try { const res = await api.get<DestatisPriceEntry[]>('/api/diesel-floaters/destatis-prices'); setPrices(res.data); }
    finally { setLoading(false); }
  };

  const fetchHistory = async () => {
    setFetching(true); setResult(null);
    try {
      const res = await api.post<{ fetched: number }>(`/api/diesel-floaters/destatis-prices/fetch?months=${months}`);
      setResult(res.data); await load();
    } catch (e: any) { alert(e.response?.data?.detail || 'Abruf fehlgeschlagen'); }
    finally { setFetching(false); }
  };

  return (
    <>
      <div className="flex items-center gap-4 mb-4">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium text-gray-700">Monate abrufen:</label>
          <input type="number" min={1} max={120} value={months} onChange={e => setMonths(parseInt(e.target.value) || 36)} className="w-20 border border-gray-300 rounded px-2 py-1.5 text-sm" />
        </div>
        <button onClick={fetchHistory} disabled={fetching} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
          {fetching ? 'Abrufen…' : 'Von Destatis abrufen'}
        </button>
        {result && <span className="text-sm text-green-600 font-medium">{result.fetched} neue Preise geladen</span>}
        <span className="text-sm text-gray-400 ml-auto">{prices.length} Einträge im Cache</span>
      </div>
      <div className="bg-white rounded-lg shadow overflow-hidden border border-gray-200">
        {loading ? <div className="text-center py-8 text-gray-400">Laden…</div> : prices.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <p className="text-lg mb-2">Kein Cache vorhanden</p>
            <p className="text-sm">Klicken Sie auf "Von Destatis abrufen" um historische Preise zu laden.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Monat</th>
                <th className="text-right px-4 py-3 font-medium text-gray-600">Preis (Ct/l)</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Serie</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Abgerufen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {prices.map(p => (
                <tr key={`${p.year}-${p.month}`} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-medium text-gray-900">{MONTH_NAMES[p.month - 1]} {p.year}</td>
                  <td className="px-4 py-2 text-right font-mono font-medium text-gray-900">{parseFloat(p.priceCt).toFixed(2)}</td>
                  <td className="px-4 py-2 text-gray-400 text-xs">{p.seriesCode}</td>
                  <td className="px-4 py-2 text-gray-400 text-xs">{new Date(p.fetchedAt).toLocaleDateString('de-DE')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
};
