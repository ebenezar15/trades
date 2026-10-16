import { useState } from 'react';
import { api } from '../api/client';
import type { KeyLevels, Regime } from '../api/types';
import { BiasMeter, Card, Chip, Empty, ErrorBox, Loading, PageHeader, TableWrap } from '../components/ui';
import { cls, dateTimeIST, fmt, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

const LEVEL_KEYS: Array<keyof KeyLevels> = ['r1', 'tc', 'pivot', 'bc', 's1', 'prev_high', 'prev_low', 'or_high', 'or_low'];

export default function Premarket() {
  const pm = useApi(api.premarket, [], 60000);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const d = pm.data;
  const regimes: Record<string, Regime> = d ? (typeof d.regime === 'string' ? { ALL: d.regime } : d.regime) : {};
  const gap = d?.gap_expectation;
  const checklist = [
    'Reviewed global cues & macro bias',
    'Noted gap expectation and key levels',
    'Checked today’s events & blackout windows',
    'Confirmed enabled strategies suit today’s regime',
    'Risk limits & capital set; mood is calm',
    'Broker login done & static IP OK',
  ];
  return (
    <div className="space-y-3">
      <PageHeader title="Pre-market checklist" subtitle={d ? `For ${d.date}` : undefined} />
      {pm.error && <ErrorBox error={pm.error} onRetry={pm.reload} />}
      {!d && pm.loading && <Loading />}
      {d && (
        <div className="grid gap-3 lg:grid-cols-2">
          <Card title="Global cues">
            {d.context ? (
              <>
                <BiasMeter value={d.context.macro_bias} />
                <ul className="mt-2 space-y-0.5 text-sm">
                  {d.context.reasons.map((r, i) => <li key={i}>• {r}</li>)}
                </ul>
                {d.context.risk_off && <p className="mt-2 font-semibold text-down">RISK-OFF</p>}
              </>
            ) : (
              <Empty>No context yet.</Empty>
            )}
          </Card>
          <Card title="Gap expectation & regime">
            <p className="text-lg font-semibold">
              {typeof gap === 'string' ? gap : gap?.text ?? (gap?.points != null ? `${gap.points > 0 ? '+' : ''}${fmt(gap.points, 0)} pts (${fmt(gap.pct, 2)}%)` : '—')}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(regimes).map(([k, v]) => <Chip key={k} tone={v === 'trend_up' ? 'up' : v === 'trend_down' ? 'down' : v === 'high_vol' ? 'warn' : 'neutral'}>{k}: {titleCase(v)}</Chip>)}
            </div>
          </Card>
          <Card title="Key levels">
            <TableWrap>
              <table className="tbl">
                <thead>
                  <tr>
                    <th></th>
                    {Object.keys(d.key_levels).map((u) => <th key={u} className="num">{u}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {LEVEL_KEYS.map((k) => (
                    <tr key={k}>
                      <td className="uppercase text-muted">{k.replace('_', ' ')}</td>
                      {Object.values(d.key_levels).map((lv, i) => <td key={i} className="num">{fmt(lv[k])}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
          <Card title="Events today">
            {d.events_today.length === 0 ? <Empty>No scheduled events today.</Empty> : (
              <ul className="space-y-1 text-sm">
                {d.events_today.map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-2">
                    <span>{e.title} <span className="text-muted">· {dateTimeIST(e.ts)}</span></span>
                    <Chip tone={e.impact === 'high' ? 'down' : 'warn'}>{e.impact}</Chip>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Strategies enabled today">
            <div className="flex flex-wrap gap-1.5">
              {d.enabled_strategies.map((s) => {
                const o = typeof s === 'string' ? { id: s, name: s } : s;
                const suits = typeof s === 'string' ? undefined : s.suits_regime;
                return <Chip key={o.id} tone={suits === false ? 'warn' : 'up'} title={suits === false ? 'Does not suit current regime' : undefined}>{o.name}{suits === false ? ' ⚠' : ''}</Chip>;
              })}
            </div>
          </Card>
          <Card title="Checklist">
            <ul className="space-y-1.5">
              {checklist.map((c) => (
                <li key={c}>
                  <label className={cls('flex items-center gap-2 text-sm', done[c] && 'text-muted line-through')}>
                    <input type="checkbox" checked={!!done[c]} onChange={(e) => setDone((x) => ({ ...x, [c]: e.target.checked }))} />
                    {c}
                  </label>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted">{Object.values(done).filter(Boolean).length}/{checklist.length} done</p>
          </Card>
        </div>
      )}
    </div>
  );
}
