import { useMemo } from 'react';
import { api } from '../api/client';
import { BreakdownTable } from '../components/BreakdownTable';
import { SeriesChart } from '../components/charts';
import { Card, ErrorBox, Loading, PageHeader, Stat } from '../components/ui';
import { fmt, pnlColor, signedInr } from '../lib/format';
import { useApi } from '../lib/useApi';

export default function Analytics() {
  const a = useApi(api.analytics, [], 120000);
  const d = a.data;
  const eq = useMemo(() => (d ? [{ type: 'area' as const, data: d.equity, color: '#38bdf8', title: 'Equity' }] : []), [d]);
  const dd = useMemo(() => (d ? [{ type: 'area' as const, data: d.drawdown, color: '#ef4444', title: 'Drawdown' }] : []), [d]);
  return (
    <div className="space-y-3">
      <PageHeader title="Analytics" subtitle="Performance from your journal (paper + live)." />
      {a.error && <ErrorBox error={a.error} onRetry={a.reload} />}
      {!d && a.loading && <Loading />}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Net P&L" value={signedInr(d.total_pnl)} tone={pnlColor(d.total_pnl)} />
            <Stat label="Trades" value={d.n ?? '—'} />
            <Stat label="Win rate" value={d.win_rate != null ? `${(d.win_rate * 100).toFixed(1)}%` : '—'} />
            <Stat label="Profit factor" value={fmt(d.profit_factor, 2)} />
            <Stat label="Expectancy" value={d.expectancy_r != null ? `${fmt(d.expectancy_r, 2)}R` : '—'} sub={d.expectancy != null ? `${signedInr(d.expectancy)} / trade` : ''} tone={pnlColor(d.expectancy_r)} />
            <Stat label="Max drawdown" value={signedInr(d.max_drawdown)} tone="text-down" sub={d.discipline_score != null ? `Discipline ${d.discipline_score}/100` : ''} />
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <Card title="Equity curve">
              <SeriesChart series={eq} height={260} />
            </Card>
            <Card title="Drawdown">
              <SeriesChart series={dd} height={260} />
            </Card>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            <BreakdownTable title="By strategy" data={d.by_strategy} />
            <BreakdownTable title="By regime" data={d.by_regime} />
            <BreakdownTable title="By hour (IST)" data={d.by_hour} sort="key" />
            <BreakdownTable title="By weekday" data={d.by_weekday} sort="weekday" />
            {d.by_underlying && <BreakdownTable title="By underlying" data={d.by_underlying} />}
          </div>
        </>
      )}
    </div>
  );
}
