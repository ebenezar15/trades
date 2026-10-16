import { api } from '../api/client';
import { useWsMessages } from '../api/ws';
import { Banner, BiasMeter, Card, Chip, ErrorBox, Loading, Meter, PageHeader, TableWrap } from '../components/ui';
import { ago, cls, dateTimeIST, fmt, pct, pnlColor } from '../lib/format';
import { useApi } from '../lib/useApi';

export default function Context() {
  const ctx = useApi(api.context, [], 30000);
  useWsMessages((m) => m.type === 'context' && ctx.setData(m.data), ['context']);
  const c = ctx.data;
  const rows = c ? Object.entries(c.inputs).sort((a, b) => Number(b[1].required) - Number(a[1].required) || Math.abs(b[1].weight) - Math.abs(a[1].weight)) : [];
  return (
    <div className="space-y-3">
      <PageHeader title="Market Context" subtitle={c ? `Updated ${dateTimeIST(c.ts)} IST` : 'External cues checked before any signal'} />
      {ctx.error && <ErrorBox error={ctx.error} onRetry={ctx.reload} />}
      {!c && ctx.loading && <Loading />}
      {c && (
        <>
          {c.risk_off && <Banner tone="down">⚠ RISK-OFF — macro bias or VIX beyond threshold. New long signals are blocked.</Banner>}
          {!c.usable && (
            <Banner tone="warn">
              Context not usable — required input(s) stale: {c.stale_required.join(', ') || 'unknown'}. Signals are paused until fresh data arrives.
            </Banner>
          )}
          <div className="grid gap-3 md:grid-cols-3">
            <Card title="Bias" className="md:col-span-2">
              <BiasMeter value={c.macro_bias} />
              <div className="mt-4">
                <Meter label="Event risk (approaching high-impact events)" value={c.event_risk * 100} danger={70} />
              </div>
            </Card>
            <Card title="Why">
              <ul className="space-y-1 text-sm">
                {c.reasons.map((r, i) => (
                  <li key={i}>• {r}</li>
                ))}
              </ul>
            </Card>
          </div>
          <Card title="External indices">
            <TableWrap>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Input</th>
                    <th className="num">Value</th>
                    <th className="num">Change</th>
                    <th className="num">Weight</th>
                    <th>Freshness</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([k, v]) => {
                    const effect = (v.change_pct ?? 0) * v.weight;
                    return (
                      <tr key={k}>
                        <td>
                          <span className="font-medium">{v.label}</span>
                          {v.required && <span className="ml-1.5 text-[10px] font-bold uppercase text-accent">required</span>}
                        </td>
                        <td className="num">{fmt(v.value)}</td>
                        <td className={cls('num', pnlColor(v.change_pct))}>
                          {pct(v.change_pct, 2, true)}
                          <span className={cls('ml-1 text-[10px]', effect > 0 ? 'text-up' : effect < 0 ? 'text-down' : 'text-muted')}>{effect > 0.05 ? '▲' : effect < -0.05 ? '▼' : '·'}</span>
                        </td>
                        <td className="num text-muted">{v.weight > 0 ? '+' : ''}{fmt(v.weight, 1)}</td>
                        <td>
                          {v.stale ? <Chip tone={v.required ? 'down' : 'warn'}>stale · {ago(v.age_s)}</Chip> : <Chip tone="up">fresh · {ago(v.age_s)}</Chip>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
            <p className="mt-2 text-xs text-muted">▲/▼ = effect on Nifty after applying the input's weight sign (e.g. rising VIX or USD/INR is bearish).</p>
          </Card>
        </>
      )}
    </div>
  );
}
