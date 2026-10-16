import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type { BacktestResponse, ExecutionMode, ParamValue, ReliabilityBin, StrategyInfo, StrategyMetrics } from '../api/types';
import { SeriesChart } from '../components/charts';
import { Card, Chip, Empty, ErrorBox, Loading, Modal, PageHeader, Segmented, Stat, StrategyBadge, TableWrap, Toggle } from '../components/ui';
import { useApp } from '../lib/app-context';
import { cls, fmt, pnlColor, prob, signedInr, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';
import { UNDERLYING_OPTIONS } from '../lib/underlyings';

function MetricsGrid({ m }: { m: StrategyMetrics }) {
  return (
    <div className="grid grid-cols-3 gap-1.5 text-xs">
      <Stat label="Trades" value={m.n ?? '—'} />
      <Stat label="Win rate" value={m.win_rate != null ? prob(m.win_rate) : '—'} sub={m.win_ci ? `CI ${prob(m.win_ci[0])}–${prob(m.win_ci[1])}` : undefined} />
      <Stat label="Expectancy" value={m.expectancy_r != null ? `${m.expectancy_r > 0 ? '+' : ''}${fmt(m.expectancy_r, 2)}R` : '—'} tone={pnlColor(m.expectancy_r)} sub={m.expectancy_rs != null ? signedInr(m.expectancy_rs) : undefined} />
      <Stat label="Profit factor" value={fmt(m.profit_factor, 2)} />
      <Stat label="Max DD" value={m.max_dd_r != null ? `${fmt(m.max_dd_r, 1)}R` : '—'} />
      <Stat label="Sharpe" value={fmt(m.sharpe, 2)} />
    </div>
  );
}

/** Calibration plot: predicted vs observed probability, bubble size ∝ n. */
export function ReliabilityDiagram({ bins, size = 220 }: { bins: ReliabilityBin[]; size?: number }) {
  const pad = 28;
  const s = size - pad - 8;
  const x = (v: number) => pad + v * s;
  const y = (v: number) => 8 + (1 - v) * s;
  const maxN = Math.max(1, ...bins.map((b) => b.n));
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[260px]" role="img" aria-label="Reliability diagram">
      <rect x={pad} y={8} width={s} height={s} fill="none" stroke="rgb(var(--line))" />
      {[0.25, 0.5, 0.75].map((g) => (
        <g key={g}>
          <line x1={x(g)} x2={x(g)} y1={8} y2={8 + s} stroke="rgb(var(--line))" strokeDasharray="2 3" />
          <line x1={pad} x2={pad + s} y1={y(g)} y2={y(g)} stroke="rgb(var(--line))" strokeDasharray="2 3" />
        </g>
      ))}
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="rgb(var(--muted))" strokeDasharray="4 3" />
      <polyline
        fill="none"
        stroke="rgb(var(--accent))"
        strokeWidth={2}
        points={bins
          .slice()
          .sort((a, b) => a.p_pred - b.p_pred)
          .map((b) => `${x(b.p_pred)},${y(b.p_obs)}`)
          .join(' ')}
      />
      {bins.map((b, i) => (
        <circle key={i} cx={x(b.p_pred)} cy={y(b.p_obs)} r={3 + 6 * Math.sqrt(b.n / maxN)} fill="rgb(var(--accent))" fillOpacity={0.5}>
          <title>{`pred ${prob(b.p_pred)} · obs ${prob(b.p_obs)} · n=${b.n}`}</title>
        </circle>
      ))}
      {[0, 0.5, 1].map((v) => (
        <g key={v} fontSize={9} fill="rgb(var(--muted))">
          <text x={x(v)} y={size - 4} textAnchor="middle">{v}</text>
          <text x={pad - 4} y={y(v) + 3} textAnchor="end">{v}</text>
        </g>
      ))}
    </svg>
  );
}

function BacktestModal({ s, onClose }: { s: StrategyInfo; onClose: () => void }) {
  const [u, setU] = useState('NIFTY');
  const [days, setDays] = useState(250);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<BacktestResponse | null>(null);
  const model = useApi(() => api.model(s.id), [s.id]);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.backtest({ strategy_id: s.id, underlying: u, days }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const eq = useMemo(() => (res ? [{ type: 'area' as const, data: res.equity, color: '#38bdf8', title: 'Equity (₹)' }] : []), [res]);
  const folds = (res?.walk_forward.folds ?? []) as Array<Record<string, unknown>>;
  const foldCols = folds.length ? Object.keys(folds[0]) : [];
  return (
    <Modal open onClose={onClose} title={`Backtest · ${s.name}`} wide>
      <div className="flex flex-wrap items-end gap-2">
        <Segmented value={u} onChange={setU} options={UNDERLYING_OPTIONS} />
        <label className="w-28">
          <span className="label">Days</span>
          <input type="number" min={20} max={2000} className="input" value={days} onChange={(e) => setDays(Number(e.target.value))} />
        </label>
        <button className="btn btn-primary" onClick={run} disabled={busy}>
          {busy ? 'Running…' : '▶ Run backtest'}
        </button>
      </div>
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
      {busy && <Loading text="Running backtest (costs included)…" />}
      {res && (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap gap-1.5">
            {!res.report.badges.includes(res.report.status) && (
              <Chip tone={res.report.status === 'validated' ? 'up' : res.report.status === 'rejected' ? 'down' : 'warn'}>{titleCase(res.report.status)}</Chip>
            )}
            {res.report.badges.map((b) => <StrategyBadge key={b} badge={b} />)}
          </div>
          {res.report.warnings.length > 0 && <ul className="text-sm text-warn">{res.report.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}</ul>}
          <MetricsGrid m={res.metrics} />
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Equity (₹, after costs)</h3>
            <SeriesChart series={eq} height={200} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Monte Carlo (trade-order shuffles)</h3>
              <dl className="grid grid-cols-2 gap-1 text-sm">
                {Object.entries(res.monte_carlo).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted">{titleCase(k)}</dt>
                    <dd className="num">{typeof v === 'number' ? fmt(v, k.includes('prob') ? 3 : 1) : String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Reliability (model calibration)</h3>
              {model.data && model.data.reliability.length > 0 ? (
                <>
                  <ReliabilityDiagram bins={model.data.reliability} />
                  {model.data.metrics && (
                    <p className="text-xs text-muted">
                      AUC {fmt(model.data.metrics.auc as number, 3)} · Brier {fmt(model.data.metrics.brier as number, 3)} · n {String(model.data.metrics.n ?? '—')}
                    </p>
                  )}
                </>
              ) : (
                <p className="text-sm text-muted">{model.loading ? 'Loading…' : 'No trained model yet (insufficient data).'}</p>
              )}
            </div>
          </div>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase text-muted">
              Walk-forward {typeof res.walk_forward.stability === 'number' && <span className="normal-case">· stability {fmt(res.walk_forward.stability, 2)}</span>}
            </h3>
            {(res.walk_forward.warnings ?? []).map((w, i) => <p key={i} className="text-sm text-warn">⚠ {w}</p>)}
            {folds.length ? (
              <TableWrap>
                <table className="tbl">
                  <thead>
                    <tr>{foldCols.map((c) => <th key={c} className="num">{titleCase(c)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {folds.map((f, i) => (
                      <tr key={i}>
                        {foldCols.map((c) => (
                          <td key={c} className="num">{typeof f[c] === 'number' ? fmt(f[c] as number, Number.isInteger(f[c]) ? 0 : 2) : String(f[c] ?? '—')}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            ) : (
              <Empty>No walk-forward folds.</Empty>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function ParamInput({ name, value, onChange }: { name: string; value: ParamValue; onChange: (v: ParamValue) => void }) {
  if (typeof value === 'boolean') {
    return (
      <label className="flex items-center justify-between gap-2 text-sm">
        <span className="font-mono text-xs">{name}</span>
        <Toggle checked={value} onChange={onChange} label={name} />
      </label>
    );
  }
  return (
    <label>
      <span className="label font-mono">{name}</span>
      <input
        className="input"
        type={typeof value === 'number' ? 'number' : 'text'}
        step="any"
        value={String(value)}
        onChange={(e) => onChange(typeof value === 'number' ? Number(e.target.value) : e.target.value)}
      />
    </label>
  );
}

function StrategyCard({ s, onSaved, onBacktest }: { s: StrategyInfo; onSaved: () => void; onBacktest: () => void }) {
  const { toast } = useApp();
  const [params, setParams] = useState(s.params);
  const [showParams, setShowParams] = useState(false);
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(params) !== JSON.stringify(s.params);
  const update = async (u: Parameters<typeof api.updateStrategy>[1], msg: string) => {
    setBusy(true);
    try {
      await api.updateStrategy(s.id, u);
      toast(msg, 'up');
      onSaved();
    } catch (e) {
      toast(`Update failed: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  const r = s.report;
  const hwr = r?.badges.includes('high_win_rate');
  const exp = r?.metrics.expectancy_r;
  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-semibold">{s.name}</h3>
          <p className="text-xs text-muted">
            <span className="font-mono">{s.id}</span> · {s.timeframe} · {s.regimes ? s.regimes.map(titleCase).join(', ') : 'All regimes'}
          </p>
        </div>
        <Toggle checked={s.enabled} disabled={busy} onChange={(v) => update({ enabled: v }, `${s.name} ${v ? 'enabled' : 'disabled'}`)} label="Enabled" />
      </div>
      <p className="mt-1 text-sm">{s.description}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {r ? r.badges.filter((b) => b !== 'high_win_rate').map((b) => <StrategyBadge key={b} badge={b} />) : <StrategyBadge badge="insufficient_data" />}
      </div>
      {hwr && (
        <div className={cls('mt-2 inline-flex flex-wrap gap-x-3 rounded-lg border px-2 py-1 text-xs', (exp ?? 0) <= 0 ? 'border-warn/60 bg-warn/10' : 'border-line bg-panel2/60')}>
          <span className="font-semibold text-accent">🏅 High win-rate {r?.metrics.win_rate != null && prob(r.metrics.win_rate)}</span>
          <span>Avg R <b className="font-mono">{fmt(r?.metrics.avg_r, 2)}</b></span>
          <span>Exp <b className={cls('font-mono', (exp ?? 0) <= 0 ? 'text-warn' : 'text-up')}>{fmt(exp, 2)}R</b></span>
          {(exp ?? 0) <= 0 && <span className="font-semibold text-warn">⚠ Expectancy ≤ 0</span>}
        </div>
      )}
      {r && (
        <div className="mt-2">
          <MetricsGrid m={r.metrics} />
          {r.warnings.length > 0 && <ul className="mt-1 text-xs text-warn">{r.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}</ul>}
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select
          className="input w-auto"
          value={s.execution_mode}
          disabled={busy}
          aria-label="Execution mode"
          onChange={(e) => update({ execution_mode: e.target.value as ExecutionMode }, `${s.name}: ${titleCase(e.target.value)}`)}
        >
          <option value="signal_only">Signal only</option>
          <option value="confirm">One-tap confirm</option>
          <option value="auto">Auto</option>
        </select>
        <button className="btn" onClick={() => setShowParams((x) => !x)}>
          Params {showParams ? '▴' : '▾'}
        </button>
        <button className="btn btn-primary" onClick={onBacktest}>
          ▶ Run backtest
        </button>
      </div>
      {showParams && (
        <div className="mt-3 space-y-2 rounded-lg border border-line p-3">
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(params).map(([k, v]) => (
              <ParamInput key={k} name={k} value={v} onChange={(nv) => setParams((p) => ({ ...p, [k]: nv }))} />
            ))}
          </div>
          <div className="flex gap-2">
            <button className="btn btn-primary" disabled={!dirty || busy} onClick={() => update({ params }, 'Parameters saved — re-run the backtest to re-validate')}>
              Save params
            </button>
            <button className="btn" disabled={!dirty} onClick={() => setParams(s.params)}>
              Reset
            </button>
          </div>
          <p className="text-xs text-muted">Changing parameters invalidates the report card until the strategy is re-validated (beware of overfitting).</p>
        </div>
      )}
    </Card>
  );
}

export default function StrategyLab() {
  const st = useApi(api.strategies, [], 15000);
  const [bt, setBt] = useState<StrategyInfo | null>(null);
  const [msg, setMsg] = useState('');
  const meta = st.data?.reports_meta;
  const job = st.data?.reports_job;
  const running = job?.status === 'running';
  const run = async (fn: () => Promise<{ ok: boolean; message?: string }>) => {
    try {
      const r = await fn();
      setMsg(r.message ?? (r.ok ? 'Started' : 'Not started'));
      st.reload();
    } catch (e) {
      setMsg(String(e));
    }
  };
  return (
    <div className="space-y-3">
      <PageHeader
        title="Strategy Lab"
        subtitle={
          <>
            Report cards come from out-of-sample backtests after costs. Validated = PF ≥ 1.3, ≥ 100 trades, max DD ≤ 15R.
            {meta?.ts && (
              <span className="block">
                Last validated {new Date(meta.ts).toLocaleString()} on {meta.days} days of{' '}
                <b className={meta.data_source === 'synthetic' ? 'text-warn' : ''}>{meta.data_source === 'synthetic' ? 'SYNTHETIC demo' : 'real broker'}</b> data.
              </span>
            )}
          </>
        }
        right={
          <>
            <button className="btn" disabled={running} onClick={() => run(api.revalidate)}>
              {running ? 'Validating…' : 'Revalidate'}
            </button>
            <button className="btn" onClick={() => run(api.retrain)}>Retrain models</button>
            {msg && <span className="text-xs text-muted">{msg}</span>}
          </>
        }
      />
      {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
      {!st.data && st.loading && <Loading />}
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {st.data?.strategies.map((s) => (
          <StrategyCard key={s.id + JSON.stringify(s.params)} s={s} onSaved={st.reload} onBacktest={() => setBt(s)} />
        ))}
      </div>
      {bt && <BacktestModal s={bt} onClose={() => setBt(null)} />}
    </div>
  );
}
