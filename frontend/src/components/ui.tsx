// Small UI primitives shared by all pages.
import { useEffect, type ReactNode } from 'react';
import { cls, titleCase } from '../lib/format';

export function Card({ title, right, children, className }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cls('card min-w-0', className)}>
      {(title || right) && (
        <div className="card-title">
          <span className="min-w-0 truncate">{title}</span>
          {right && <span className="flex shrink-0 items-center gap-2 normal-case tracking-normal">{right}</span>}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div className="min-w-0">
        <h1 className="text-xl font-bold sm:text-2xl">{title}</h1>
        {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}

type Tone = 'neutral' | 'up' | 'down' | 'warn' | 'accent';
const toneCls: Record<Tone, string> = {
  neutral: '',
  up: 'border-up/40 bg-up/10 text-up',
  down: 'border-down/40 bg-down/10 text-down',
  warn: 'border-warn/40 bg-warn/10 text-warn',
  accent: 'border-accent/40 bg-accent/10 text-accent',
};
export function Chip({ tone = 'neutral', children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span className={cls('chip', toneCls[tone], className)} title={title}>
      {children}
    </span>
  );
}

const BADGES: Record<string, { label: string; tone: Tone }> = {
  validated: { label: '✓ Validated', tone: 'up' },
  high_win_rate: { label: '🏅 High win-rate', tone: 'accent' },
  insufficient_data: { label: 'Insufficient data', tone: 'neutral' },
  rejected: { label: '✕ Rejected', tone: 'down' },
  negative_expectancy_warning: { label: '⚠ Negative expectancy', tone: 'warn' },
};
export function StrategyBadge({ badge }: { badge: string }) {
  const b = BADGES[badge] ?? { label: titleCase(badge), tone: 'neutral' as Tone };
  return <Chip tone={b.tone}>{b.label}</Chip>;
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cls('inline-block h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-transparent', className)} />;
}

export function Loading({ text = 'Loading…' }: { text?: string }) {
  return (
    <div className="flex items-center gap-2 p-4 text-sm text-muted">
      <Spinner /> {text}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-down/40 bg-down/10 p-3 text-sm text-down">
      <span className="min-w-0 break-words">⚠ {error}</span>
      {onRetry && (
        <button className="btn ml-auto" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-4 text-center text-sm text-muted">{children}</div>;
}

export function Banner({ tone, children }: { tone: 'down' | 'warn' | 'accent' | 'up'; children: ReactNode }) {
  return <div className={cls('rounded-lg border px-3 py-2 text-sm font-medium', toneCls[tone])}>{children}</div>;
}

/** Horizontal progress meter (0..100). */
export function Meter({ label, value, max = 100, display, danger = 80 }: { label: string; value: number; max?: number; display?: string; danger?: number }) {
  const pctv = Math.max(0, Math.min(100, (value / max) * 100));
  const color = pctv >= danger ? 'bg-down' : pctv >= danger * 0.6 ? 'bg-warn' : 'bg-up';
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="font-mono">{display ?? `${pctv.toFixed(0)}%`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-panel2">
        <div className={cls('h-full rounded-full transition-all', color)} style={{ width: `${pctv}%` }} />
      </div>
    </div>
  );
}

/** Bias meter for a value in -1..1 (bearish..bullish). */
export function BiasMeter({ value, label = 'Macro bias' }: { value: number | null | undefined; label?: string }) {
  const v = Math.max(-1, Math.min(1, value ?? 0));
  const word = value === null || value === undefined ? 'n/a' : v > 0.25 ? 'Bullish' : v < -0.25 ? 'Bearish' : 'Neutral';
  const tone = v > 0.25 ? 'text-up' : v < -0.25 ? 'text-down' : 'text-muted';
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className={cls('font-semibold', tone)}>
          {word} {value !== null && value !== undefined && <span className="font-mono">({v > 0 ? '+' : ''}{v.toFixed(2)})</span>}
        </span>
      </div>
      <div className="relative h-2.5 rounded-full bg-gradient-to-r from-down/70 via-panel2 to-up/70">
        <div className="absolute top-1/2 h-4 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded bg-fg shadow" style={{ left: `${((v + 1) / 2) * 100}%` }} />
      </div>
      <div className="mt-0.5 flex justify-between text-[10px] text-muted">
        <span>Bearish</span>
        <span>Bullish</span>
      </div>
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer, wide, dismissable = true }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  dismissable?: boolean;
}) {
  useEffect(() => {
    if (!open || !dismissable) return;
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose, dismissable]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4" onClick={dismissable ? onClose : undefined}>
      <div
        role="dialog"
        aria-modal="true"
        className={cls('max-h-[92vh] w-full overflow-y-auto rounded-t-2xl border border-line bg-panel p-4 shadow-2xl sm:rounded-2xl', wide ? 'sm:max-w-3xl' : 'sm:max-w-md')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          {dismissable && (
            <button className="btn btn-ghost -mr-2 -mt-1 px-2" onClick={onClose} aria-label="Close">
              ✕
            </button>
          )}
        </div>
        {children}
        {footer && <div className="mt-4 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <aside className="h-full w-full max-w-xl overflow-y-auto border-l border-line bg-panel p-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-2">
          <h2 className="min-w-0 break-words text-lg font-semibold">{title}</h2>
          <button className="btn btn-ghost -mr-2 px-2" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </aside>
    </div>
  );
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cls('relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-line transition disabled:opacity-50', checked ? 'bg-up' : 'bg-panel2')}
    >
      <span className={cls('inline-block h-4 w-4 rounded-full bg-white shadow transition', checked ? 'translate-x-6' : 'translate-x-1')} />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: Array<{ value: T; label: ReactNode }>; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cls('whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-semibold transition', value === o.value ? 'bg-panel text-fg shadow' : 'text-muted')}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Stat({ label, value, tone, sub }: { label: string; value: ReactNode; tone?: string; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-line bg-panel2/50 px-3 py-2">
      <div className="truncate text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={cls('truncate font-mono text-lg font-semibold', tone)}>{value}</div>
      {sub && <div className="truncate text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="-mx-3 overflow-x-auto px-3 sm:-mx-4 sm:px-4">{children}</div>;
}
