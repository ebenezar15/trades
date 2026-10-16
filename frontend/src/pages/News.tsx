import { useState } from 'react';
import { api } from '../api/client';
import type { NewsItem } from '../api/types';
import { useWsMessages } from '../api/ws';
import { Banner, Card, Chip, Empty, ErrorBox, Loading, PageHeader, Segmented } from '../components/ui';
import { cls, dateTimeIST, timeIST, titleCase } from '../lib/format';
import { useApi } from '../lib/useApi';

const impactTone = (i: string) => (i === 'high' ? 'down' : i === 'medium' ? 'warn' : 'neutral');
function sentiment(v: number) {
  if (v > 0.15) return { label: 'Positive', tone: 'up' as const };
  if (v < -0.15) return { label: 'Negative', tone: 'down' as const };
  return { label: 'Neutral', tone: 'neutral' as const };
}

export default function News() {
  const [impact, setImpact] = useState<'all' | 'high' | 'medium'>('all');
  const news = useApi(() => api.news(100), [], 60000);
  const events = useApi(() => api.events(14), [], 60000);
  useWsMessages(
    (m) => {
      if (m.type === 'news') news.setData((d) => ({ items: [m.data as NewsItem, ...(d?.items ?? []).filter((x) => x.id !== m.data.id)] }));
    },
    ['news'],
  );
  const items = (news.data?.items ?? []).filter((n) => impact === 'all' || n.impact === impact || (impact === 'medium' && n.impact === 'high'));
  const bo = events.data?.blackout;

  return (
    <div className="space-y-3">
      <PageHeader title="News & Events" />
      {bo?.active && (
        <Banner tone="down">
          ⛔ News blackout active{bo.event ? `: ${bo.event.title} at ${timeIST(bo.event.ts)} IST` : ''} — new entries are paused.
        </Banner>
      )}
      <div className="grid gap-3 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title="Live feed"
          right={<Segmented value={impact} onChange={setImpact} options={[{ value: 'all', label: 'All' }, { value: 'medium', label: 'Med+' }, { value: 'high', label: 'High' }]} />}
        >
          {news.error && <ErrorBox error={news.error} onRetry={news.reload} />}
          {!news.data && news.loading && <Loading />}
          {news.data && items.length === 0 && <Empty>No news.</Empty>}
          <ul className="divide-y divide-line">
            {items.map((n) => {
              const s = sentiment(n.sentiment);
              return (
                <li key={n.id} className="py-2.5">
                  <a href={n.url} target="_blank" rel="noopener noreferrer" className="font-medium hover:text-accent">
                    {n.title}
                  </a>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                    <span>{n.source}</span>
                    <span>· {dateTimeIST(n.ts)}</span>
                    <Chip tone={impactTone(n.impact)}>{titleCase(n.impact)} impact</Chip>
                    <Chip tone={s.tone}>
                      {s.label} {n.sentiment > 0 ? '+' : ''}
                      {n.sentiment.toFixed(2)}
                    </Chip>
                    <span title="Relevance to Nifty/Bank Nifty">relevance {(n.relevance * 100).toFixed(0)}%</span>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
        <Card title="Upcoming events (14 days)">
          {events.error && <ErrorBox error={events.error} onRetry={events.reload} />}
          {!events.data && events.loading && <Loading />}
          {events.data && events.data.events.length === 0 && <Empty>No scheduled events.</Empty>}
          <ul className="space-y-2">
            {events.data?.events.map((e) => (
              <li key={e.id} className={cls('rounded-lg border p-2', bo?.event?.id === e.id ? 'border-down/50 bg-down/5' : 'border-line')}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{e.title}</span>
                  <Chip tone={impactTone(e.impact)}>{e.impact}</Chip>
                </div>
                <div className="text-xs text-muted">
                  {dateTimeIST(e.ts)} IST · {titleCase(e.category)}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
