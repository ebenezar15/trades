// Typed REST client for the IndexEdge backend. All paths are relative (/api/...), so the same build works
// when served by the backend and via the Vite dev proxy. With VITE_MOCK=1 every call is answered by ./mock.
import type {
  AgentResponse,
  BrokerConnectResponse,
  BrokerSetupResponse,
  BrokerTestResponse,
  AnalyticsResponse,
  BacktestRequest,
  BacktestResponse,
  CandlesResponse,
  ContextSnapshot,
  EodReport,
  EventsResponse,
  HealthResponse,
  KillSwitchResponse,
  ModelResponse,
  NewsResponse,
  OkResponse,
  OptionChainResponse,
  OverviewResponse,
  PositionsResponse,
  PremarketResponse,
  QuizResponse,
  ReplayDaysResponse,
  ReplayResponse,
  SettingsPatch,
  SettingsResponse,
  SignalsResponse,
  StatusResponse,
  StrategiesResponse,
  StrategyUpdate,
  TradeDetail,
  TradeNote,
  TradesResponse,
  TradingMode,
} from './types';

export const MOCK = import.meta.env.VITE_MOCK === '1';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

type Method = 'GET' | 'POST' | 'PUT';
type Query = Record<string, string | number | boolean | null | undefined>;

function qs(q?: Query): string {
  if (!q) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
}

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  if (MOCK) {
    const { mockRequest } = await import('./mock');
    return mockRequest(method, path, body) as Promise<T>;
  }
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data ? String((data as { detail: unknown }).detail) : res.statusText;
    throw new ApiError(res.status, detail || `HTTP ${res.status}`, data);
  }
  return data as T;
}

const get = <T>(path: string, q?: Query) => request<T>('GET', path + qs(q));
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {});
const put = <T>(path: string, body: unknown) => request<T>('PUT', path, body);

export const api = {
  status: () => get<StatusResponse>('/api/status'),
  candles: (underlying: string, tf = '5m', limit = 500) =>
    get<CandlesResponse>('/api/candles', { underlying, tf, limit }),
  overview: () => get<OverviewResponse>('/api/overview'),
  context: () => get<ContextSnapshot>('/api/context'),

  signals: (status?: string, limit = 100) => get<SignalsResponse>('/api/signals', { status, limit }),
  confirmSignal: (id: string) => post<OkResponse>(`/api/signals/${encodeURIComponent(id)}/confirm`),
  skipSignal: (id: string) => post<OkResponse>(`/api/signals/${encodeURIComponent(id)}/skip`),

  positions: () => get<PositionsResponse>('/api/positions'),
  killSwitch: () => post<KillSwitchResponse>('/api/kill-switch'),

  optionChain: (underlying: string, expiry?: string) =>
    get<OptionChainResponse>('/api/option-chain', { underlying, expiry }),

  news: (limit = 50) => get<NewsResponse>('/api/news', { limit }),
  events: (days = 14) => get<EventsResponse>('/api/events', { days }),

  trades: (q: { start?: string; end?: string; strategy?: string } = {}) => get<TradesResponse>('/api/trades', q),
  trade: (id: string) => get<TradeDetail>(`/api/trades/${encodeURIComponent(id)}`),
  addNote: (id: string, note: TradeNote) => post<OkResponse>(`/api/trades/${encodeURIComponent(id)}/notes`, note),
  exportUrl: (fmt: 'csv' | 'xlsx') => `/api/export${qs({ fmt })}`,

  analytics: () => get<AnalyticsResponse>('/api/analytics'),

  strategies: () => get<StrategiesResponse>('/api/strategies'),
  revalidate: () => post<OkResponse>('/api/strategies/revalidate'),
  retrain: () => post<OkResponse>('/api/models/retrain'),
  updateStrategy: (id: string, update: StrategyUpdate) =>
    post<OkResponse>(`/api/strategies/${encodeURIComponent(id)}`, update),
  backtest: (req: BacktestRequest) => post<BacktestResponse>('/api/backtest', req),
  model: (strategyId: string) => get<ModelResponse>(`/api/models/${encodeURIComponent(strategyId)}`),

  replayDays: (underlying: string) => get<ReplayDaysResponse>('/api/replay/days', { underlying }),
  replay: (underlying: string, day: string) => get<ReplayResponse>('/api/replay', { underlying, day }),
  quiz: (underlying: string) => get<QuizResponse>('/api/learn/quiz', { underlying }),

  premarket: () => get<PremarketResponse>('/api/premarket'),
  eod: (day?: string) => get<EodReport>('/api/eod', { day }),

  settings: () => get<SettingsResponse>('/api/settings'),
  updateSettings: (patch: SettingsPatch) => put<OkResponse>('/api/settings', patch),
  setSecret: (name: string, value: string) => post<OkResponse>('/api/secrets', { name, value }),
  setMode: (mode: TradingMode, confirm_text = '') => post<OkResponse>('/api/mode', { mode, confirm_text }),
  acceptDisclaimer: () => post<OkResponse>('/api/disclaimer/accept'),
  login: () => post<OkResponse>('/api/login'),

  health: () => get<HealthResponse>('/api/health'),

  brokerSetup: () => get<BrokerSetupResponse>('/api/broker/setup'),
  brokerTest: () => post<BrokerTestResponse>('/api/broker/test'),
  brokerConnect: () => post<BrokerConnectResponse>('/api/broker/connect'),

  agent: () => get<AgentResponse>('/api/agent'),
  setAgent: (enabled: boolean) => post<OkResponse>('/api/agent', { enabled }),
  agentAutoAll: () => post<OkResponse>('/api/agent/auto-all'),
};

export type Api = typeof api;
