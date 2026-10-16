import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, MOCK } from '../api/client';
import type { TradeRow } from '../api/types';
import { Card, Chip, Drawer, Empty, ErrorBox, Loading, PageHeader, Stat, TableWrap } from '../components/ui';
import { useApp } from '../lib/app-context';
import { cls, dateTimeIST, fmt, pnlColor, signedInr, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

const EMOTIONS = ['calm', 'confident', 'fearful', 'greedy', 'impatient', 'revenge', 'bored', 'fomo'];

function downloadCsv(rows: TradeRow[]) {
  const cols: Array<keyof TradeRow> = ['id', 'strategy_id', 'underlying', 'instrument_symbol', 'side', 'qty', 'entry_ts', 'entry_price', 'stop', 'exit_ts', 'exit_price', 'outcome', 'pnl', 'costs', 'r_multiple', 'mode', 'status'];
  const csv = [cols.join(','), ...rows.map((r) => cols.map((c) => JSON.stringify(r[c] ?? '')).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = 'indexedge-trades.csv';
  a.click();
}

function TradeDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { toast } = useApp();
  const t = useApi(() => (id ? api.trade(id) : Promise.resolve(null)), [id]);
  const [text, setText] = useState('');
  const [tags, setTags] = useState('');
  const [emotion, setEmotion] = useState('calm');
  const [busy, setBusy] = useState(false);
  const d = t.data;
  const save = async () => {
    if (!id || !text.trim()) return;
    setBusy(true);
    try {
      await api.addNote(id, { text: text.trim(), tags: tags.split(',').map((x) => x.trim()).filter(Boolean), emotion });
      setText('');
      setTags('');
      toast('Note saved', 'up');
      await t.reload();
    } catch (e) {
      toast(`Save failed: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  const a = d?.analysis;
  return (
    <Drawer open={!!id} onClose={onClose} title={d ? `${d.instrument_symbol} · ${d.strategy_id}` : 'Trade'}>
      {t.loading && !d && <Loading />}
      {t.error && <ErrorBox error={t.error} onRetry={t.reload} />}
      {d && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Stat label="P&L" value={signedInr(d.pnl)} tone={pnlColor(d.pnl)} />
            <Stat label="R multiple" value={d.r_multiple != null ? `${d.r_multiple > 0 ? '+' : ''}${fmt(d.r_multiple, 2)}R` : '—'} tone={pnlColor(d.r_multiple)} />
            <Stat label="Outcome" value={titleCase(d.outcome ?? d.status)} />
            <Stat label="Entry" value={fmt(d.entry_price)} sub={dateTimeIST(d.entry_ts)} />
            <Stat label="Exit" value={fmt(d.exit_price)} sub={dateTimeIST(d.exit_ts)} />
            <Stat label="Stop / Targets" value={fmt(d.stop)} sub={d.targets.map((x) => fmt(x)).join(' / ')} />
            <Stat label="MFE / MAE" value={`${fmt(d.mfe, 2)} / ${fmt(d.mae, 2)}`} />
            <Stat label="Costs" value={signedInr(d.costs != null ? -d.costs : null)} />
            <Stat label="Qty · Mode" value={`${d.qty}`} sub={d.mode} />
          </div>
          {d.reasons && d.reasons.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Entry reasons</h3>
              <ul className="list-disc pl-5 text-sm">{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          )}
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Post-trade analysis</h3>
            {a ? (
              <dl className="space-y-1.5 text-sm">
                {a.quadrant && (
                  <div>
                    <Chip tone={/good result/.test(a.quadrant) ? 'up' : 'warn'}>{a.quadrant}</Chip>
                  </div>
                )}
                {a.summary && <p>{a.summary}</p>}
                {(['entry_quality', 'mfe_mae', 'plan_vs_actual', 'slippage'] as const).map((k) =>
                  a[k] ? (
                    <div key={k} className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2">
                      <dt className="text-muted">{titleCase(k)}</dt>
                      <dd>{String(a[k])}</dd>
                    </div>
                  ) : null,
                )}
                <div className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2">
                  <dt className="text-muted">Rule violations</dt>
                  <dd className={a.rule_violations?.length ? 'text-down' : 'text-up'}>{a.rule_violations?.length ? a.rule_violations.join('; ') : 'None'}</dd>
                </div>
              </dl>
            ) : (
              <p className="text-sm text-muted">Available after the trade closes.</p>
            )}
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Journal notes</h3>
            {(d.notes ?? []).length === 0 && <p className="text-sm text-muted">No notes yet.</p>}
            <ul className="mb-3 space-y-2">
              {(d.notes ?? []).map((n, i) => (
                <li key={i} className="rounded-lg border border-line p-2 text-sm">
                  <p>{n.text}</p>
                  <div className="mt-1 flex flex-wrap gap-1 text-xs">
                    <Chip tone="accent">{n.emotion}</Chip>
                    {n.tags.map((t) => <Chip key={t}>#{t}</Chip>)}
                    {n.ts && <span className="text-muted">{dateTimeIST(n.ts)}</span>}
                  </div>
                </li>
              ))}
            </ul>
            <div className="space-y-2 rounded-lg border border-line p-3">
              <textarea className="input min-h-[80px]" placeholder="What did you see, think and feel?" value={text} onChange={(e) => setText(e.target.value)} />
              <div className="grid grid-cols-2 gap-2">
                <input className="input" placeholder="tags, comma separated" value={tags} onChange={(e) => setTags(e.target.value)} />
                <select className="input" value={emotion} onChange={(e) => setEmotion(e.target.value)} aria-label="Emotion">
                  {EMOTIONS.map((x) => <option key={x}>{x}</option>)}
                </select>
              </div>
              <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={save}>
                {busy ? 'Saving…' : 'Add note'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Drawer>
  );
}

export default function Trades() {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [strategy, setStrategy] = useState('');
  const [status, setStatus] = useState('');
  const [params, setParams] = useSearchParams();
  const mode = params.get('mode') ?? '';
  const setMode = (m: string) => {
    const p = new URLSearchParams(params);
    if (m) p.set('mode', m);
    else p.delete('mode');
    setParams(p, { replace: true });
  };
  const [sel, setSel] = useState<string | null>(null);
  const tr = useApi(
    () => api.trades({ start: start || undefined, end: end || undefined, strategy: strategy || undefined, mode: mode || undefined }),
    [start, end, strategy, mode],
    30000,
  );
  const strategies = useApi(api.strategies);
  // client-side mode filter too, in case the backend ignores ?mode=
  const rows = useMemo(() => (tr.data?.trades ?? []).filter((t) => (!status || t.status === status) && (!mode || t.mode === mode)), [tr.data, status, mode]);
  const closed = rows.filter((t) => t.status === 'closed');
  const total = closed.reduce((a, t) => a + (t.pnl ?? 0), 0);
  const wins = closed.filter((t) => (t.pnl ?? 0) > 0).length;

  return (
    <div className="space-y-3">
      <PageHeader
        title="Trades & Journal"
        right={
          MOCK ? (
            <button className="btn" onClick={() => downloadCsv(rows)}>⬇ CSV</button>
          ) : (
            <>
              <a className="btn" href={api.exportUrl('csv')}>⬇ CSV</a>
              <a className="btn" href={api.exportUrl('xlsx')}>⬇ Excel</a>
            </>
          )
        }
      />
      <Card>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <label>
            <span className="label">From</span>
            <input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>
            <span className="label">To</span>
            <input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <label>
            <span className="label">Strategy</span>
            <select className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">All</option>
              {strategies.data?.strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label>
            <span className="label">Status</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option value="open">Open</option>
              <option value="closed">Closed</option>
            </select>
          </label>
          <label>
            <span className="label">Mode</span>
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="">All</option>
              <option value="manual">Manual (Angel One by hand)</option>
              <option value="paper">Paper</option>
              <option value="live">Live (auto)</option>
            </select>
          </label>
        </div>
      </Card>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Trades" value={rows.length} />
        <Stat label="Win rate" value={closed.length ? `${((wins / closed.length) * 100).toFixed(0)}%` : '—'} />
        <Stat label="Net P&L" value={signedInr(total)} tone={pnlColor(total)} />
      </div>
      <Card>
        {tr.error && <ErrorBox error={tr.error} onRetry={tr.reload} />}
        {!tr.data && tr.loading && <Loading />}
        {tr.data && rows.length === 0 && <Empty>No trades for this filter.</Empty>}
        {rows.length > 0 && (
          <TableWrap>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Entry</th>
                  <th>Strategy</th>
                  <th>Instrument</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">R</th>
                  <th className="num">P&amp;L</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className="cursor-pointer hover:bg-panel2" onClick={() => setSel(t.id)}>
                    <td className="text-xs">{dateTimeIST(t.entry_ts)}</td>
                    <td>
                      {t.strategy_id}
                      {t.mode === 'manual' && <Chip tone="accent" className="ml-1">manual</Chip>}
                    </td>
                    <td className="text-xs">
                      <span className={cls('mr-1 font-bold', t.side === 'BUY' ? 'text-up' : 'text-down')}>{t.side}</span>
                      {t.instrument_symbol}
                    </td>
                    <td className="num">{t.qty}</td>
                    <td className="num">{fmt(t.entry_price)}</td>
                    <td className="num">{t.status === 'open' ? <span className="text-accent">{fmt(t.ltp)}</span> : fmt(t.exit_price)}</td>
                    <td className={cls('num', pnlColor(t.r_multiple))}>{t.r_multiple != null ? fmt(t.r_multiple, 2) : '—'}</td>
                    <td className={cls('num font-semibold', pnlColor(t.pnl))}>{signedInr(t.pnl)}</td>
                    <td>{t.status === 'open' ? (t.alert ? <Chip tone={/stop/i.test(t.alert) ? 'down' : 'up'}>{/stop/i.test(t.alert) ? 'STOP HIT' : 'TARGET'}</Chip> : <Chip tone="accent">open</Chip>) : <Chip tone={(t.pnl ?? 0) > 0 ? 'up' : 'down'}>{t.outcome ?? 'closed'}</Chip>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <TradeDrawer id={sel} onClose={() => setSel(null)} />
    </div>
  );
}
