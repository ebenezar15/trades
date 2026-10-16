// Signal display: probability gauge (with CI + sample size), high-win-rate honesty block, and the "Why" panel.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { SignalJSON } from '../api/types';
import { useApp } from '../lib/app-context';
import { cls, fmt, inr, prob, signedInr, timeIST, titleCase } from '../lib/format';
import { Chip, StrategyBadge } from './ui';

export function isPendingConfirm(s: SignalJSON): boolean {
  return s.status === 'pending_confirm' || s.status === 'pending' || (s.status === 'active' && s.execution_mode === 'confirm');
}

/** Semicircle gauge for probability 0..1 with CI arc; shows "Insufficient data" when null. */
export function ProbabilityGauge({ p, ci, n, size = 112 }: { p: number | null; ci: [number, number] | null; n: number; size?: number }) {
  const w = size;
  const h = size * 0.62;
  const r = size * 0.42;
  const cx = w / 2;
  const cy = h - 6;
  const pt = (v: number, rr = r) => {
    const a = Math.PI * (1 - Math.max(0, Math.min(1, v)));
    return [cx + rr * Math.cos(a), cy - rr * Math.sin(a)];
  };
  const arc = (a: number, b: number, rr = r) => {
    const [x1, y1] = pt(a, rr);
    const [x2, y2] = pt(b, rr);
    return `M ${x1} ${y1} A ${rr} ${rr} 0 0 1 ${x2} ${y2}`;
  };
  if (p === null || p === undefined) {
    return (
      <div className="flex flex-col items-center justify-center text-center" style={{ width: w }}>
        <svg width={w} height={h} aria-hidden>
          <path d={arc(0, 1)} stroke="rgb(var(--line))" strokeWidth={8} fill="none" strokeLinecap="round" />
          <text x={cx} y={cy - 6} textAnchor="middle" className="fill-muted" fontSize={size * 0.2}>?</text>
        </svg>
        <div className="text-[11px] font-semibold leading-tight text-warn">Insufficient data</div>
        <div className="text-[10px] text-muted">n = {n} (&lt; 50)</div>
      </div>
    );
  }
  const color = p >= 0.6 ? 'rgb(var(--up))' : p >= 0.45 ? 'rgb(var(--warn))' : 'rgb(var(--down))';
  const [nx, ny] = pt(p, r - 2);
  return (
    <div className="flex flex-col items-center text-center" style={{ width: w }} title="Calibrated probability of target before stop">
      <svg width={w} height={h} role="img" aria-label={`Probability ${prob(p)}`}>
        <path d={arc(0, 1)} stroke="rgb(var(--line))" strokeWidth={8} fill="none" strokeLinecap="round" />
        {ci && <path d={arc(ci[0], ci[1])} stroke={color} strokeOpacity={0.35} strokeWidth={14} fill="none" />}
        <path d={arc(0, p)} stroke={color} strokeWidth={8} fill="none" strokeLinecap="round" />
        <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="rgb(var(--fg))" strokeWidth={2} />
        <circle cx={cx} cy={cy} r={3} fill="rgb(var(--fg))" />
        <text x={cx} y={cy - r * 0.38} textAnchor="middle" fontSize={size * 0.17} fontWeight={700} fill="rgb(var(--fg))">
          {prob(p)}
        </text>
      </svg>
      <div className="font-mono text-[10px] leading-tight text-muted">
        {ci ? `95% CI ${prob(ci[0])}–${prob(ci[1])}` : 'CI n/a'}
        <br />n = {n}
      </div>
    </div>
  );
}

/** R:R + expectancy, always shown together with the 🏅 badge when present (spec D2). */
export function RrExpectancy({ s }: { s: SignalJSON }) {
  const hwr = s.badges.includes('high_win_rate');
  const expR = s.expectancy_r;
  const negative = (expR !== null && expR !== undefined ? expR <= 0 : s.ev_per_trade <= 0) || s.badges.includes('negative_expectancy_warning');
  return (
    <div
      className={cls(
        'inline-flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-2 py-1 text-xs',
        hwr && negative ? 'border-warn/60 bg-warn/10' : 'border-line bg-panel2/60',
      )}
    >
      {hwr && <span className="font-semibold text-accent">🏅 High win-rate{s.win_rate ? ` ${prob(s.win_rate)}` : ''}</span>}
      <span>
        R:R <b className="font-mono">{fmt(s.expected_rr, 2)}</b>
      </span>
      <span>
        Exp{' '}
        <b className={cls('font-mono', negative ? 'text-warn' : 'text-up')}>
          {expR !== null && expR !== undefined ? `${expR > 0 ? '+' : ''}${fmt(expR, 2)}R` : ''}
          {expR !== null && expR !== undefined ? ' · ' : ''}
          {signedInr(s.ev_per_trade)}
        </b>
      </span>
      {hwr && negative && <span className="font-semibold text-warn">⚠ Expectancy ≤ 0 — high win rate hides larger losses</span>}
    </div>
  );
}

const statusTone = (st: string) =>
  st === 'blocked' ? 'down' : st === 'taken' ? 'up' : isPendingLike(st) ? 'warn' : st === 'active' ? 'accent' : 'neutral';
const isPendingLike = (st: string) => st === 'pending_confirm' || st === 'pending';

function Countdown({ until }: { until: string }) {
  const [left, setLeft] = useState(() => Math.max(0, Math.round((new Date(until).getTime() - Date.now()) / 1000)));
  useEffect(() => {
    const id = setInterval(() => setLeft(Math.max(0, Math.round((new Date(until).getTime() - Date.now()) / 1000))), 1000);
    return () => clearInterval(id);
  }, [until]);
  return <span className="font-mono">{left}s</span>;
}

export function ConfirmButtons({ s, onDone }: { s: SignalJSON; onDone?: () => void }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState<'' | 'confirm' | 'skip'>('');
  const act = async (kind: 'confirm' | 'skip') => {
    setBusy(kind);
    try {
      await (kind === 'confirm' ? api.confirmSignal(s.id) : api.skipSignal(s.id));
      toast(kind === 'confirm' ? `Confirmed ${s.strategy_name}` : `Skipped ${s.strategy_name}`, kind === 'confirm' ? 'up' : 'info');
      onDone?.();
    } catch (e) {
      toast(`Failed: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy('');
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button className="btn btn-primary" disabled={!!busy} onClick={() => act('confirm')}>
        {busy === 'confirm' ? '…' : '✓ Confirm'}
      </button>
      <button className="btn" disabled={!!busy} onClick={() => act('skip')}>
        {busy === 'skip' ? '…' : 'Skip'}
      </button>
      {s.confirm_expires_at && (
        <span className="text-xs text-muted">
          expires in <Countdown until={s.confirm_expires_at} />
        </span>
      )}
    </div>
  );
}

export function WhyPanel({ s }: { s: SignalJSON }) {
  const maxAbs = Math.max(0.0001, ...s.top_features.map(([, v]) => Math.abs(v)));
  const List = ({ title, items, empty }: { title: string; items: string[]; empty: string }) => (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h4>
      {items.length ? (
        <ul className="list-disc space-y-0.5 pl-4 text-sm">
          {items.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">{empty}</p>
      )}
    </div>
  );
  return (
    <div className="mt-3 grid gap-4 border-t border-line pt-3 md:grid-cols-2">
      <List title="Setup conditions" items={s.reasons} empty="—" />
      <List title="Market context" items={s.context_reasons} empty="No context notes" />
      <List title="News" items={s.news_reasons} empty="No relevant news" />
      <div>
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Top model features</h4>
        {s.top_features.length ? (
          <div className="space-y-1">
            {s.top_features.map(([name, v]) => (
              <div key={name} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_3.5rem] items-center gap-2 text-xs">
                <span className="truncate font-mono">{name}</span>
                <div className="relative h-2 rounded bg-panel2">
                  <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                  <div
                    className={cls('absolute inset-y-0 rounded', v >= 0 ? 'left-1/2 bg-up' : 'right-1/2 bg-down')}
                    style={{ width: `${(Math.abs(v) / maxAbs) * 50}%` }}
                  />
                </div>
                <span className="num">{v > 0 ? '+' : ''}{fmt(v, 2)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No model yet — probability from historical win rate.</p>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 md:col-span-2">
        {s.badges.map((b) => (
          <StrategyBadge key={b} badge={b} />
        ))}
        <Chip>Regime: {titleCase(s.regime)}</Chip>
        {s.instrument && <Chip>{s.instrument.symbol}</Chip>}
      </div>
    </div>
  );
}

export function SignalCard({ s, compact, onChanged }: { s: SignalJSON; compact?: boolean; onChanged?: () => void }) {
  const [open, setOpen] = useState(false);
  const buy = s.side === 'BUY';
  return (
    <article className={cls('rounded-xl border bg-panel p-3', s.status === 'blocked' ? 'border-down/40' : isPendingConfirm(s) ? 'border-warn/60' : 'border-line')}>
      <div className="flex gap-3">
        <ProbabilityGauge p={s.probability} ci={s.probability_ci} n={s.sample_size} size={compact ? 96 : 112} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cls('rounded px-1.5 py-0.5 text-xs font-bold', buy ? 'bg-up/15 text-up' : 'bg-down/15 text-down')}>
              {buy ? '▲ LONG' : '▼ SHORT'}
            </span>
            <span className="font-semibold">{s.underlying}</span>
            <Chip tone={statusTone(s.status) as 'up'}>{titleCase(s.status)}</Chip>
            <span className="ml-auto text-xs text-muted">{timeIST(s.ts)}</span>
          </div>
          <div className="mt-0.5 truncate text-sm text-muted">{s.strategy_name}</div>
          <div className="mt-1 grid grid-cols-3 gap-1 font-mono text-xs sm:text-sm">
            <div>
              <div className="text-[10px] uppercase text-muted">Entry</div>
              {fmt(s.entry)}
            </div>
            <div>
              <div className="text-[10px] uppercase text-muted">Stop</div>
              <span className="text-down">{fmt(s.stop)}</span>
            </div>
            <div className="min-w-0">
              <div className="text-[10px] uppercase text-muted">Targets</div>
              <span className="block truncate text-up">{s.targets.map((x) => fmt(x)).join(' / ')}</span>
            </div>
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <RrExpectancy s={s} />
        {!compact &&
          s.badges
            .filter((b) => b !== 'high_win_rate' && b !== 'negative_expectancy_warning')
            .map((b) => <StrategyBadge key={b} badge={b} />)}
        <span className="text-xs text-muted">
          Qty <b className="font-mono text-fg">{s.quantity}</b>
          {s.instrument?.symbol ? ` · ${s.instrument.symbol}` : ''}
        </span>
      </div>
      {s.status === 'blocked' && s.blocked_reason && <div className="mt-2 text-sm text-down">⛔ {s.blocked_reason}</div>}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        {isPendingConfirm(s) ? <ConfirmButtons s={s} onDone={onChanged} /> : <span />}
        <button className="btn btn-ghost text-accent" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'Hide why ▴' : 'Why? ▾'}
        </button>
      </div>
      {open && <WhyPanel s={s} />}
      {open && s.ev_per_trade !== 0 && <p className="mt-2 text-xs text-muted">EV after costs for suggested qty: {inr(s.ev_per_trade)}</p>}
    </article>
  );
}
