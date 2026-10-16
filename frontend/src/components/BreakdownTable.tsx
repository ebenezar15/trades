import type { Breakdown } from '../api/types';
import { normalizeBreakdown } from '../lib/breakdown';
import { cls, fmt, pnlColor, signedInr, titleCase } from '../lib/format';
import { Card, Empty, TableWrap } from './ui';

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function BreakdownTable({ title, data, sort = 'pnl' }: { title: string; data: Breakdown | undefined; sort?: 'pnl' | 'key' | 'weekday' }) {
  let rows = normalizeBreakdown(data);
  if (sort === 'pnl') rows = rows.sort((a, b) => b.pnl - a.pnl);
  else if (sort === 'weekday') rows = rows.sort((a, b) => WD.indexOf(String(a.key).slice(0, 3)) - WD.indexOf(String(b.key).slice(0, 3)));
  else rows = rows.sort((a, b) => String(a.key).localeCompare(String(b.key)));
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.pnl)));
  return (
    <Card title={title}>
      {rows.length === 0 ? (
        <Empty>No data</Empty>
      ) : (
        <TableWrap>
          <table className="tbl">
            <thead>
              <tr>
                <th></th>
                <th className="num">N</th>
                <th className="num">Win%</th>
                <th className="num">Exp R</th>
                <th className="num">PF</th>
                <th className="num">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td className="max-w-[10rem] truncate font-medium">{titleCase(String(r.key))}</td>
                  <td className="num">{r.n}</td>
                  <td className="num">{(r.win_rate * 100).toFixed(0)}%</td>
                  <td className={cls('num', pnlColor(r.expectancy_r))}>{r.expectancy_r != null ? fmt(r.expectancy_r, 2) : '—'}</td>
                  <td className="num">{r.profit_factor != null ? fmt(r.profit_factor, 2) : '—'}</td>
                  <td className="num">
                    <div className={cls('font-semibold', pnlColor(r.pnl))}>{signedInr(r.pnl)}</div>
                    <div className="ml-auto h-1 w-16 rounded bg-panel2">
                      <div className={cls('h-full rounded', r.pnl >= 0 ? 'bg-up' : 'bg-down')} style={{ width: `${(Math.abs(r.pnl) / maxAbs) * 100}%` }} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}
