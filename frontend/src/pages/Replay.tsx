import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { ReplayOutcome, ReplaySignal } from '../api/types';
import { CandleChart, type ChartLine, type ChartMarker } from '../components/charts';
import { Card, Chip, Empty, ErrorBox, Loading, PageHeader, Segmented } from '../components/ui';
import { cls, fmt, timeIST, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';
import { UNDERLYING_OPTIONS } from '../lib/underlyings';

function outcomesList(o: ReplayOutcome[] | Record<string, ReplayOutcome> | undefined): ReplayOutcome[] {
  if (!o) return [];
  return Array.isArray(o) ? o : Object.entries(o).map(([k, v]) => ({ signal_id: k, ...v }));
}
function outcomeFor(s: ReplaySignal, outs: ReplayOutcome[]): ReplayOutcome | undefined {
  return outs.find((o) => (s.id && o.signal_id === s.id) || (o.signal_id === undefined && o.index === s.index));
}

export default function Replay() {
  const [u, setU] = useState('NIFTY');
  const [day, setDay] = useState<string>('');
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<'1' | '2' | '4'>('2');
  const days = useApi(() => api.replayDays(u), [u]);
  useEffect(() => {
    if (days.data?.days.length && !days.data.days.includes(day)) setDay(days.data.days[0]);
  }, [days.data, day]);
  const rp = useApi(() => (day ? api.replay(u, day) : Promise.resolve(null)), [u, day]);
  const n = rp.data?.candles.length ?? 0;
  useEffect(() => {
    setIdx(Math.min(14, Math.max(0, n - 1)));
    setPlaying(false);
  }, [rp.data, n]);
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setIdx((i) => {
        if (i >= n - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, 1000 / Number(speed));
    return () => clearInterval(id);
  }, [playing, speed, n]);

  const outs = useMemo(() => outcomesList(rp.data?.outcomes), [rp.data]);
  const visible = useMemo(() => (rp.data?.signals ?? []).filter((s) => s.index <= idx), [rp.data, idx]);
  const candles = useMemo(() => rp.data?.candles.slice(0, idx + 1) ?? [], [rp.data, idx]);
  const markers = useMemo<ChartMarker[]>(() => {
    const ms: ChartMarker[] = [];
    visible.forEach((s) => {
      const c = rp.data!.candles[s.index];
      ms.push({ time: c.time, position: s.side === 'BUY' ? 'belowBar' : 'aboveBar', color: s.side === 'BUY' ? '#22c55e' : '#ef4444', shape: s.side === 'BUY' ? 'arrowUp' : 'arrowDown', text: s.strategy_id });
      const o = outcomeFor(s, outs);
      if (o?.exit_index !== undefined && o.exit_index <= idx) {
        const ec = rp.data!.candles[o.exit_index];
        ms.push({ time: ec.time, position: 'inBar', color: (o.r ?? 0) > 0 ? '#22c55e' : '#ef4444', shape: 'circle', text: `${(o.r ?? 0) > 0 ? '+' : ''}${fmt(o.r ?? 0, 1)}R` });
      }
    });
    return ms;
  }, [visible, outs, idx, rp.data]);
  const last = visible[visible.length - 1];
  const lines = useMemo<ChartLine[]>(() => {
    if (!last) return [];
    const o = outcomeFor(last, outs);
    if (o?.exit_index !== undefined && o.exit_index <= idx) return [];
    return [
      { price: last.entry, color: '#38bdf8', title: 'Entry' },
      { price: last.stop, color: '#ef4444', title: 'SL', dashed: true },
      ...last.targets.map((t, i) => ({ price: t, color: '#22c55e', title: `T${i + 1}`, dashed: true })),
    ];
  }, [last, outs, idx]);

  return (
    <div className="space-y-3">
      <PageHeader
        title="Replay"
        subtitle="Step through a past session bar by bar; signals appear exactly when they would have."
        right={
          <>
            <Segmented value={u} onChange={setU} options={UNDERLYING_OPTIONS} />
            <select className="input w-auto" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day">
              {days.data?.days.map((d) => <option key={d}>{d}</option>)}
            </select>
          </>
        }
      />
      {(days.error || rp.error) && <ErrorBox error={(days.error || rp.error)!} onRetry={rp.reload} />}
      {rp.loading && !rp.data && <Loading />}
      {rp.data && (
        <div className="grid gap-3 lg:grid-cols-3">
          <Card className="lg:col-span-2" title={`${u} · ${day}`} right={<span className="font-mono text-xs">{n ? `${idx + 1}/${n} · ${timeIST(rp.data.candles[idx].time)}` : ''}</span>}>
            <CandleChart candles={candles} markers={markers} lines={lines} show={{ levels: false }} fitKey={`${u}${day}`} fixedBars={75} height={340} />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button className="btn" onClick={() => setIdx(0)} aria-label="Start">⏮</button>
              <button className="btn" onClick={() => setIdx((i) => Math.max(0, i - 1))} aria-label="Step back">◀</button>
              <button className="btn btn-primary w-20" onClick={() => setPlaying((p) => !p)}>{playing ? '❚❚ Pause' : '▶ Play'}</button>
              <button className="btn" onClick={() => setIdx((i) => Math.min(n - 1, i + 1))} aria-label="Step forward">▶</button>
              <button className="btn" onClick={() => setIdx(n - 1)} aria-label="End">⏭</button>
              <Segmented value={speed} onChange={setSpeed} options={[{ value: '1', label: '1×' }, { value: '2', label: '2×' }, { value: '4', label: '4×' }]} />
              <input type="range" min={0} max={Math.max(0, n - 1)} value={idx} onChange={(e) => setIdx(Number(e.target.value))} className="min-w-0 flex-1" aria-label="Bar" />
            </div>
          </Card>
          <Card title="Signals so far">
            {visible.length === 0 && <Empty>No signal yet at this bar.</Empty>}
            <ul className="space-y-2">
              {visible.map((s, i) => {
                const o = outcomeFor(s, outs);
                const done = o?.exit_index !== undefined && o.exit_index <= idx;
                return (
                  <li key={s.id ?? i} className="rounded-lg border border-line p-2 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className={cls('font-bold', s.side === 'BUY' ? 'text-up' : 'text-down')}>{s.side === 'BUY' ? '▲ LONG' : '▼ SHORT'}</span>
                      <span className="text-xs text-muted">{timeIST(rp.data!.candles[s.index].time)}</span>
                    </div>
                    <div className="text-muted">{s.strategy_name ?? s.strategy_id}</div>
                    <div className="font-mono text-xs">E {fmt(s.entry)} · SL {fmt(s.stop)} · T {s.targets.map((t) => fmt(t)).join('/')}</div>
                    <div className="mt-1">
                      {done ? <Chip tone={(o!.r ?? 0) > 0 ? 'up' : 'down'}>{titleCase(o!.outcome)} {o!.r !== undefined ? `${o!.r > 0 ? '+' : ''}${fmt(o!.r, 2)}R` : ''}</Chip> : <Chip tone="accent">in progress…</Chip>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}
