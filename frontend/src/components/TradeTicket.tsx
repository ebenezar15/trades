// "Trade on Angel One" ticket (Iteration 3). The user places orders by hand in Angel One on the PC; this sheet lists
// every value in the order the Angel One order window asks for it, each with a copy button, then journals the fill.
import { useState, type FormEvent, type ReactNode } from 'react';
import { api } from '../api/client';
import type { SignalJSON, TradeTicket } from '../api/types';
import { useApp } from '../lib/app-context';
import { cls, fmt } from '../lib/format';
import { useApi } from '../lib/useApi';
import { copyText } from './ConnectWizard';
import { ErrorBox, Loading, Modal } from './ui';

/** Signals that can still be traded by hand. */
export function isTradable(s: SignalJSON): boolean {
  return s.status === 'active' || s.status === 'pending_confirm' || s.status === 'pending';
}

const px = (v: number | null | undefined) => (v === null || v === undefined || Number.isNaN(v) ? '' : v.toFixed(2));

function BigCopy({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle');
  return (
    <button
      type="button"
      className={cls(
        'inline-flex h-11 min-w-[72px] shrink-0 items-center justify-center rounded-lg border px-3 text-sm font-semibold transition active:scale-95',
        state === 'ok' ? 'border-up/60 bg-up/10 text-up' : state === 'fail' ? 'border-down/50 text-down' : 'border-line bg-panel2 text-fg',
      )}
      onClick={async () => {
        const ok = await copyText(text);
        setState(ok ? 'ok' : 'fail');
        setTimeout(() => setState('idle'), 1400);
      }}
      aria-label={`Copy ${label}: ${text}`}
    >
      {state === 'ok' ? '✓ Copied' : state === 'fail' ? 'Select' : 'Copy'}
    </button>
  );
}

function Row({ n, label, value, copy, tone, big, sub }: {
  n?: number;
  label: string;
  value: ReactNode;
  copy?: string;
  tone?: string;
  big?: boolean;
  sub?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-line/60 py-2 last:border-0">
      {n !== undefined && (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-panel2 text-[11px] font-bold text-muted">{n}</span>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</div>
        <div className={cls('select-all break-all font-mono font-bold', big ? 'text-xl sm:text-2xl' : 'text-lg', tone)}>{value}</div>
        {sub && <div className="text-xs text-muted">{sub}</div>}
      </div>
      {copy !== undefined && <BigCopy text={copy} label={label} />}
    </div>
  );
}

function Section({ title, tone, children, right }: { title: string; tone?: 'down' | 'up' | 'accent'; children: ReactNode; right?: ReactNode }) {
  return (
    <section
      className={cls(
        'rounded-xl border p-3',
        tone === 'down' ? 'border-down/40 bg-down/5' : tone === 'up' ? 'border-up/40 bg-up/5' : 'border-line bg-panel2/30',
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className={cls('text-sm font-bold uppercase tracking-wide', tone === 'down' ? 'text-down' : tone === 'up' ? 'text-up' : 'text-accent')}>{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function indexLine(t: TradeTicket): ReactNode {
  const il = t.index_levels;
  if (!il) return null;
  const longIndex = il.stop < il.entry; // stop below entry ⇒ the plan profits when the index rises
  const dir = longIndex ? 'below' : 'above';
  const tgtDir = longIndex ? 'above' : 'below';
  return (
    <Section title={`Or watch the index (${t.underlying})`} tone="accent">
      <p className="text-base">
        Exit if <b>{t.underlying}</b> trades <b>{dir}</b> <b className="font-mono text-down">{fmt(il.stop)}</b>.
      </p>
      {il.targets.length > 0 && (
        <p className="mt-1 text-base">
          Book profit {tgtDir} <b className="font-mono text-up">{il.targets.map((x) => fmt(x)).join(' / ')}</b>.
        </p>
      )}
      <p className="mt-1 text-xs text-muted">Planned on the index at {fmt(il.entry)}. IndexEdge also alerts you at these levels.</p>
    </Section>
  );
}

function TicketBody({ t, onPlaced }: { t: TradeTicket; onPlaced: () => void }) {
  const { toast } = useApp();
  const [fill, setFill] = useState(px(t.limit_price));
  const [qty, setQty] = useState(String(t.quantity));
  const [busy, setBusy] = useState(false);
  const buy = t.side === 'BUY';
  const exitSide = buy ? 'SELL' : 'BUY';
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const f = Number(fill);
    const q = Math.round(Number(qty));
    if (!(f > 0) || !(q > 0)) return;
    setBusy(true);
    try {
      const r = await api.manualEntry(t.signal_id, { fill_price: f, quantity: q });
      toast(`✓ Trade journaled (${r.trade_id}) — IndexEdge will alert you at the stop and target`, 'up');
      onPlaced();
    } catch (err) {
      toast(`Could not save: ${err instanceof Error ? err.message : err}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <Section title="1 · Entry order" tone={buy ? 'up' : 'down'} right={t.ltp != null && <span className="text-xs text-muted">LTP <b className="font-mono text-fg">{fmt(t.ltp)}</b></span>}>
          <Row n={1} label="Exchange" value={t.exchange} copy={t.exchange} />
          <Row n={2} label="Symbol" value={t.symbol} copy={t.symbol} big />
          <Row n={3} label="Buy / Sell" value={t.side} tone={buy ? 'text-up' : 'text-down'} />
          <Row n={4} label="Product" value={t.product} />
          <Row n={5} label="Order type" value={t.order_type} />
          <Row n={6} label="Quantity" value={t.quantity} copy={String(t.quantity)} sub={`${t.lots} lot${t.lots === 1 ? '' : 's'} × ${t.lot_size}`} />
          <Row n={7} label="Limit price" value={px(t.limit_price)} copy={px(t.limit_price)} tone="text-accent" />
        </Section>
        <div className="space-y-3">
          <Section title="2 · Stop-loss (right after the fill)" tone="down">
            <Row label="Buy / Sell" value={exitSide} tone={buy ? 'text-down' : 'text-up'} sub={`Same symbol · ${t.product} · qty ${t.quantity}`} />
            <Row label="Order type" value="SL-L (stop-loss limit)" />
            <Row label="Trigger price" value={px(t.stop_trigger)} copy={px(t.stop_trigger)} tone="text-down" />
            <Row label="Limit price" value={px(t.stop_limit)} copy={px(t.stop_limit)} tone="text-down" />
          </Section>
          <Section title="3 · Target" tone="up">
            <Row label={`${exitSide} limit at`} value={px(t.target)} copy={px(t.target)} tone="text-up" sub="Place it, or exit by hand when IndexEdge alerts you." />
          </Section>
        </div>
      </div>
      {t.premium_basis === 'future' && (
        <p className="rounded-lg border border-warn/40 bg-warn/10 p-2 text-sm text-warn">Prices are for the index future, not an option premium.</p>
      )}
      {indexLine(t)}
      {(t.steps.length > 0 || t.notes.length > 0) && (
        <div className="grid gap-3 md:grid-cols-2">
          {t.steps.length > 0 && (
            <Section title="Steps in Angel One">
              <ol className="list-decimal space-y-1 pl-5 text-sm leading-relaxed">
                {t.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            </Section>
          )}
          {t.notes.length > 0 && (
            <Section title="Notes">
              <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-muted">
                {t.notes.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
      <form onSubmit={submit} className="sticky bottom-0 -mx-4 -mb-4 border-t border-line bg-panel px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label>
            <span className="label">Fill price</span>
            <input className="input input-lg font-mono" inputMode="decimal" value={fill} onChange={(e) => setFill(e.target.value)} aria-label="Fill price" />
          </label>
          <label>
            <span className="label">Quantity</span>
            <input className="input input-lg font-mono" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ''))} aria-label="Quantity" />
          </label>
          <button type="submit" className="btn btn-primary col-span-2 h-12 text-base sm:col-span-1" disabled={busy || !(Number(fill) > 0) || !(Number(qty) > 0)}>
            {busy ? 'Saving…' : '✓ I placed this trade'}
          </button>
        </div>
      </form>
    </div>
  );
}

export function TicketModal({ signalId, onClose, onPlaced }: { signalId: string | null; onClose: () => void; onPlaced?: () => void }) {
  const tk = useApi(() => (signalId ? api.ticket(signalId) : Promise.resolve(null)), [signalId]);
  const t = tk.data;
  return (
    <Modal
      open={!!signalId}
      onClose={onClose}
      wide
      title={
        t ? (
          <span>
            Trade on Angel One
            <span className="block text-sm font-normal text-muted">
              {t.strategy_name} · {t.underlying}
            </span>
          </span>
        ) : (
          'Trade on Angel One'
        )
      }
    >
      {tk.loading && !t && <Loading text="Building ticket…" />}
      {tk.error && <ErrorBox error={tk.error} onRetry={tk.reload} />}
      {t && (
        <TicketBody
          t={t}
          onPlaced={() => {
            onPlaced?.();
            onClose();
          }}
        />
      )}
    </Modal>
  );
}

/** Button + modal, for signal cards. */
export function TradeTicketButton({ s, onPlaced, className }: { s: SignalJSON; onPlaced?: () => void; className?: string }) {
  const [open, setOpen] = useState(false);
  if (!isTradable(s)) return null;
  return (
    <>
      <button type="button" className={cls('btn h-11 border-accent/60 bg-accent/10 font-semibold text-accent', className)} onClick={() => setOpen(true)}>
        📋 Trade on Angel One
      </button>
      <TicketModal signalId={open ? s.id : null} onClose={() => setOpen(false)} onPlaced={onPlaced} />
    </>
  );
}
