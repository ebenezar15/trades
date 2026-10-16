// Types for the IndexEdge REST / WebSocket API.
// Source of truth: docs/superpowers/plans/2026-10-16-indexedge.md — "REST / WebSocket API contract".
// Fields marked "(ext)" are optional extras the UI uses if present; the UI degrades gracefully without them.

export type Underlying = 'NIFTY' | 'BANKNIFTY' | string;
export type TradingMode = 'paper' | 'live';
export type ExecutionMode = 'signal_only' | 'confirm' | 'auto';
export type Regime = 'trend_up' | 'trend_down' | 'range' | 'high_vol' | 'unknown';
export type Side = 'BUY' | 'SELL';
export type SignalStatus = 'active' | 'pending_confirm' | 'blocked' | 'taken' | 'skipped' | 'expired' | string;
export type Impact = 'low' | 'medium' | 'high';

// ---------------------------------------------------------------- status
export interface StatusResponse {
  mode: TradingMode;
  demo_mode: boolean;
  market_open: boolean;
  broker: { connected: boolean; name: string; logged_in: boolean };
  static_ip: { ok: boolean | null; public_ip: string | null; expected: string; message: string };
  feed: { last_tick_age_s: number | null; ws: string };
  risk: {
    locked: boolean;
    lock_reason: string;
    daily_pnl: number;
    trades_today: number;
    max_trades: number;
    daily_loss_used_pct: number;
  };
  disclaimer_accepted: boolean;
  server_time: string;
  /** (ext) */
  data_source?: DataSource;
  /** per-underlying data source */
  data_sources?: Record<string, DataSource>;
  configured_mode?: TradingMode;
  execution_broker?: string;
  safe_mode?: { active: boolean; reason: string };
}

// ---------------------------------------------------------------- candles
export interface Candle {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
export interface LinePoint {
  time: number;
  value: number;
}
export interface SupertrendPoint extends LinePoint {
  direction: 1 | -1 | number;
}
export interface KeyLevels {
  pivot?: number | null;
  bc?: number | null;
  tc?: number | null;
  r1?: number | null;
  s1?: number | null;
  prev_high?: number | null;
  prev_low?: number | null;
  or_high?: number | null;
  or_low?: number | null;
}
/** Signal annotation on the candle chart (Iteration 2). */
export interface CandleMarker {
  time: number; // unix seconds
  position: 'aboveBar' | 'belowBar';
  shape: 'arrowUp' | 'arrowDown';
  color: string;
  text: string;
  signal_id: string;
  status: SignalStatus;
}
/** Open trade levels, in underlying price terms (Iteration 2). */
export interface TradeLine {
  trade_id: string;
  side: Side;
  entry: number;
  stop: number;
  targets: number[];
  status: string;
  label: string;
}
export interface CandlesResponse {
  candles: Candle[];
  overlays: {
    ema9: LinePoint[];
    ema21: LinePoint[];
    vwap: LinePoint[];
    supertrend: SupertrendPoint[];
  };
  levels: KeyLevels;
  markers?: CandleMarker[];
  trade_lines?: TradeLine[];
}

// ---------------------------------------------------------------- overview
export interface IndexOverview {
  underlying: Underlying;
  ltp: number;
  change_pct: number;
  regime: Regime;
  vix?: number | null;
  pcr?: number | null;
  max_pain?: number | null;
}
export interface OverviewResponse {
  indices: IndexOverview[];
}

// ---------------------------------------------------------------- context
export interface ContextInput {
  label: string;
  value: number | null;
  change_pct: number | null;
  age_s: number | null;
  stale: boolean;
  weight: number;
  required: boolean;
}
export interface ContextSnapshot {
  ts: string;
  macro_bias: number; // -1..1
  risk_off: boolean;
  event_risk: number; // 0..1
  usable: boolean;
  inputs: Record<string, ContextInput>;
  reasons: string[];
  stale_required: string[];
}

// ---------------------------------------------------------------- signals
export interface SignalInstrument {
  symbol: string;
  token?: string;
  name?: string;
  exchange?: string;
  kind?: string;
  lot_size?: number;
  expiry?: string | null;
  strike?: number | null;
  option_type?: 'CE' | 'PE' | null;
}
export interface SignalJSON {
  id: string;
  strategy_id: string;
  strategy_name: string;
  underlying: Underlying;
  side: Side;
  ts: string;
  entry: number;
  stop: number;
  targets: number[];
  probability: number | null;
  probability_ci: [number, number] | null;
  sample_size: number;
  expected_rr: number;
  ev_per_trade: number;
  quantity: number;
  instrument: SignalInstrument | null;
  badges: string[];
  reasons: string[];
  context_reasons: string[];
  news_reasons: string[];
  top_features: [string, number][];
  regime: Regime;
  status: SignalStatus;
  blocked_reason: string;
  /** (ext) historical expectancy in R for this strategy; used beside the high-win-rate badge */
  expectancy_r?: number | null;
  /** (ext) historical raw win rate for the strategy */
  win_rate?: number | null;
  /** (ext) ISO time after which a pending confirm expires */
  confirm_expires_at?: string | null;
  /** (ext) execution mode of the strategy that generated it */
  execution_mode?: ExecutionMode;
}
export interface SignalsResponse {
  signals: SignalJSON[];
}

// ---------------------------------------------------------------- positions
export interface Position {
  symbol: string;
  qty: number;
  avg_price: number;
  ltp: number;
  pnl: number;
}
export interface PositionsResponse {
  positions: Position[];
  open_trades: TradeRow[];
}
export interface KillSwitchResponse {
  ok: boolean;
  cancelled: number;
  squared_off: number;
}

// ---------------------------------------------------------------- option chain
export type Buildup = 'long_buildup' | 'short_buildup' | 'short_covering' | 'long_unwinding' | 'neutral' | string;
export interface OptionLeg {
  ltp: number | null;
  oi: number | null;
  oi_change: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  buildup: Buildup | null;
  volume?: number | null;
}
export interface OptionChainRow {
  strike: number;
  ce: OptionLeg | null;
  pe: OptionLeg | null;
}
export interface OptionChainResponse {
  spot: number;
  expiry: string;
  expiries: string[];
  pcr: number | null;
  max_pain: number | null;
  walls: { resistance: number[]; support: number[] };
  rows: OptionChainRow[];
}

// ---------------------------------------------------------------- news / events
export interface NewsItem {
  id: string;
  ts: string;
  source: string;
  title: string;
  url: string;
  sentiment: number; // -1..1
  relevance: number; // 0..1
  impact: Impact;
}
export interface NewsResponse {
  items: NewsItem[];
}
export interface MarketEvent {
  id: string;
  ts: string;
  title: string;
  category: string;
  impact: Impact;
}
export interface EventsResponse {
  events: MarketEvent[];
  blackout: { active: boolean; event: MarketEvent | null };
}

// ---------------------------------------------------------------- trades
export interface TradeRow {
  id: string;
  signal_id?: string | null;
  strategy_id: string;
  underlying: Underlying;
  instrument_symbol: string;
  side: Side;
  qty: number;
  entry_ts: string;
  entry_price: number;
  stop: number;
  targets: number[];
  mode: 'paper' | 'live' | 'backtest';
  status: 'open' | 'closed';
  exit_ts?: string | null;
  exit_price?: number | null;
  outcome?: string | null;
  pnl?: number | null;
  costs?: number | null;
  r_multiple?: number | null;
  mfe?: number | null;
  mae?: number | null;
  features?: Record<string, number> | null;
  reasons?: string[] | null;
  context?: Record<string, unknown> | null;
  ltp?: number | null; // (ext) for open trades
}
export interface TradesResponse {
  trades: TradeRow[];
}
export interface TradeNote {
  ts?: string;
  text: string;
  tags: string[];
  emotion: string;
}
export interface PostTradeAnalysis {
  entry_quality?: string;
  mfe_mae?: string;
  plan_vs_actual?: string;
  slippage?: string;
  rule_violations?: string[];
  quadrant?: string;
  summary?: string;
  [k: string]: unknown;
}
export interface TradeDetail extends TradeRow {
  analysis: PostTradeAnalysis | null;
  notes?: TradeNote[];
}

// ---------------------------------------------------------------- analytics
export interface BreakdownRow {
  key?: string;
  n: number;
  win_rate: number;
  pnl: number;
  expectancy_r?: number;
  profit_factor?: number | null;
  [k: string]: unknown;
}
/** Breakdowns may arrive as an array of rows or as a mapping key -> row; the UI normalises both. */
export type Breakdown = BreakdownRow[] | Record<string, Omit<BreakdownRow, 'key'>>;
export interface AnalyticsResponse {
  equity: LinePoint[];
  drawdown: LinePoint[];
  n?: number;
  win_rate?: number;
  profit_factor?: number | null;
  expectancy?: number;
  expectancy_r?: number;
  avg_r?: number;
  max_drawdown?: number;
  total_pnl?: number;
  discipline_score?: number | null;
  by_strategy?: Breakdown;
  by_hour?: Breakdown;
  by_weekday?: Breakdown;
  by_regime?: Breakdown;
  by_underlying?: Breakdown;
  [k: string]: unknown;
}

// ---------------------------------------------------------------- strategies / backtest / models
export interface StrategyMetrics {
  n?: number;
  win_rate?: number;
  win_ci?: [number, number];
  avg_r?: number;
  expectancy_r?: number;
  expectancy_rs?: number;
  profit_factor?: number | null;
  max_dd_r?: number;
  sharpe?: number | null;
  by_year?: Record<string, unknown>;
  [k: string]: unknown;
}
export type ReportStatus = 'validated' | 'high_win_rate' | 'insufficient' | 'rejected';
export interface ReportCard {
  status: ReportStatus;
  badges: string[];
  metrics: StrategyMetrics;
  warnings: string[];
}
export type ParamValue = number | string | boolean;
export interface StrategyInfo {
  id: string;
  name: string;
  description: string;
  timeframe: string;
  regimes: Regime[] | null;
  enabled: boolean;
  execution_mode: ExecutionMode;
  params: Record<string, ParamValue>;
  report: ReportCard | null;
  learn: string;
}
export interface StrategiesResponse {
  strategies: StrategyInfo[];
  /** (ext) when/where the report cards were computed */
  reports_meta?: { ts?: string; days?: number; data_source?: string } | null;
  /** (ext) background validation job status */
  reports_job?: { status?: string; started?: string; finished?: string; error?: string } | null;
}
export interface StrategyUpdate {
  enabled?: boolean;
  execution_mode?: ExecutionMode;
  params?: Record<string, ParamValue>;
}
export interface BacktestRequest {
  strategy_id: string;
  underlying: Underlying;
  days: number;
}
export interface MonteCarlo {
  dd_p50?: number;
  dd_p95?: number;
  [k: string]: unknown;
}
export interface WalkForward {
  folds?: Array<Record<string, unknown>>;
  oos?: StrategyMetrics;
  stability?: number | Record<string, unknown>;
  warnings?: string[];
  [k: string]: unknown;
}
export interface BacktestResponse {
  metrics: StrategyMetrics;
  report: ReportCard;
  trades: Array<Record<string, unknown>>;
  equity: LinePoint[];
  monte_carlo: MonteCarlo;
  walk_forward: WalkForward;
}
export interface ReliabilityBin {
  p_pred: number;
  p_obs: number;
  n: number;
}
export interface ModelResponse {
  metrics: { auc?: number; brier?: number; n?: number; [k: string]: unknown } | null;
  reliability: ReliabilityBin[];
}

// ---------------------------------------------------------------- replay / learn
export interface ReplaySignal extends Partial<SignalJSON> {
  index: number; // bar index at which the signal became visible
  strategy_id: string;
  side: Side;
  entry: number;
  stop: number;
  targets: number[];
}
export interface ReplayOutcome {
  signal_id?: string;
  index?: number;
  outcome: string;
  r?: number;
  exit_index?: number;
  [k: string]: unknown;
}
export interface ReplayDaysResponse {
  days: string[];
}
export interface ReplayResponse {
  candles: Candle[];
  signals: ReplaySignal[];
  outcomes: ReplayOutcome[] | Record<string, ReplayOutcome>;
}
export interface QuizChoice {
  id: string;
  label: string;
}
export interface QuizResponse {
  day: string;
  upto_index: number;
  candles: Candle[];
  question: string;
  choices: QuizChoice[];
  answer_id: string;
  explanation?: string; // (ext)
}

// ---------------------------------------------------------------- premarket / eod
export interface PremarketResponse {
  date: string;
  context: ContextSnapshot | null;
  gap_expectation: { underlying?: string; points?: number; pct?: number; text?: string } | string | null;
  key_levels: Record<string, KeyLevels>;
  events_today: MarketEvent[];
  enabled_strategies: Array<{ id: string; name: string; suits_regime?: boolean } | string>;
  regime: Record<string, Regime> | Regime;
}
export type EodReport = Record<string, unknown> & {
  day?: string;
  pnl?: number;
  trades?: number;
  wins?: number;
  losses?: number;
  summary?: string;
  lessons?: string[];
};

// ---------------------------------------------------------------- settings
export interface RiskSettings {
  capital: number;
  risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_trades_per_day: number;
  max_consecutive_losses: number;
  max_open_positions: number;
  max_lots_per_index: Record<string, number>;
  no_trade_first_min: number;
  no_trade_last_min: number;
  cooldown_after_loss_min: number;
  revenge_size_multiplier: number;
  stale_feed_seconds: number;
  lock_edits_during_market: boolean;
}
export interface NotifySettings {
  telegram_enabled: boolean;
  telegram_chat_id: string;
  notify_signals: boolean;
  notify_high_impact_news: boolean;
  notify_eod: boolean;
}
export interface NewsSource {
  name: string;
  url: string;
  kind: string;
  enabled: boolean;
}
export interface ContextInputSetting {
  symbol: string;
  label: string;
  weight: number;
  required: boolean;
  max_age_min: number;
}
export interface SettingsResponse {
  app: { demo_mode: boolean; disclaimer_accepted: boolean; host?: string; port?: number; [k: string]: unknown };
  broker: { static_ip: string; login_time: string; intraday_cutoff: string; ip_check_url?: string; [k: string]: unknown };
  risk: RiskSettings;
  execution: {
    mode: TradingMode;
    default_execution_mode: ExecutionMode;
    limit_buffer_ticks: number;
    preferred_instrument: string;
    option_moneyness: string;
    [k: string]: unknown;
  };
  strategies?: Record<string, unknown>;
  context: { refresh_seconds: number; risk_off_threshold: number; vix_risk_off_level: number; inputs: Record<string, ContextInputSetting> };
  news: { poll_seconds: number; sources: NewsSource[]; high_impact_pause: boolean; [k: string]: unknown };
  notify: NotifySettings;
  secrets: Record<string, boolean>;
  can_edit_limits: boolean;
  /** (ext) one-time pairing link for phones on the home Wi-Fi; only returned to the PC itself */
  phone_url?: string;
  [k: string]: unknown;
}
export type SettingsPatch = Partial<{
  [K in keyof SettingsResponse]: SettingsResponse[K] extends object ? Partial<SettingsResponse[K]> : SettingsResponse[K];
}>;

// ---------------------------------------------------------------- health
export interface HealthJob {
  id?: string;
  name: string;
  next_run?: string | null;
  last_run?: string | null;
  ok?: boolean;
  [k: string]: unknown;
}
export interface HealthResponse {
  ws_status: string;
  last_tick_age_s: number | null;
  latency_ms: number | null;
  broker_errors: number;
  order_rate: number;
  jobs: HealthJob[];
  log_tail: string[] | string;
}

export interface OkResponse {
  ok: boolean;
  message?: string;
  [k: string]: unknown;
}

// ---------------------------------------------------------------- broker setup (Iteration 2)
/** angel_one = real-time · yahoo = delayed ~1–2 min, no index volume · synthetic = demo data */
export type DataSource = 'angel_one' | 'yahoo' | 'synthetic' | string;
export interface SmartApiField {
  field: string;
  value: string;
  note: string;
}
export interface BrokerSetupStep {
  id: string;
  title: string;
  done: boolean;
}
export interface BrokerSetupResponse {
  connected: boolean;
  configured: boolean;
  data_source: DataSource;
  public_ip: string | null;
  static_ip: string | null;
  smartapi_fields: SmartApiField[];
  secrets: Record<string, boolean>;
  steps: BrokerSetupStep[];
}
export interface BrokerTestStep {
  name: string;
  ok: boolean;
  detail: string;
}
export interface BrokerTestResponse {
  ok: boolean;
  steps: BrokerTestStep[];
}
export interface BrokerConnectResponse {
  ok: boolean;
  message: string;
  steps?: BrokerTestStep[];
}

// ---------------------------------------------------------------- agent (Iteration 2)
export type AgentWatchState = 'signal' | 'no_setup' | 'blocked' | 'disabled' | string;
export interface AgentWatch {
  strategy_id: string;
  name: string;
  underlying: Underlying;
  enabled: boolean;
  execution_mode: ExecutionMode;
  last_eval_ts: string | null;
  state: AgentWatchState;
  detail: string;
}
export type AgentLogLevel = 'info' | 'warning' | 'error' | 'trade' | string;
export interface AgentLogEntry {
  ts: string;
  level: AgentLogLevel;
  kind: string;
  message: string;
}
export interface AgentResponse {
  enabled: boolean;
  auto_strategies: string[];
  data_source: DataSource;
  market_open: boolean;
  next_bar_in_s: number | null;
  watching: AgentWatch[];
  log: AgentLogEntry[];
}

// ---------------------------------------------------------------- websocket
export type WsType = 'tick' | 'candle' | 'signal' | 'context' | 'news' | 'order' | 'trade' | 'health' | 'alert' | 'agent';
export interface TickData {
  underlying?: string;
  symbol?: string;
  token?: string;
  ltp: number;
  ts?: string;
}
export interface CandleData extends Candle {
  underlying: string;
  tf?: string;
}
export interface AlertData {
  level?: 'info' | 'warning' | 'error' | 'critical';
  title?: string;
  message: string;
}
export type WsMessage =
  | { type: 'tick'; data: TickData }
  | { type: 'candle'; data: CandleData }
  | { type: 'signal'; data: SignalJSON }
  | { type: 'context'; data: ContextSnapshot }
  | { type: 'news'; data: NewsItem }
  | { type: 'order'; data: Record<string, unknown> }
  | { type: 'trade'; data: TradeRow }
  | { type: 'health'; data: Partial<HealthResponse> }
  | { type: 'alert'; data: AlertData }
  | { type: 'agent'; data: AgentLogEntry };
