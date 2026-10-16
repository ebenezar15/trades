import { useMemo, useState } from 'react';
import { api } from '../api/client';
import { CandleChart, type ChartMarker } from '../components/charts';
import { Markdown } from '../components/Markdown';
import { Card, Chip, ErrorBox, Loading, PageHeader, Segmented, StrategyBadge } from '../components/ui';
import { cls } from '../lib/format';
import { useApi } from '../lib/useApi';
import { UNDERLYING_OPTIONS } from '../lib/underlyings';

function readScore(): { right: number; total: number } {
  try {
    return JSON.parse(localStorage.getItem('ie-quiz-score') ?? '') as { right: number; total: number };
  } catch {
    return { right: 0, total: 0 };
  }
}

function Quiz() {
  const [u, setU] = useState('NIFTY');
  const [nonce, setNonce] = useState(0);
  const q = useApi(() => api.quiz(u), [u, nonce]);
  const [picked, setPicked] = useState<string | null>(null);
  const [score, setScore] = useState(readScore);
  const d = q.data;
  const shown = useMemo(() => (d ? (picked ? d.candles : d.candles.slice(0, d.upto_index + 1)) : []), [d, picked]);
  const markers = useMemo<ChartMarker[]>(() => (d && d.candles[d.upto_index] ? [{ time: d.candles[d.upto_index].time, position: 'aboveBar', color: '#38bdf8', shape: 'arrowDown', text: 'Now' }] : []), [d]);
  const pick = (id: string) => {
    if (!d || picked) return;
    setPicked(id);
    const s = { right: score.right + (id === d.answer_id ? 1 : 0), total: score.total + 1 };
    setScore(s);
    try {
      localStorage.setItem('ie-quiz-score', JSON.stringify(s));
    } catch {
      /* ignore */
    }
  };
  const next = () => {
    setPicked(null);
    setNonce((x) => x + 1);
  };
  return (
    <Card
      title="Would you take this trade?"
      right={
        <>
          <span className="text-xs text-muted">
            Score {score.right}/{score.total}
          </span>
          <Segmented value={u} onChange={(v) => { setU(v); setPicked(null); }} options={UNDERLYING_OPTIONS} />
        </>
      }
    >
      {q.error && <ErrorBox error={q.error} onRetry={q.reload} />}
      {q.loading && !d && <Loading />}
      {d && (
        <div className="space-y-3">
          <CandleChart candles={shown} markers={markers} show={{ levels: false }} fitKey={`${d.day}-${picked ? 'all' : 'part'}`} height={280} />
          <p className="font-medium">{d.question}</p>
          <div className="flex flex-wrap gap-2">
            {d.choices.map((c) => (
              <button
                key={c.id}
                className={cls(
                  'btn',
                  picked && c.id === d.answer_id && 'border-up bg-up/20 text-up',
                  picked === c.id && c.id !== d.answer_id && 'border-down bg-down/20 text-down',
                )}
                disabled={!!picked}
                onClick={() => pick(c.id)}
              >
                {c.label}
              </button>
            ))}
          </div>
          {picked && (
            <div className={cls('rounded-lg border p-3 text-sm', picked === d.answer_id ? 'border-up/50 bg-up/10' : 'border-down/50 bg-down/10')}>
              <b>{picked === d.answer_id ? '✓ Correct.' : '✕ Not this time.'}</b> Best answer: {d.choices.find((c) => c.id === d.answer_id)?.label}.{' '}
              {d.explanation ?? 'The rest of the session is now revealed on the chart.'}
              <div className="mt-2">
                <button className="btn btn-primary" onClick={next}>Next question →</button>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Learn() {
  const [tab, setTab] = useState<'explain' | 'quiz'>('explain');
  const st = useApi(api.strategies);
  const [sel, setSel] = useState<string | null>(null);
  const s = st.data?.strategies.find((x) => x.id === sel) ?? st.data?.strategies[0];
  return (
    <div className="space-y-3">
      <PageHeader title="Learn" right={<Segmented value={tab} onChange={setTab} options={[{ value: 'explain', label: 'Strategy explainers' }, { value: 'quiz', label: 'Quiz' }]} />} />
      {tab === 'quiz' ? (
        <Quiz />
      ) : (
        <>
          {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
          {st.loading && !st.data && <Loading />}
          {st.data && (
            <div className="grid gap-3 md:grid-cols-[14rem_minmax(0,1fr)]">
              <div className="flex gap-1.5 overflow-x-auto md:flex-col md:overflow-visible">
                {st.data.strategies.map((x) => (
                  <button
                    key={x.id}
                    onClick={() => setSel(x.id)}
                    className={cls('shrink-0 rounded-lg border px-3 py-2 text-left text-sm', s?.id === x.id ? 'border-accent bg-accent/10 text-accent' : 'border-line bg-panel')}
                  >
                    {x.name}
                  </button>
                ))}
              </div>
              {s && (
                <Card>
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {(s.report?.badges ?? ['insufficient_data']).map((b) => <StrategyBadge key={b} badge={b} />)}
                    {!s.enabled && <Chip>disabled</Chip>}
                  </div>
                  <Markdown text={s.learn || `_No explainer available for ${s.name} yet._`} />
                </Card>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
