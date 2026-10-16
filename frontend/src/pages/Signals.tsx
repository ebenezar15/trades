import { useMemo, useState } from 'react';
import { api } from '../api/client';
import { useWsMessages } from '../api/ws';
import { SignalCard, isPendingConfirm } from '../components/signals';
import { Empty, ErrorBox, Loading, PageHeader, Segmented } from '../components/ui';
import { useApi } from '../lib/useApi';

type Filter = 'all' | 'pending' | 'active' | 'blocked' | 'taken' | 'skipped' | 'expired';

export default function Signals() {
  const [filter, setFilter] = useState<Filter>('all');
  const [u, setU] = useState<'ALL' | 'NIFTY' | 'BANKNIFTY'>('ALL');
  const sig = useApi(() => api.signals(undefined, 200), [], 10000);
  useWsMessages(() => void sig.reload(), ['signal']);

  const list = useMemo(() => {
    const xs = sig.data?.signals ?? [];
    return xs
      .filter((s) => u === 'ALL' || s.underlying === u)
      .filter((s) => (filter === 'all' ? true : filter === 'pending' ? isPendingConfirm(s) : filter === 'active' ? s.status === 'active' && !isPendingConfirm(s) : s.status === filter))
      .sort((a, b) => Number(isPendingConfirm(b)) - Number(isPendingConfirm(a)) || b.ts.localeCompare(a.ts));
  }, [sig.data, filter, u]);
  const pendingN = (sig.data?.signals ?? []).filter(isPendingConfirm).length;

  return (
    <div>
      <PageHeader
        title="Signals"
        subtitle="Probabilities are measured from data with 95% CI and sample size — never claimed."
        right={
          <Segmented
            value={u}
            onChange={setU}
            options={[
              { value: 'ALL', label: 'All' },
              { value: 'NIFTY', label: 'NIFTY' },
              { value: 'BANKNIFTY', label: 'BANKNIFTY' },
            ]}
          />
        }
      />
      <div className="-mx-3 mb-3 overflow-x-auto px-3 sm:mx-0 sm:px-0">
        <Segmented
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'pending', label: `Confirm${pendingN ? ` (${pendingN})` : ''}` },
            { value: 'active', label: 'Active' },
            { value: 'blocked', label: 'Blocked' },
            { value: 'taken', label: 'Taken' },
            { value: 'skipped', label: 'Skipped' },
            { value: 'expired', label: 'Expired' },
          ]}
        />
      </div>
      {sig.loading && !sig.data && <Loading />}
      {sig.error && <ErrorBox error={sig.error} onRetry={sig.reload} />}
      {sig.data && list.length === 0 && (
        <div className="card">
          <Empty>No signals match this filter.</Empty>
        </div>
      )}
      <div className="grid gap-3 xl:grid-cols-2">
        {list.map((s) => (
          <SignalCard key={s.id} s={s} onChanged={sig.reload} />
        ))}
      </div>
    </div>
  );
}
