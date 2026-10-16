import { api } from '../api/client';
import { useWsMessages, useWsState } from '../api/ws';
import { Card, Chip, ErrorBox, Loading, PageHeader, Stat, TableWrap } from '../components/ui';
import { useApp } from '../lib/app-context';
import { ago, dateTimeIST, fmt } from '../lib/format';
import { useApi } from '../lib/useApi';

export default function Health() {
  const h = useApi(api.health, [], 5000);
  const ws = useWsState();
  const { status } = useApp();
  useWsMessages((m) => m.type === 'health' && h.setData((d) => (d ? { ...d, ...m.data } : d)), ['health']);
  const d = h.data;
  const tickAge = d?.last_tick_age_s;
  const logs = d ? (Array.isArray(d.log_tail) ? d.log_tail : d.log_tail.split('\n')) : [];
  return (
    <div className="space-y-3">
      <PageHeader title="Health" />
      {h.error && <ErrorBox error={h.error} onRetry={h.reload} />}
      {!d && h.loading && <Loading />}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Broker WS" value={d.ws_status} tone={/conn|open|ok/i.test(d.ws_status) ? 'text-up' : 'text-down'} />
            <Stat label="UI socket" value={ws} tone={ws === 'open' ? 'text-up' : 'text-warn'} />
            <Stat label="Last tick" value={ago(tickAge)} tone={tickAge != null && tickAge > 10 ? 'text-down' : 'text-up'} />
            <Stat label="Latency" value={d.latency_ms != null ? `${fmt(d.latency_ms, 0)} ms` : '—'} />
            <Stat label="Broker errors" value={d.broker_errors} tone={d.broker_errors ? 'text-down' : ''} />
            <Stat label="Order rate" value={`${fmt(d.order_rate, 1)}/s`} sub="limit 8/s" />
          </div>
          {status && (
            <div className="flex flex-wrap gap-1.5">
              <Chip tone={status.broker.connected ? 'up' : 'down'}>Broker {status.broker.name}: {status.broker.connected ? 'connected' : 'disconnected'}</Chip>
              <Chip tone={status.broker.logged_in ? 'up' : 'warn'}>{status.broker.logged_in ? 'Logged in' : 'Not logged in'}</Chip>
              <Chip tone={status.static_ip.ok ? 'up' : 'down'}>Static IP {status.static_ip.ok ? 'OK' : 'MISMATCH'}</Chip>
            </div>
          )}
          <Card title="Scheduled jobs">
            <TableWrap>
              <table className="tbl">
                <thead>
                  <tr><th>Job</th><th>Last run</th><th>Next run</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {d.jobs.map((j, i) => (
                    <tr key={j.id ?? j.name ?? i}>
                      <td className="font-mono text-xs">{j.name}</td>
                      <td className="text-xs">{dateTimeIST(j.last_run ?? null)}</td>
                      <td className="text-xs">{dateTimeIST(j.next_run ?? null)}</td>
                      <td>{j.ok === false ? <Chip tone="down">failed</Chip> : <Chip tone="up">ok</Chip>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
          <Card title="Log tail">
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-panel2 p-2 font-mono text-[11px] leading-relaxed">{logs.join('\n')}</pre>
          </Card>
        </>
      )}
    </div>
  );
}
