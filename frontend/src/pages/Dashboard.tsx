import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Candle, IndexOverview, Position, TradeRow } from '../api/types';
import { useWsMessages } from '../api/ws';
import { CandleChart } from '../components/charts';
import { KillSwitch } from '../components/KillSwitch';
import { SignalCard, isPendingConfirm } from '../components/signals';
import { BiasMeter, Card, Chip, Empty, ErrorBox, Loading, Meter } from '../components/ui';
import { useApp } from '../lib/app-context';
import { cls, fmt, inr, pct, pnlColor, signedInr, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

const REGIME_TONE: Record<string, 'up' | 'down' | 'warn' | 'neutral'> = { trend_up: 'up', trend_down: 'down', high_vol: 'warn', range: 'neutral' };

function IndexTab({ ix, active, onClick, live }: { ix: IndexOverview; active: boolean; onClick: () => void; live?: number }) {
  const ltp = live ?? ix.ltp;
  return (
    <button
      onClick={onClick}
      className={cls('min-w-0 flex-1 rounded-xl border p-2.5 text-left transition sm:p-3', active ? 'border-accent bg-accent/10' : 'border-line bg-panel hover:bg-panel2')}
    >
      <div className="flex items-center justify-between gap-1 text-xs font-semibold text-muted">
        <span>{ix.underlying}</span>
        <span className={pnlColor(ix.change_pct)}>{pct(ix.change_pct, 2, true)}</span>
      </div>
      <div className="truncate font-mono text-lg font-bold sm:text-xl">{fmt(ltp)}</div>
    </button>
  );
}

function Positions({ positions, trades }: { positions: Position[]; trades: TradeRow[] }) {
  const total = positions.reduce((a, p) => a + p.pnl, 0);
  return (
    <Card title="Open positions" right={<span className={cls('font-mono text-sm font-bold', pnlColor(total))}>{signedInr(total)}</span>}>
      {positions.length === 0 ? (
        <Empty>No open positions</Empty>
      ) : (
        <ul className="divide-y divide-line">
          {positions.map((p) => {
            const tr = trades.find((t) => t.instrument_symbol === p.symbol);
            return (
              <li key={p.symbol} className="flex items-start justify-between gap-2 py-2">
                <div className="min-w-0">
                  <div className="truncate font-medium">{p.symbol}</div>
                  <div className="font-mono text-xs text-muted">
                    <span className={cls(p.qty < 0 && 'text-down')}>{p.qty}</span> @ {fmt(p.avg_price)} → <span className="text-fg">{fmt(p.ltp)}</span>
                  </div>
                  {tr && (
                    <div className="truncate text-[11px] text-muted">
                      {tr.strategy_id} · SL {fmt(tr.stop)} · T {tr.targets.map((x) => fmt(x)).join('/')}
                    </div>
                  )}
                </div>
                <span className={cls('shrink-0 font-mono font-semibold', pnlColor(p.pnl))}>{signedInr(p.pnl)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

export default function Dashboard() {
  const { status } = useApp();
  const [u, setU] = useState('NIFTY');
  const [show, setShow] = useState({ ema: true, vwap: true, supertrend: true, levels: true });
  const overview = useApi(api.overview, [], 15000);
  const candles = useApi(() => api.candles(u, '5m', 500), [u], 60000);
  const ctx = useApi(api.context, [], 60000);
  const signals = useApi(() => api.signals(undefined, 30), [], 10000);
  const positions = useApi(api.positions, [], 3000);
  const [live, setLive] = useState<Record<string, Candle>>({});
  const [ltps, setLtps] = useState<Record<string, number>>({});

  useWsMessages(
    (m) => {
      if (m.type === 'tick' && m.data.underlying) setLtps((x) => ({ ...x, [m.data.underlying as string]: m.data.ltp }));
      if (m.type === 'candle') {
        const c = m.data;
        setLive((x) => ({ ...x, [c.underlying]: c }));
        if (c.underlying === u && (!c.tf || c.tf === '5m')) {
          candles.setData((d) => {
            if (!d || !d.candles.length) return d;
            const last = d.candles[d.candles.length - 1];
            if (c.time > last.time) return { ...d, candles: [...d.candles, { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume }] };
            return d;
          });
        }
      }
      if (m.type === 'signal') void signals.reload();
      if (m.type === 'context') ctx.setData(m.data);
      if (m.type === 'trade' || m.type === 'order') void positions.reload();
    },
    ['tick', 'candle', 'signal', 'context', 'trade', 'order'],
  );

  const ix = overview.data?.indices.find((i) => i.underlying === u);
  const activeSignals = useMemo(
    () => (signals.data?.signals ?? []).filter((s) => s.status === 'active' || s.status === 'blocked' || isPendingConfirm(s)).slice(0, 6),
    [signals.data],
  );
  const risk = status?.risk;

  return (
    <div className="space-y-3">
      {/* index selector */}
      <div className="flex gap-2">
        {(overview.data?.indices ?? []).map((i) => (
          <IndexTab key={i.underlying} ix={i} active={i.underlying === u} onClick={() => setU(i.underlying)} live={ltps[i.underlying]} />
        ))}
        {overview.loading && !overview.data && <Loading />}
      </div>
      {overview.error && !overview.data && <ErrorBox error={overview.error} onRetry={overview.reload} />}

      {/* chips */}
      {ix && (
        <div className="flex flex-wrap gap-1.5">
          <Chip tone={REGIME_TONE[ix.regime] ?? 'neutral'}>Regime: {titleCase(ix.regime)}</Chip>
          {ix.vix != null && <Chip tone={ix.vix >= 22 ? 'down' : ix.vix >= 17 ? 'warn' : 'neutral'}>VIX {fmt(ix.vix, 2)}</Chip>}
          {ix.pcr != null && <Chip tone={ix.pcr > 1.2 ? 'up' : ix.pcr < 0.8 ? 'down' : 'neutral'}>PCR {fmt(ix.pcr, 2)}</Chip>}
          {ix.max_pain != null && <Chip>Max pain {fmt(ix.max_pain, 0)}</Chip>}
          {ctx.data?.risk_off && <Chip tone="down">RISK-OFF</Chip>}
          {ctx.data && !ctx.data.usable && <Chip tone="warn">Context stale — signals paused</Chip>}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title={`${u} · 5m`}
          right={
            <div className="flex flex-wrap gap-1">
              {(['ema', 'vwap', 'supertrend', 'levels'] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setShow((s) => ({ ...s, [k]: !s[k] }))}
                  className={cls('rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase', show[k] ? 'bg-accent/15 text-accent' : 'text-muted')}
                >
                  {k === 'supertrend' ? 'ST' : k === 'ema' ? 'EMA' : k}
                </button>
              ))}
            </div>
          }
        >
          {candles.error && !candles.data && <ErrorBox error={candles.error} onRetry={candles.reload} />}
          <CandleChart
            candles={candles.data?.candles ?? []}
            overlays={candles.data?.overlays}
            levels={candles.data?.levels}
            show={show}
            live={live[u]}
            fitKey={u}
            height={typeof window !== 'undefined' && window.innerWidth < 640 ? 300 : 400}
          />
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
            <span><span className="text-[#f59e0b]">━</span> EMA9</span>
            <span><span className="text-[#a855f7]">━</span> EMA21</span>
            <span><span className="text-[#38bdf8]">┅</span> VWAP</span>
            <span><span className="text-up">━</span>/<span className="text-down">━</span> Supertrend</span>
            <span>Levels: P/BC/TC · R1/S1 · PDH/PDL · ORH/ORL</span>
          </div>
        </Card>

        <div className="space-y-3">
          <KillSwitch />
          <Card title="Risk">
            {risk ? (
              <div className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm text-muted">Day P&amp;L</span>
                  <span className={cls('font-mono text-xl font-bold', pnlColor(risk.daily_pnl))}>{signedInr(risk.daily_pnl)}</span>
                </div>
                <Meter label="Daily loss limit used" value={risk.daily_loss_used_pct} />
                <Meter label="Trades used" value={risk.trades_today} max={Math.max(1, risk.max_trades)} display={`${risk.trades_today} / ${risk.max_trades}`} />
                {risk.locked && <div className="rounded-lg bg-down/10 p-2 text-sm text-down">🔒 {risk.lock_reason || 'Trading locked for today'}</div>}
              </div>
            ) : (
              <Loading />
            )}
          </Card>
          <Card title="Market context" right={<Link to="/context" className="text-xs text-accent">Details →</Link>}>
            {ctx.data ? (
              <div className="space-y-2">
                <BiasMeter value={ctx.data.macro_bias} />
                <Meter label="Event risk" value={ctx.data.event_risk * 100} danger={70} />
                <ul className="space-y-0.5 text-xs text-muted">
                  {ctx.data.reasons.slice(0, 3).map((r, i) => (
                    <li key={i}>• {r}</li>
                  ))}
                </ul>
              </div>
            ) : ctx.error ? (
              <ErrorBox error={ctx.error} onRetry={ctx.reload} />
            ) : (
              <Loading />
            )}
          </Card>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="space-y-2 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">Active signals</h2>
            <Link to="/signals" className="text-xs text-accent">All signals →</Link>
          </div>
          {signals.error && !signals.data && <ErrorBox error={signals.error} onRetry={signals.reload} />}
          {signals.data && activeSignals.length === 0 && (
            <div className="card">
              <Empty>No active signals. Waiting for a setup that passes the context, news and regime gates.</Empty>
            </div>
          )}
          <div className="grid gap-2 xl:grid-cols-2">
            {activeSignals.map((s) => (
              <SignalCard key={s.id} s={s} compact onChanged={signals.reload} />
            ))}
          </div>
        </div>
        <div>
          {positions.data ? (
            <Positions positions={positions.data.positions} trades={positions.data.open_trades} />
          ) : positions.error ? (
            <ErrorBox error={positions.error} onRetry={positions.reload} />
          ) : (
            <Loading />
          )}
          {positions.data && positions.data.positions.length > 0 && (
            <p className="mt-1 text-right text-xs text-muted">
              Deployed {inr(positions.data.positions.reduce((a, p) => a + Math.abs(p.qty * p.avg_price), 0))}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
