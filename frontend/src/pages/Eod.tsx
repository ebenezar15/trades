import { useState } from 'react';
import { api } from '../api/client';
import { Card, ErrorBox, Loading, PageHeader, Stat, TableWrap } from '../components/ui';
import { fmt, pnlColor, signedInr, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <>—</>;
  if (typeof v === 'number') return <span className="font-mono">{fmt(v, Number.isInteger(v) ? 0 : 2)}</span>;
  if (typeof v === 'string' || typeof v === 'boolean') return <>{String(v)}</>;
  if (Array.isArray(v)) return <ul className="list-disc pl-4">{v.map((x, i) => <li key={i}><Value v={x} /></li>)}</ul>;
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3">
      {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{titleCase(k)}</dt>
          <dd><Value v={x} /></dd>
        </div>
      ))}
    </dl>
  );
}

export default function Eod() {
  const [day, setDay] = useState('');
  const r = useApi(() => api.eod(day || undefined), [day]);
  const d = r.data;
  const known = new Set(['day', 'pnl', 'trades', 'wins', 'losses', 'summary', 'lessons']);
  const extra = d ? Object.entries(d).filter(([k]) => !known.has(k)) : [];
  return (
    <div className="space-y-3">
      <PageHeader title="End-of-day report" subtitle={d?.day} right={<input type="date" className="input w-auto" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day" />} />
      {r.error && <ErrorBox error={r.error} onRetry={r.reload} />}
      {!d && r.loading && <Loading />}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="P&L" value={signedInr(d.pnl)} tone={pnlColor(d.pnl)} />
            <Stat label="Trades" value={d.trades ?? '—'} />
            <Stat label="Wins" value={d.wins ?? '—'} tone="text-up" />
            <Stat label="Losses" value={d.losses ?? '—'} tone="text-down" />
          </div>
          {d.summary && <Card title="Summary"><p className="text-sm">{d.summary}</p></Card>}
          {d.lessons && d.lessons.length > 0 && (
            <Card title="Lessons">
              <ul className="list-disc space-y-1 pl-5 text-sm">{d.lessons.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </Card>
          )}
          {extra.length > 0 && (
            <Card title="Details">
              <TableWrap>
                <table className="tbl">
                  <tbody>
                    {extra.map(([k, v]) => (
                      <tr key={k}>
                        <td className="align-top text-muted">{titleCase(k)}</td>
                        <td className="whitespace-normal"><Value v={v} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
