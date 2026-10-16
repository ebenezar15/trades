// Manual trades panel (Iteration 3): trades the user placed by hand on Angel One, with live P&L, the stop/target
// plan, a prominent alert banner when a level is hit, and an "I exited" form.
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { TradeRow } from '../api/types';
import { useApp } from '../lib/app-context';
import { cls, fmt, pnlColor, signedInr, timeIST } from '../lib/format';
import { alertKind, disableSound, enableSound, useSound } from '../lib/sound';
import { Card, Chip } from './ui';

export const isManualOpen = (t: TradeRow) => t.mode === 'manual' && t.status === 'open';

export function SoundButton({ className }: { className?: string }) {
  const { enabled, ready } = useSound();
  if (enabled && ready)
    return (
      <button type="button" className={cls('btn btn-ghost h-11 text-up', className)} onClick={disableSound} title="Sound alerts are on — tap to mute">
        🔔 Sound on
      </button>
    );
  return (
    <button type="button" className={cls('btn h-11 border-warn/50 bg-warn/10 font-semibold text-warn', className)} onClick={enableSound}>
      🔔 Enable sound alerts
    </button>
  );
}

export function AlertBanner({ text }: { text: string }) {
  const k = alertKind(text);
  return (
    <div
      role="alert"
      className={cls(
        'flex items-center gap-2 rounded-lg px-3 py-2.5 text-base font-extrabold tracking-wide',
        k === 'stop' ? 'alert-pulse bg-down text-white' : k === 'target' ? 'bg-up text-white dark:text-slate-900' : 'bg-warn text-slate-900',
      )}
    >
      <span aria-hidden>{k === 'stop' ? '⛔' : k === 'target' ? '🎯' : '⚠'}</span>
      <span className="min-w-0">{text}</span>
    </div>
  );
}

function Level({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</div>
      <div className={cls('break-words font-mono text-base font-semibold', tone)}>{value}</div>
      {sub && <div className="break-words font-mono text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

function ManualTradeCard({ t, onChanged }: { t: TradeRow; onChanged: () => void }) {
  const { toast } = useApp();
  const [exitOpen, setExitOpen] = useState(false);
  const [price, setPrice] = useState('');
  const [busy, setBusy] = useState(false);
  const il = t.index_levels;
  const k = alertKind(t.alert);
  const longIdx = il ? il.stop < il.entry : true;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const p = Number(price);
    if (!(p > 0)) return;
    setBusy(true);
    try {
      await api.manualExit(t.id, p);
      toast(`Exit recorded for ${t.instrument_symbol} @ ${fmt(p)}`, 'up');
      setExitOpen(false);
      onChanged();
    } catch (err) {
      toast(`Could not record exit: ${err instanceof Error ? err.message : err}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  return (
    <article
      className={cls(
        'flex flex-col gap-2 rounded-xl border bg-panel p-3',
        t.alert ? (k === 'stop' ? 'border-down border-2' : k === 'target' ? 'border-up border-2' : 'border-warn') : 'border-line',
      )}
    >
      {t.alert && <AlertBanner text={t.alert} />}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cls('rounded px-1.5 py-0.5 text-xs font-bold', t.side === 'BUY' ? 'bg-up/15 text-up' : 'bg-down/15 text-down')}>{t.side}</span>
            <span className="break-all font-mono text-base font-bold">{t.instrument_symbol}</span>
          </div>
          <div className="text-xs text-muted">
            {t.qty} qty · {t.strategy_id} · since {timeIST(t.entry_ts)}
          </div>
        </div>
        <div className="text-right">
          <div className={cls('font-mono text-xl font-bold', pnlColor(t.pnl))}>{signedInr(t.pnl)}</div>
          <div className="font-mono text-xs text-muted">
            LTP <span className="text-fg">{fmt(t.ltp)}</span>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 rounded-lg bg-panel2/50 p-2">
        <Level label="Entry" value={fmt(t.entry_price)} sub={il ? `idx ${fmt(il.entry)}` : undefined} />
        <Level label="Stop" value={fmt(t.stop)} tone="text-down" sub={il ? `idx ${longIdx ? '<' : '>'} ${fmt(il.stop)}` : undefined} />
        <Level
          label="Target"
          value={t.targets.map((x) => fmt(x)).join(' / ') || '—'}
          tone="text-up"
          sub={il && il.targets.length ? `idx ${il.targets.map((x) => fmt(x)).join(' / ')}` : undefined}
        />
      </div>
      {exitOpen ? (
        <form onSubmit={submit} className="mt-auto flex flex-wrap items-end gap-2">
          <label className="min-w-0 flex-1">
            <span className="label">Exit price (your fill on Angel One)</span>
            <input className="input input-lg font-mono" inputMode="decimal" autoFocus value={price} onChange={(e) => setPrice(e.target.value)} aria-label="Exit price" />
          </label>
          <button type="submit" className="btn btn-primary h-12 px-5" disabled={busy || !(Number(price) > 0)}>
            {busy ? 'Saving…' : 'Save exit'}
          </button>
          <button type="button" className="btn h-12" onClick={() => setExitOpen(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <button
          type="button"
          className={cls('btn mt-auto h-11 w-full font-semibold', t.alert && 'btn-primary')}
          onClick={() => {
            setPrice(t.ltp != null ? t.ltp.toFixed(2) : '');
            setExitOpen(true);
          }}
        >
          ✓ I exited this trade
        </button>
      )}
    </article>
  );
}

export function ManualTradesPanel({ trades, onChanged }: { trades: TradeRow[]; onChanged: () => void }) {
  const rank = (t: TradeRow) => (t.alert ? (alertKind(t.alert) === 'stop' ? 0 : 1) : 2);
  const open = trades.filter(isManualOpen).sort((a, b) => rank(a) - rank(b));
  const alerts = open.filter((t) => t.alert).length;
  const total = open.reduce((a, t) => a + (t.pnl ?? 0), 0);
  if (!open.length) return null;
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          My Angel One trades
          {alerts > 0 && <Chip tone="down">{alerts} alert{alerts > 1 ? 's' : ''}</Chip>}
        </span>
      }
      right={
        <>
          <span className={cls('font-mono text-sm font-bold', pnlColor(total))}>{signedInr(total)}</span>
          <Link to="/trades?mode=manual" className="hidden text-xs text-accent sm:inline">
            History →
          </Link>
        </>
      }
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">Placed by you on the PC. IndexEdge watches the levels and alerts you here + on Telegram.</p>
        <SoundButton />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {open.map((t) => (
          <ManualTradeCard key={t.id} t={t} onChanged={onChanged} />
        ))}
      </div>
    </Card>
  );
}
