// Agent: master switch for automated trading, what each strategy is watching right now, and a live activity log.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { AgentLogEntry, AgentResponse, AgentWatch } from '../api/types';
import { useWsMessages } from '../api/ws';
import { Card, Chip, DataSourceBadge, Empty, ErrorBox, Loading, Modal, PageHeader, Segmented, Stat, TableWrap } from '../components/ui';
import { useApp } from '../lib/app-context';
import { cls, timeIST, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

const MAX_LOG = 300;
const logKey = (e: AgentLogEntry) => `${e.ts}|${e.kind}|${e.message}`;
const tsMs = (s: string) => {
  const v = Date.parse(s);
  return Number.isFinite(v) ? v : 0;
};
/** Merge log entries (dedupe by ts+kind+message), newest first, capped. */
function mergeLog(a: AgentLogEntry[], b: AgentLogEntry[]): AgentLogEntry[] {
  const seen = new Set<string>();
  const out: AgentLogEntry[] = [];
  for (const e of [...a, ...b]) {
    const k = logKey(e);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(e);
  }
  return out.sort((x, y) => tsMs(y.ts) - tsMs(x.ts)).slice(0, MAX_LOG);
}

const STATE_TONE: Record<string, 'up' | 'down' | 'warn' | 'neutral' | 'accent'> = { signal: 'up', blocked: 'warn', no_setup: 'neutral', disabled: 'neutral' };
const STATE_LABEL: Record<string, string> = { signal: 'Signal', blocked: 'Blocked', no_setup: 'No setup', disabled: 'Disabled' };
const MODE_LABEL: Record<string, string> = { auto: 'Auto', confirm: 'Confirm', signal_only: 'Signal only' };
const LEVEL_CLS: Record<string, string> = {
  info: 'text-muted',
  warning: 'text-warn',
  error: 'text-down',
  trade: 'text-accent',
};

function StateChip({ state }: { state: string }) {
  return (
    <Chip tone={STATE_TONE[state] ?? 'neutral'} className={cls(state === 'disabled' && 'opacity-60')}>
      {STATE_LABEL[state] ?? titleCase(state)}
    </Chip>
  );
}

function ModeChip({ mode, agentOn }: { mode: string; agentOn: boolean }) {
  const auto = mode === 'auto';
  return (
    <Chip tone={auto ? (agentOn ? 'accent' : 'neutral') : 'neutral'} title={auto && !agentOn ? 'Agent is OFF — Auto behaves like Signal only' : undefined}>
      {MODE_LABEL[mode] ?? titleCase(mode)}
      {auto && !agentOn && ' (paused)'}
    </Chip>
  );
}

function useCountdown(seconds: number | null | undefined, stamp: number) {
  const [nowMs, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  if (seconds === null || seconds === undefined) return null;
  let left = Math.round(seconds - (nowMs - stamp) / 1000);
  if (left < 0) left = ((left % 300) + 300) % 300; // 5-minute bars: roll over until the next poll
  return left;
}
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

function MasterSwitch({ data, onToggle, busy }: { data: AgentResponse; onToggle: (v: boolean) => void; busy: boolean }) {
  const on = data.enabled;
  return (
    <section className={cls('card border-2', on ? 'border-up/60 bg-up/5' : 'border-down/50 bg-down/5')}>
      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="Agent master switch"
          disabled={busy}
          onClick={() => onToggle(!on)}
          className={cls(
            'relative inline-flex h-14 w-28 shrink-0 items-center rounded-full border-2 transition disabled:opacity-60',
            on ? 'border-up bg-up' : 'border-down/70 bg-down/80',
          )}
        >
          <span className={cls('absolute text-xs font-extrabold tracking-wider text-white', on ? 'left-3.5' : 'right-3')}>{on ? 'ON' : 'OFF'}</span>
          <span className={cls('inline-block h-11 w-11 rounded-full bg-white shadow-lg transition', on ? 'translate-x-[3.75rem]' : 'translate-x-1')} />
        </button>
        <div className="min-w-0 flex-1">
          <div className={cls('flex items-center gap-2 text-lg font-bold', on ? 'text-up' : 'text-down')}>
            <span className={cls('h-3 w-3 rounded-full', on ? 'animate-pulse bg-up' : 'bg-down')} />
            Agent is {on ? 'ON' : 'OFF'}
          </div>
          <p className="text-sm text-muted">
            When ON, strategies set to Auto place and manage trades within your risk limits. Paper mode places simulated orders.
            {!on && ' While OFF, Auto strategies behave like Signal only.'}
          </p>
          <p className="mt-1 text-xs text-muted">
            {data.auto_strategies.length} strateg{data.auto_strategies.length === 1 ? 'y' : 'ies'} on Auto
            {data.auto_strategies.length > 0 && `: ${data.auto_strategies.join(', ')}`}
          </p>
        </div>
      </div>
    </section>
  );
}

function WatchTable({ rows, agentOn }: { rows: AgentWatch[]; agentOn: boolean }) {
  return (
    <>
      {/* phone: cards */}
      <ul className="space-y-2 md:hidden">
        {rows.map((w) => (
          <li key={`${w.strategy_id}-${w.underlying}`} className={cls('rounded-lg border border-line p-2.5', !w.enabled && 'opacity-60')}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium">{w.name}</div>
                <div className="text-xs text-muted">
                  {w.underlying} · eval {w.last_eval_ts ? timeIST(w.last_eval_ts) : '—'}
                </div>
              </div>
              <StateChip state={w.state} />
            </div>
            <div className="mt-1.5 flex items-start gap-2">
              <ModeChip mode={w.execution_mode} agentOn={agentOn} />
              <span className="min-w-0 break-words text-xs text-muted">{w.detail}</span>
            </div>
          </li>
        ))}
      </ul>
      {/* desktop: table */}
      <div className="hidden md:block">
        <TableWrap>
          <table className="tbl">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Underlying</th>
                <th>Mode</th>
                <th>State</th>
                <th>Detail</th>
                <th className="num">Last eval</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => (
                <tr key={`${w.strategy_id}-${w.underlying}`} className={cls(!w.enabled && 'opacity-60')}>
                  <td className="font-medium">{w.name}</td>
                  <td>{w.underlying}</td>
                  <td>
                    <ModeChip mode={w.execution_mode} agentOn={agentOn} />
                  </td>
                  <td>
                    <StateChip state={w.state} />
                  </td>
                  <td className="min-w-[14rem] whitespace-normal text-xs text-muted">{w.detail}</td>
                  <td className="num font-mono text-xs">{w.last_eval_ts ? timeIST(w.last_eval_ts) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </div>
    </>
  );
}

function ActivityLog({ log }: { log: AgentLogEntry[] }) {
  const [filter, setFilter] = useState<'all' | 'trade' | 'issues'>('all');
  const rows = useMemo(
    () => log.filter((e) => filter === 'all' || (filter === 'trade' ? e.level === 'trade' : e.level === 'warning' || e.level === 'error')),
    [log, filter],
  );
  return (
    <Card
      title={`Activity log (${log.length})`}
      right={
        <Segmented
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'trade', label: 'Trades' },
            { value: 'issues', label: 'Issues' },
          ]}
        />
      }
    >
      {rows.length === 0 ? (
        <Empty>No activity yet. The agent logs every evaluation, gate decision and order here.</Empty>
      ) : (
        <ol className="max-h-[32rem] space-y-1 overflow-y-auto pr-1 font-mono text-xs">
          {rows.map((e) => (
            <li
              key={logKey(e)}
              className={cls(
                'flex gap-2 rounded px-1.5 py-1',
                e.level === 'trade' && 'border-l-2 border-accent bg-accent/10',
                e.level === 'error' && 'bg-down/5',
              )}
            >
              <span className="shrink-0 text-muted">{timeIST(e.ts)}</span>
              <span className={cls('w-14 shrink-0 uppercase', LEVEL_CLS[e.level] ?? 'text-muted', e.level === 'trade' && 'font-bold')}>{e.kind || e.level}</span>
              <span className={cls('min-w-0 break-words', e.level === 'trade' ? 'font-semibold text-fg' : e.level === 'error' ? 'text-down' : e.level === 'warning' ? 'text-warn' : 'text-fg/90')}>
                {e.message}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export default function Agent() {
  const { toast, status } = useApp();
  const ag = useApi(api.agent, [], 15000);
  const [stamp, setStamp] = useState(() => Date.now());
  const [log, setLog] = useState<AgentLogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [showDisabled, setShowDisabled] = useState(false);

  useEffect(() => {
    if (!ag.data) return;
    setStamp(Date.now());
    setLog((cur) => mergeLog(cur, ag.data!.log ?? []));
  }, [ag.data]);

  useWsMessages(
    useCallback((m) => {
      if (m.type === 'agent') setLog((cur) => mergeLog([m.data], cur));
    }, []),
    ['agent'],
  );

  const left = useCountdown(ag.data?.next_bar_in_s, stamp);
  const d = ag.data;

  const toggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      await api.setAgent(enabled);
      ag.setData((x) => (x ? { ...x, enabled } : x));
      toast(`Agent ${enabled ? 'ON' : 'OFF'}`, enabled ? 'up' : 'warn');
      await ag.reload();
    } catch (e) {
      toast(`Failed: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  const autoAll = async () => {
    setBusy(true);
    try {
      const r = await api.agentAutoAll();
      toast(r.message || 'All enabled strategies set to Auto', 'up');
      setConfirmAuto(false);
      await ag.reload();
    } catch (e) {
      toast(`Failed: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy(false);
    }
  };

  const enabledCount = d ? new Set(d.watching.filter((w) => w.enabled).map((w) => w.strategy_id)).size : 0;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    (d?.watching ?? []).forEach((w) => (c[w.state] = (c[w.state] ?? 0) + 1));
    return c;
  }, [d]);

  return (
    <div className="space-y-3">
      <PageHeader
        title="Agent"
        subtitle="Automated execution of your Auto strategies"
        right={
          d && (
            <button className="btn" disabled={busy} onClick={() => setConfirmAuto(true)}>
              Set all enabled strategies to Auto
            </button>
          )
        }
      />
      {ag.error && !d && <ErrorBox error={ag.error} onRetry={ag.reload} />}
      {!d && ag.loading && <Loading />}

      {d && (
        <>
          <MasterSwitch data={d} onToggle={toggle} busy={busy} />

          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <div className="min-w-0 rounded-lg border border-line bg-panel2/50 px-3 py-2">
              <div className="truncate text-[11px] uppercase tracking-wide text-muted">Data source</div>
              <div className="mt-1">
                <DataSourceBadge source={d.data_source} />
              </div>
              {d.data_source !== 'angel_one' && (
                <Link to="/connect" className="mt-0.5 block truncate text-xs text-accent">
                  Connect Angel One →
                </Link>
              )}
            </div>
            <Stat label="Market" value={d.market_open ? 'Open' : 'Closed'} tone={d.market_open ? 'text-up' : 'text-muted'} sub={status ? `${timeIST(status.server_time)} IST` : undefined} />
            <Stat label="Next 5m bar" value={left === null ? '—' : mmss(left)} sub="strategies evaluate on bar close" />
            <Stat
              label="Watching"
              value={`${enabledCount} strateg${enabledCount === 1 ? 'y' : 'ies'}`}
              sub={`${counts.signal ?? 0} signal · ${counts.blocked ?? 0} blocked · ${counts.no_setup ?? 0} waiting`}
            />
          </div>

          <div className="grid gap-3 2xl:grid-cols-5">
            <Card className="2xl:col-span-3" title="What the agent is watching" right={<Link to="/strategies" className="text-xs text-accent">Strategy Lab →</Link>}>
              {d.watching.length === 0 ? (
                <Empty>No strategies configured.</Empty>
              ) : (
                <WatchTable rows={showDisabled ? d.watching : d.watching.filter((w) => w.enabled && w.state !== 'disabled')} agentOn={d.enabled} />
              )}
              {d.watching.some((w) => !w.enabled || w.state === 'disabled') && (
                <button className="mt-2 text-xs text-accent" onClick={() => setShowDisabled((v) => !v)}>
                  {showDisabled ? 'Hide disabled strategies' : `Show disabled strategies (${d.watching.filter((w) => !w.enabled || w.state === 'disabled').length})`}
                </button>
              )}
            </Card>
            <div className="min-w-0 2xl:col-span-2">
              <ActivityLog log={log} />
            </div>
          </div>
        </>
      )}

      <Modal
        open={confirmAuto}
        onClose={() => setConfirmAuto(false)}
        title="Set all enabled strategies to Auto?"
        footer={
          <>
            <button className="btn" onClick={() => setConfirmAuto(false)}>
              Cancel
            </button>
            <button className="btn btn-primary" disabled={busy} onClick={autoAll}>
              Set {enabledCount} to Auto
            </button>
          </>
        }
      >
        <p className="text-sm">
          Every <b>enabled</b> strategy will place and manage trades on its own whenever the agent is ON, within your risk limits.
          {status?.mode === 'live' ? (
            <b className="text-down"> You are in LIVE mode — these will be real orders with real money.</b>
          ) : (
            ' You are in PAPER mode, so orders are simulated.'
          )}
        </p>
        <p className="mt-2 text-xs text-muted">You can change any strategy back to Signal only or Confirm in the Strategy Lab.</p>
      </Modal>
    </div>
  );
}
