import { cloneElement, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { OptionLeg } from '../api/types';
import { Card, Chip, ErrorBox, Loading, PageHeader, Segmented, Stat, TableWrap, Toggle } from '../components/ui';
import { cls, compact, fmt, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

const BUILDUP_BG: Record<string, string> = {
  long_buildup: 'bg-up/25',
  short_buildup: 'bg-down/25',
  short_covering: 'bg-accent/20',
  long_unwinding: 'bg-warn/25',
  neutral: '',
};
const BUILDUP_SHORT: Record<string, string> = { long_buildup: 'LB', short_buildup: 'SB', short_covering: 'SC', long_unwinding: 'LU', neutral: '' };

function Bar({ value, max, side, tone }: { value: number; max: number; side: 'l' | 'r'; tone: string }) {
  const w = max ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
  return (
    <div className={cls('relative h-1.5 w-full rounded bg-panel2', side === 'l' && 'rotate-180')}>
      <div className={cls('absolute inset-y-0 left-0 rounded', tone)} style={{ width: `${w}%` }} />
    </div>
  );
}

export default function OptionChain() {
  const [u, setU] = useState('NIFTY');
  const [expiry, setExpiry] = useState<string | undefined>();
  const [greeks, setGreeks] = useState(() => typeof window !== 'undefined' && window.innerWidth >= 1024);
  const oc = useApi(() => api.optionChain(u, expiry), [u, expiry], 15000);
  const d = oc.data;

  const { maxOi, maxChg, atm } = useMemo(() => {
    if (!d) return { maxOi: 0, maxChg: 0, atm: 0 };
    let mo = 0,
      mc = 0;
    d.rows.forEach((r) => {
      [r.ce, r.pe].forEach((l) => {
        if (!l) return;
        mo = Math.max(mo, l.oi ?? 0);
        mc = Math.max(mc, Math.abs(l.oi_change ?? 0));
      });
    });
    const atmRow = d.rows.reduce((a, r) => (Math.abs(r.strike - d.spot) < Math.abs(a.strike - d.spot) ? r : a), d.rows[0]);
    return { maxOi: mo, maxChg: mc, atm: atmRow?.strike ?? 0 };
  }, [d]);

  const legCells = (l: OptionLeg | null, side: 'ce' | 'pe') => {
    const cells = [
      <td key="oi" className="min-w-[88px]">
        <div className="num text-xs">{compact(l?.oi)}</div>
        <Bar value={l?.oi ?? 0} max={maxOi} side={side === 'ce' ? 'l' : 'r'} tone={side === 'ce' ? 'bg-down/70' : 'bg-up/70'} />
      </td>,
      <td key="chg" className={cls('min-w-[80px]', l?.buildup ? BUILDUP_BG[l.buildup] : '')} title={l?.buildup ? titleCase(l.buildup) : ''}>
        <div className={cls('num text-xs', (l?.oi_change ?? 0) >= 0 ? 'text-up' : 'text-down')}>
          {(l?.oi_change ?? 0) > 0 ? '+' : ''}
          {compact(l?.oi_change)}
          {l?.buildup && BUILDUP_SHORT[l.buildup] && <span className="ml-1 text-[9px] font-bold text-muted">{BUILDUP_SHORT[l.buildup]}</span>}
        </div>
        <Bar value={l?.oi_change ?? 0} max={maxChg} side={side === 'ce' ? 'l' : 'r'} tone={(l?.oi_change ?? 0) >= 0 ? 'bg-up/60' : 'bg-down/60'} />
      </td>,
      <td key="iv" className="num text-xs">{fmt(l?.iv, 1)}</td>,
      ...(greeks
        ? [
            <td key="d" className="num text-xs">{fmt(l?.delta, 2)}</td>,
            <td key="g" className="num text-xs">{fmt(l?.gamma, 4)}</td>,
            <td key="t" className="num text-xs">{fmt(l?.theta, 1)}</td>,
            <td key="v" className="num text-xs">{fmt(l?.vega, 1)}</td>,
          ]
        : []),
      <td key="ltp" className="num font-semibold">{fmt(l?.ltp)}</td>,
    ];
    return side === 'ce' ? cells : cells.reverse();
  };
  const head = ['OI', 'ΔOI', 'IV', ...(greeks ? ['Δ', 'Γ', 'Θ', 'Vega'] : []), 'LTP'];

  return (
    <div className="space-y-3">
      <PageHeader
        title="Option Chain"
        right={
          <>
            <Segmented value={u} onChange={(v) => { setU(v); setExpiry(undefined); }} options={[{ value: 'NIFTY', label: 'NIFTY' }, { value: 'BANKNIFTY', label: 'BANKNIFTY' }]} />
            {d && (
              <select className="input w-auto" value={d.expiry} onChange={(e) => setExpiry(e.target.value)} aria-label="Expiry">
                {d.expiries.map((x) => (
                  <option key={x} value={x}>{x}</option>
                ))}
              </select>
            )}
            <label className="flex items-center gap-2 text-xs text-muted">
              Greeks <Toggle checked={greeks} onChange={setGreeks} label="Show greeks" />
            </label>
          </>
        }
      />
      {oc.error && <ErrorBox error={oc.error} onRetry={oc.reload} />}
      {!d && oc.loading && <Loading />}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Spot" value={fmt(d.spot)} />
            <Stat label="PCR (OI)" value={fmt(d.pcr, 2)} tone={(d.pcr ?? 1) > 1.2 ? 'text-up' : (d.pcr ?? 1) < 0.8 ? 'text-down' : ''} sub={(d.pcr ?? 1) > 1 ? 'Put-heavy (supportive)' : 'Call-heavy (capping)'} />
            <Stat label="Max pain" value={fmt(d.max_pain, 0)} sub={d.max_pain ? `${fmt(d.max_pain - d.spot, 0)} pts from spot` : ''} />
            <Stat label="ATM" value={fmt(atm, 0)} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted">Resistance (CE walls):</span>
            {d.walls.resistance.map((w) => <Chip key={w} tone="down">{fmt(w, 0)}</Chip>)}
            <span className="ml-2 text-muted">Support (PE walls):</span>
            {d.walls.support.map((w) => <Chip key={w} tone="up">{fmt(w, 0)}</Chip>)}
          </div>
          <Card>
            <TableWrap>
              <table className="tbl">
                <thead>
                  <tr>
                    <th colSpan={head.length} className="text-center text-down">CALLS</th>
                    <th className="text-center">Strike</th>
                    <th colSpan={head.length} className="text-center text-up">PUTS</th>
                  </tr>
                  <tr>
                    {head.map((h) => <th key={'c' + h} className="num">{h}</th>)}
                    <th className="text-center">—</th>
                    {[...head].reverse().map((h) => <th key={'p' + h} className="num">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => {
                    const isAtm = r.strike === atm;
                    const ceItm = r.strike < d.spot;
                    const res = d.walls.resistance.includes(r.strike);
                    const sup = d.walls.support.includes(r.strike);
                    return (
                      <tr key={r.strike} className={cls(isAtm && 'outline outline-2 -outline-offset-2 outline-accent')}>
                        {legCells(r.ce, 'ce').map((c, i) => (
                          <ItmWrap key={'c' + i} itm={ceItm}>{c}</ItmWrap>
                        ))}
                        <td className={cls('text-center font-mono font-bold', isAtm ? 'bg-accent/20 text-accent' : 'bg-panel2')}>
                          {fmt(r.strike, 0)}
                          {(res || sup) && <div className={cls('text-[9px] font-semibold', res ? 'text-down' : 'text-up')}>{res && sup ? 'R/S wall' : res ? 'R wall' : 'S wall'}</div>}
                        </td>
                        {legCells(r.pe, 'pe').map((c, i) => (
                          <ItmWrap key={'p' + i} itm={!ceItm && !isAtm}>{c}</ItmWrap>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
            <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted">
              <span className="rounded bg-up/25 px-1.5">LB Long buildup</span>
              <span className="rounded bg-down/25 px-1.5">SB Short buildup</span>
              <span className="rounded bg-accent/20 px-1.5">SC Short covering</span>
              <span className="rounded bg-warn/25 px-1.5">LU Long unwinding</span>
              <span>Shaded = in the money · outlined row = ATM</span>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// Adds ITM tint to a cell without losing its own buildup background.
function ItmWrap({ itm, children }: { itm: boolean; children: React.ReactElement<{ className?: string }> }) {
  if (!itm) return children;
  const own = children.props.className ?? '';
  return cloneElement(children, { className: cls(own, !/\bbg-/.test(own) && 'bg-warn/[0.06]') });
}
