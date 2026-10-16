// Mock backend used when VITE_MOCK=1. Returns realistic, deterministic fake data for every endpoint so the UI can
// be developed and screenshotted without the Python backend. Never bundled into normal builds (dynamic import
// behind the MOCK flag, which Vite statically replaces).
import type {
  AnalyticsResponse,
  BacktestResponse,
  Candle,
  CandlesResponse,
  ContextSnapshot,
  EodReport,
  EventsResponse,
  HealthResponse,
  LinePoint,
  NewsItem,
  OptionChainResponse,
  OptionLeg,
  PremarketResponse,
  QuizResponse,
  Regime,
  ReplayResponse,
  SettingsResponse,
  SignalJSON,
  StatusResponse,
  StrategyInfo,
  SupertrendPoint,
  TradeDetail,
  TradeNote,
  TradeRow,
  WsMessage,
} from './types';

// ---------------------------------------------------------------- deterministic RNG
function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return ((s >>> 0) % 1_000_000) / 1_000_000;
  };
}
function gauss(r: () => number) {
  const u = Math.max(r(), 1e-9);
  const v = r();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;
const IST = 19800;

// ---------------------------------------------------------------- time helpers
const now = () => Date.now();
function istParts(ms: number) {
  const d = new Date(ms + IST * 1000);
  return { y: d.getUTCFullYear(), m: d.getUTCMonth(), d: d.getUTCDate(), wd: d.getUTCDay(), h: d.getUTCHours(), mi: d.getUTCMinutes() };
}
/** unix seconds of 09:15 IST on the IST calendar day containing ms */
function sessionStart(ms: number) {
  const p = istParts(ms);
  return Date.UTC(p.y, p.m, p.d, 9, 15) / 1000 - IST;
}
function isoIST(unixS: number) {
  const d = new Date((unixS + IST) * 1000);
  return d.toISOString().replace('Z', '+05:30').replace(/\.\d{3}/, '');
}
function dayStr(unixS: number) {
  return new Date((unixS + IST) * 1000).toISOString().slice(0, 10);
}
/** previous `n` weekdays (IST), oldest first, ending with today if it is a weekday */
function weekdays(n: number): number[] {
  const out: number[] = [];
  let t = sessionStart(now());
  while (out.length < n) {
    const wd = istParts(t * 1000).wd;
    if (wd !== 0 && wd !== 6) out.unshift(t);
    t -= 86400;
  }
  return out;
}

// ---------------------------------------------------------------- candles
const BASE: Record<string, number> = { NIFTY: 25180, BANKNIFTY: 56420 };
const BARS_PER_DAY = 75; // 09:15..15:30 at 5m

function genDay(underlying: string, start: number, open: number, seed: number, bars = BARS_PER_DAY): Candle[] {
  const r = rng(seed);
  const vol = underlying === 'BANKNIFTY' ? 0.0011 : 0.0009;
  const drift = (r() - 0.5) * 0.0004;
  let px = open;
  const out: Candle[] = [];
  for (let i = 0; i < bars; i++) {
    const o = px;
    const c = o * (1 + drift + vol * gauss(r));
    const h = Math.max(o, c) * (1 + Math.abs(gauss(r)) * vol * 0.4);
    const l = Math.min(o, c) * (1 - Math.abs(gauss(r)) * vol * 0.4);
    const u = Math.abs(i - 37) / 37; // U-shaped volume
    const v = Math.round((80_000 + 220_000 * u * u) * (0.7 + r() * 0.6));
    out.push({ time: start + i * 300, open: round(o), high: round(h), low: round(l), close: round(c), volume: v });
    px = c;
  }
  return out;
}

const candleCache: Record<string, Candle[]> = {};
function seriesFor(underlying: string): Candle[] {
  if (candleCache[underlying]) return candleCache[underlying];
  const days = weekdays(4);
  let px = BASE[underlying] ?? 25000;
  const all: Candle[] = [];
  const nowS = now() / 1000;
  days.forEach((d, k) => {
    const isToday = k === days.length - 1;
    let bars = BARS_PER_DAY;
    if (isToday) {
      const elapsed = Math.floor((nowS - d) / 300) + 1;
      // outside market hours show a full (or a demo half) session so charts are never empty
      bars = elapsed <= 6 ? 42 : Math.min(BARS_PER_DAY, elapsed);
    }
    const day = genDay(underlying, d, px * (1 + (k ? 0.002 * gauss(rng(d)) : 0)), d + underlying.length, bars);
    all.push(...day);
    px = day[day.length - 1].close;
  });
  candleCache[underlying] = all;
  return all;
}

function ema(vals: number[], n: number): number[] {
  const k = 2 / (n + 1);
  const out: number[] = [];
  vals.forEach((v, i) => out.push(i === 0 ? v : v * k + out[i - 1] * (1 - k)));
  return out;
}
function overlays(c: Candle[]) {
  const closes = c.map((x) => x.close);
  const e9 = ema(closes, 9);
  const e21 = ema(closes, 21);
  const vwap: LinePoint[] = [];
  let pv = 0,
    vv = 0,
    curDay = '';
  c.forEach((x) => {
    const d = dayStr(x.time);
    if (d !== curDay) {
      pv = 0;
      vv = 0;
      curDay = d;
    }
    const tp = (x.high + x.low + x.close) / 3;
    pv += tp * x.volume;
    vv += x.volume;
    vwap.push({ time: x.time, value: round(pv / vv) });
  });
  // supertrend (10, 3)
  const st: SupertrendPoint[] = [];
  let atr = 0;
  let upper = 0,
    lower = 0,
    dir = 1;
  c.forEach((x, i) => {
    const prevClose = i ? c[i - 1].close : x.close;
    const tr = Math.max(x.high - x.low, Math.abs(x.high - prevClose), Math.abs(x.low - prevClose));
    atr = i === 0 ? tr : (atr * 9 + tr) / 10;
    const mid = (x.high + x.low) / 2;
    const bu = mid + 3 * atr;
    const bl = mid - 3 * atr;
    upper = i === 0 || bu < upper || prevClose > upper ? bu : upper;
    lower = i === 0 || bl > lower || prevClose < lower ? bl : lower;
    if (x.close > upper) dir = 1;
    else if (x.close < lower) dir = -1;
    if (i >= 10) st.push({ time: x.time, value: round(dir === 1 ? lower : upper), direction: dir });
  });
  return {
    ema9: c.map((x, i) => ({ time: x.time, value: round(e9[i]) })).slice(8),
    ema21: c.map((x, i) => ({ time: x.time, value: round(e21[i]) })).slice(20),
    vwap,
    supertrend: st,
  };
}
function levels(c: Candle[]) {
  const today = dayStr(c[c.length - 1].time);
  const prev = c.filter((x) => dayStr(x.time) < today);
  const lastPrevDay = dayStr(prev[prev.length - 1].time);
  const pd = prev.filter((x) => dayStr(x.time) === lastPrevDay);
  const ph = Math.max(...pd.map((x) => x.high));
  const pl = Math.min(...pd.map((x) => x.low));
  const pc = pd[pd.length - 1].close;
  const pivot = (ph + pl + pc) / 3;
  const bc = (ph + pl) / 2;
  const tc = 2 * pivot - bc;
  const td = c.filter((x) => dayStr(x.time) === today).slice(0, 3);
  return {
    pivot: round(pivot),
    bc: round(Math.min(bc, tc)),
    tc: round(Math.max(bc, tc)),
    r1: round(2 * pivot - pl),
    s1: round(2 * pivot - ph),
    prev_high: ph,
    prev_low: pl,
    or_high: td.length === 3 ? Math.max(...td.map((x) => x.high)) : null,
    or_low: td.length === 3 ? Math.min(...td.map((x) => x.low)) : null,
  };
}
function candlesResp(underlying: string, limit: number): CandlesResponse {
  const all = seriesFor(underlying);
  const c = all.slice(-limit);
  const ov = overlays(all);
  const t0 = c[0].time;
  const cut = <T extends { time: number }>(xs: T[]) => xs.filter((x) => x.time >= t0);
  return {
    candles: c,
    overlays: { ema9: cut(ov.ema9), ema21: cut(ov.ema21), vwap: cut(ov.vwap), supertrend: cut(ov.supertrend) },
    levels: levels(all),
  };
}
const ltp = (u: string) => {
  const s = seriesFor(u);
  return s[s.length - 1].close;
};
const prevClose = (u: string) => {
  const s = seriesFor(u);
  const today = dayStr(s[s.length - 1].time);
  const prev = s.filter((x) => dayStr(x.time) < today);
  return prev[prev.length - 1].close;
};

// ---------------------------------------------------------------- mutable state
const state = {
  mode: 'paper' as 'paper' | 'live',
  disclaimer: (() => {
    try {
      return localStorage.getItem('ie-mock-disclaimer') === '1';
    } catch {
      return false;
    }
  })(),
  locked: false,
  lockReason: '',
  secrets: { ANGEL_API_KEY: true, ANGEL_CLIENT_CODE: true, ANGEL_PIN: false, ANGEL_TOTP_SECRET: false, TELEGRAM_BOT_TOKEN: false } as Record<string, boolean>,
  notes: {} as Record<string, TradeNote[]>,
  strategyOverrides: {} as Record<string, Partial<StrategyInfo>>,
  signalStatus: {} as Record<string, string>,
};

// ---------------------------------------------------------------- strategies
const LEARN: Record<string, string> = {
  orb15: `## Opening Range Breakout (15 min)

The **opening range** is the high and low of the first 15 minutes (09:15–09:30). A close outside the range with
above-average volume often starts the day's trend.

**Rules**
1. Wait for the 09:30 bar to close; mark OR high / OR low.
2. Long on a 5-minute close above OR high with relative volume > 1.2; short below OR low.
3. Stop: opposite side of the range or 1×ATR, whichever is tighter.
4. Targets: 1R and 2R; exit everything by 15:00.

**When it fails:** range days (low ADX), event days, and gaps that already exhausted the move.

> Measured, not claimed: the win rate and expectancy you see come from walk-forward backtests on your data.`,
  vwap_pullback: `## VWAP Pullback

In a trending session price tends to *respect* VWAP. Buy the first pullback to VWAP in an uptrend (price above
VWAP, EMA9 > EMA21), sell the rally to VWAP in a downtrend.

- Entry: rejection candle at VWAP (pin bar / engulfing)
- Stop: below the pullback low (above the high for shorts)
- Target: prior swing / 2R`,
  ema_supertrend_adx: `## EMA + Supertrend + ADX

Trend-following combo: **EMA9 > EMA21**, **Supertrend green**, **ADX > 20** ⇒ long bias; mirror for shorts.
Enter on the close of the bar that completes all three conditions. Trail with Supertrend.`,
  cpr_pivot: `## CPR / Pivot bounce

The *Central Pivot Range* (BC–Pivot–TC) from yesterday's H/L/C acts as today's balance zone.
A **narrow CPR** hints at a trending day; a **wide CPR** at a range day. Trade bounces off CPR / R1 / S1
with confirmation.`,
  squeeze_breakout: `## Volatility Squeeze Breakout

When Bollinger Bands contract *inside* the Keltner Channel, volatility is compressed. The first close outside the
bands after the squeeze releases is traded in the breakout direction.`,
  rsi_divergence: `## RSI Divergence

Price makes a lower low while RSI makes a higher low ⇒ **bullish divergence** (mirror for bearish). Confirmed only
after the swing is complete (no look-ahead), so entries are later than they look on a chart.`,
  oi_pcr: `## OI / PCR sentiment

Uses option-chain open interest. Heavy put writing (rising PCR) below price = support; heavy call writing above =
resistance. Signals when price breaks the strongest OI wall with short covering.`,
  expiry_straddle: `## Expiry-day short straddle (disabled by default)

Sells ATM CE + PE on expiry morning to collect theta. **High win rate, but rare large losses** — this is exactly the
kind of strategy where the 🏅 badge must be read next to expectancy.`,
  gap_fill: `## Gap fill

Moderate gaps (0.3–0.8%) without news often retrace toward the previous close. Fade the gap after the first
15-minute candle fails to extend.`,
};
const STRATS: Array<[string, string, string, Regime[] | null, Record<string, number | string | boolean>]> = [
  ['orb15', 'Opening Range Breakout 15m', 'Breakout of the first 15-minute range with volume confirmation.', ['trend_up', 'trend_down'], { or_minutes: 15, min_rvol: 1.2, target_r: 2 }],
  ['vwap_pullback', 'VWAP Pullback', 'Pullback to session VWAP in the direction of the trend.', ['trend_up', 'trend_down'], { ema_fast: 9, ema_slow: 21, max_dist_atr: 0.3 }],
  ['ema_supertrend_adx', 'EMA + Supertrend + ADX', 'Trend filter combo with ADX strength.', ['trend_up', 'trend_down'], { adx_min: 20, st_period: 10, st_mult: 3 }],
  ['cpr_pivot', 'CPR / Pivot Bounce', 'Bounce off central pivot range and R1/S1.', ['range'], { touch_tol_pct: 0.05, confirm: true }],
  ['squeeze_breakout', 'Volatility Squeeze Breakout', 'Bollinger inside Keltner, trade the release.', null, { bb_n: 20, bb_k: 2, kc_mult: 1.5 }],
  ['rsi_divergence', 'RSI Divergence', 'Swing-confirmed RSI divergence reversal.', ['range', 'high_vol'], { rsi_n: 14, lookback: 30 }],
  ['oi_pcr', 'OI / PCR Sentiment', 'Option-chain OI walls and PCR shifts.', null, { pcr_hi: 1.3, pcr_lo: 0.7 }],
  ['expiry_straddle', 'Expiry Short Straddle', 'Expiry-day ATM straddle sell (theta).', ['range'], { entry_time: '09:45', sl_pct: 25 }],
  ['gap_fill', 'Gap Fill', 'Fade moderate no-news gaps.', ['range'], { min_gap_pct: 0.3, max_gap_pct: 0.8 }],
];
function report(id: string): StrategyInfo['report'] {
  const r = rng(id.length * 97 + id.charCodeAt(0));
  if (id === 'oi_pcr') return { status: 'insufficient', badges: ['insufficient_data'], metrics: { n: 31, win_rate: 0.55 }, warnings: ['Only 31 trades — need ≥ 50'] };
  if (id === 'expiry_straddle')
    return {
      status: 'high_win_rate',
      badges: ['high_win_rate', 'negative_expectancy_warning'],
      metrics: { n: 142, win_rate: 0.84, win_ci: [0.77, 0.89], avg_r: -0.04, expectancy_r: -0.04, expectancy_rs: -112, profit_factor: 0.93, max_dd_r: 14.2, sharpe: -0.2 },
      warnings: ['Expectancy ≤ 0 after costs despite 84% win rate', 'Tail losses dominate'],
    };
  if (id === 'gap_fill')
    return { status: 'rejected', badges: ['rejected'], metrics: { n: 118, win_rate: 0.47, win_ci: [0.38, 0.56], avg_r: -0.08, expectancy_r: -0.08, profit_factor: 0.88, max_dd_r: 19.1 }, warnings: ['Profit factor < 1.3', 'Max DD > 15R'] };
  const n = 120 + Math.floor(r() * 300);
  const wr = 0.42 + r() * 0.2;
  const exp = round(0.05 + r() * 0.25, 2);
  return {
    status: 'validated',
    badges: id === 'vwap_pullback' ? ['validated', 'high_win_rate'] : ['validated'],
    metrics: {
      n,
      win_rate: id === 'vwap_pullback' ? 0.81 : round(wr, 3),
      win_ci: id === 'vwap_pullback' ? [0.75, 0.86] : [round(wr - 0.06, 2), round(wr + 0.06, 2)],
      avg_r: exp,
      expectancy_r: exp,
      expectancy_rs: Math.round(exp * 2000),
      profit_factor: round(1.3 + r() * 0.6, 2),
      max_dd_r: round(6 + r() * 7, 1),
      sharpe: round(0.8 + r(), 2),
    },
    warnings: [],
  };
}
function strategies(): StrategyInfo[] {
  return STRATS.map(([id, name, description, regimes, params]) => ({
    id,
    name,
    description,
    timeframe: '5m',
    regimes,
    enabled: !['expiry_straddle', 'gap_fill', 'oi_pcr'].includes(id),
    execution_mode: id === 'orb15' ? 'confirm' : 'signal_only',
    params,
    report: report(id),
    learn: LEARN[id] ?? '',
    ...state.strategyOverrides[id],
  }));
}

// ---------------------------------------------------------------- signals
function makeSignals(): SignalJSON[] {
  const n = ltp('NIFTY');
  const b = ltp('BANKNIFTY');
  const t = Math.floor(now() / 1000);
  const base: SignalJSON[] = [
    {
      id: 'sig-001', strategy_id: 'vwap_pullback', strategy_name: 'VWAP Pullback', underlying: 'NIFTY', side: 'BUY',
      ts: isoIST(t - 120), entry: round(n), stop: round(n - 38), targets: [round(n + 40), round(n + 78)],
      probability: 0.63, probability_ci: [0.56, 0.69], sample_size: 214, expected_rr: 1.6, ev_per_trade: 1840,
      quantity: 75, instrument: { symbol: 'NIFTY20OCT2625200CE', kind: 'option', lot_size: 75, strike: 25200, option_type: 'CE', expiry: '2026-10-20' },
      badges: ['validated', 'high_win_rate'], expectancy_r: 0.21, win_rate: 0.81,
      reasons: ['Price above VWAP and EMA9 > EMA21', 'Bullish pin bar at VWAP', 'Relative volume 1.4×'],
      context_reasons: ['S&P 500 fut +0.6% (bullish)', 'India VIX 13.2 (calm)', 'USD/INR -0.1% (mildly bullish)'],
      news_reasons: ['RBI keeps repo unchanged — neutral/positive for banks'],
      top_features: [['dist_vwap_atr', 0.18], ['ema_slope_21', 0.12], ['rvol', 0.09], ['adx', 0.05], ['context_bias', 0.04]],
      regime: 'trend_up', status: 'pending_confirm', blocked_reason: '', execution_mode: 'confirm',
      confirm_expires_at: isoIST(t + 55),
    },
    {
      id: 'sig-002', strategy_id: 'expiry_straddle', strategy_name: 'Expiry Short Straddle', underlying: 'BANKNIFTY', side: 'SELL',
      ts: isoIST(t - 900), entry: round(b), stop: round(b + 160), targets: [round(b - 90)],
      probability: 0.84, probability_ci: [0.77, 0.89], sample_size: 142, expected_rr: 0.56, ev_per_trade: -112,
      quantity: 30, instrument: { symbol: 'BANKNIFTY28OCT2656400PE', kind: 'option', lot_size: 30, strike: 56400, option_type: 'PE' },
      badges: ['high_win_rate', 'negative_expectancy_warning'], expectancy_r: -0.04, win_rate: 0.84,
      reasons: ['Expiry day, IV rank 62', 'Price inside CPR'],
      context_reasons: ['Macro bias neutral (+0.05)'], news_reasons: [],
      top_features: [['iv_rank', 0.11], ['cpr_width', -0.07], ['minutes_to_close', 0.05]],
      regime: 'range', status: 'active', blocked_reason: '',
    },
    {
      id: 'sig-003', strategy_id: 'squeeze_breakout', strategy_name: 'Volatility Squeeze Breakout', underlying: 'BANKNIFTY', side: 'BUY',
      ts: isoIST(t - 1500), entry: round(b - 40), stop: round(b - 140), targets: [round(b + 60), round(b + 160)],
      probability: null, probability_ci: null, sample_size: 37, expected_rr: 2.0, ev_per_trade: 0,
      quantity: 30, instrument: { symbol: 'BANKNIFTY28OCT2656400CE', kind: 'option', lot_size: 30 },
      badges: ['insufficient_data'],
      reasons: ['Bollinger inside Keltner for 9 bars', 'Close above upper band'],
      context_reasons: ['S&P 500 fut +0.6% (bullish)'], news_reasons: [],
      top_features: [], regime: 'high_vol', status: 'active', blocked_reason: '',
    },
    {
      id: 'sig-004', strategy_id: 'orb15', strategy_name: 'Opening Range Breakout 15m', underlying: 'NIFTY', side: 'SELL',
      ts: isoIST(t - 3600), entry: round(n + 20), stop: round(n + 60), targets: [round(n - 20), round(n - 60)],
      probability: 0.48, probability_ci: [0.42, 0.55], sample_size: 268, expected_rr: 2.0, ev_per_trade: 690,
      quantity: 75, instrument: { symbol: 'NIFTY20OCT2625150PE', kind: 'option', lot_size: 75 },
      badges: ['validated'], expectancy_r: 0.12, win_rate: 0.48,
      reasons: ['Close below OR low', 'Relative volume 1.3×'],
      context_reasons: ['External context stale: GIFT Nifty 42 min old'], news_reasons: ['High-impact: US CPI at 18:00 IST'],
      top_features: [['or_width_atr', -0.06], ['rvol', 0.05]],
      regime: 'trend_down', status: 'blocked', blocked_reason: 'News blackout: US CPI in 12 min',
    },
    {
      id: 'sig-005', strategy_id: 'ema_supertrend_adx', strategy_name: 'EMA + Supertrend + ADX', underlying: 'NIFTY', side: 'BUY',
      ts: isoIST(t - 7200), entry: round(n - 60), stop: round(n - 100), targets: [round(n - 20), round(n + 20)],
      probability: 0.55, probability_ci: [0.49, 0.61], sample_size: 301, expected_rr: 1.8, ev_per_trade: 1120,
      quantity: 75, instrument: { symbol: 'NIFTY20OCT2625100CE', kind: 'option', lot_size: 75 },
      badges: ['validated'], expectancy_r: 0.17, win_rate: 0.55,
      reasons: ['EMA9 crossed above EMA21', 'Supertrend flipped green', 'ADX 24'],
      context_reasons: ['Nikkei +0.9% (bullish)'], news_reasons: [],
      top_features: [['adx', 0.1], ['st_dir', 0.08]], regime: 'trend_up', status: 'taken', blocked_reason: '',
    },
    {
      id: 'sig-006', strategy_id: 'cpr_pivot', strategy_name: 'CPR / Pivot Bounce', underlying: 'BANKNIFTY', side: 'SELL',
      ts: isoIST(t - 9000), entry: round(b + 120), stop: round(b + 200), targets: [round(b)],
      probability: 0.52, probability_ci: [0.45, 0.59], sample_size: 188, expected_rr: 1.5, ev_per_trade: 240,
      quantity: 30, instrument: { symbol: 'BANKNIFTY28OCT2656500PE', kind: 'option', lot_size: 30 },
      badges: ['validated'], expectancy_r: 0.06, win_rate: 0.52,
      reasons: ['Rejection at R1'], context_reasons: [], news_reasons: [], top_features: [],
      regime: 'range', status: 'skipped', blocked_reason: '',
    },
  ];
  return base.map((s) => ({ ...s, status: state.signalStatus[s.id] ?? s.status }));
}

// ---------------------------------------------------------------- trades
function makeTrades(): TradeRow[] {
  const r = rng(42);
  const ids = STRATS.map((s) => s[0]).filter((x) => x !== 'gap_fill');
  const out: TradeRow[] = [];
  const days = weekdays(40);
  let k = 0;
  days.slice(0, -1).forEach((d) => {
    const nTr = Math.floor(r() * 3);
    for (let j = 0; j < nTr; j++) {
      const u = r() > 0.5 ? 'NIFTY' : 'BANKNIFTY';
      const sid = ids[Math.floor(r() * ids.length)];
      const side = r() > 0.5 ? 'BUY' : 'SELL';
      const entryTs = d + 900 + Math.floor(r() * 60) * 300;
      const rr = r() < 0.52 ? 0.6 + r() * 1.6 : -(0.5 + r() * 0.55);
      const lot = u === 'NIFTY' ? 75 : 30;
      const qty = lot * (1 + Math.floor(r() * 2));
      const prem = round(80 + r() * 140);
      const riskPts = round(prem * 0.25);
      const exit = round(prem + rr * riskPts);
      const costs = round(60 + r() * 40);
      const pnl = round((exit - prem) * qty - costs);
      out.push({
        id: `T${String(++k).padStart(4, '0')}`, signal_id: `S${k}`, strategy_id: sid, underlying: u,
        instrument_symbol: `${u}${side === 'BUY' ? 'CE' : 'PE'}`, side: 'BUY', qty, entry_ts: isoIST(entryTs),
        entry_price: prem, stop: round(prem - riskPts), targets: [round(prem + riskPts), round(prem + 2 * riskPts)],
        mode: 'paper', status: 'closed', exit_ts: isoIST(entryTs + 300 * (2 + Math.floor(r() * 20))), exit_price: exit,
        outcome: rr > 0 ? (rr > 1.5 ? 'target2' : 'target1') : 'stop', pnl, costs, r_multiple: round(rr),
        mfe: round(Math.max(rr, 0) + r() * 0.6), mae: round(Math.min(rr, 0) - r() * 0.3),
        reasons: ['Setup conditions met'], features: { adx: round(15 + r() * 20), rvol: round(0.8 + r()) },
      });
    }
  });
  const today = weekdays(1)[0];
  out.push({
    id: 'T9001', signal_id: 'sig-005', strategy_id: 'ema_supertrend_adx', underlying: 'NIFTY',
    instrument_symbol: 'NIFTY20OCT2625100CE', side: 'BUY', qty: 75, entry_ts: isoIST(today + 3000),
    entry_price: 142.5, stop: 118, targets: [167, 191], mode: 'paper', status: 'open', ltp: 156.2,
    pnl: round((156.2 - 142.5) * 75), reasons: ['EMA9 crossed above EMA21', 'Supertrend flipped green'],
  });
  return out.reverse();
}
let tradesCache: TradeRow[] | null = null;
const trades = () => (tradesCache ??= makeTrades());

function breakdown(rows: TradeRow[], key: (t: TradeRow) => string) {
  const m: Record<string, { n: number; win_rate: number; pnl: number; expectancy_r: number; profit_factor: number | null }> = {};
  const g: Record<string, TradeRow[]> = {};
  rows.forEach((t) => (g[key(t)] ??= []).push(t));
  Object.entries(g).forEach(([k, ts]) => {
    const wins = ts.filter((t) => (t.pnl ?? 0) > 0);
    const gp = wins.reduce((a, t) => a + (t.pnl ?? 0), 0);
    const gl = -ts.filter((t) => (t.pnl ?? 0) <= 0).reduce((a, t) => a + (t.pnl ?? 0), 0);
    m[k] = {
      n: ts.length,
      win_rate: round(wins.length / ts.length, 3),
      pnl: round(ts.reduce((a, t) => a + (t.pnl ?? 0), 0)),
      expectancy_r: round(ts.reduce((a, t) => a + (t.r_multiple ?? 0), 0) / ts.length, 2),
      profit_factor: gl > 0 ? round(gp / gl, 2) : null,
    };
  });
  return m;
}

function equityOf(pnls: Array<[number, number]>, start = 200000) {
  let eq = start,
    peak = start;
  const equity: LinePoint[] = [];
  const drawdown: LinePoint[] = [];
  pnls.forEach(([time, p]) => {
    eq += p;
    peak = Math.max(peak, eq);
    equity.push({ time, value: round(eq) });
    drawdown.push({ time, value: round(eq - peak) });
  });
  return { equity, drawdown };
}

function analytics(): AnalyticsResponse {
  const closed = trades().filter((t) => t.status === 'closed').slice().reverse();
  const pts: Array<[number, number]> = [];
  let lastT = 0;
  closed.forEach((t) => {
    let time = Math.floor(new Date(t.exit_ts ?? t.entry_ts).getTime() / 1000);
    if (time <= lastT) time = lastT + 1;
    lastT = time;
    pts.push([time, t.pnl ?? 0]);
  });
  const { equity, drawdown } = equityOf(pts);
  const wins = closed.filter((t) => (t.pnl ?? 0) > 0);
  const gp = wins.reduce((a, t) => a + (t.pnl ?? 0), 0);
  const gl = -closed.filter((t) => (t.pnl ?? 0) <= 0).reduce((a, t) => a + (t.pnl ?? 0), 0);
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const hour = (t: TradeRow) => `${String(new Date(new Date(t.entry_ts).getTime() + IST * 1000).getUTCHours()).padStart(2, '0')}:00`;
  return {
    equity,
    drawdown,
    n: closed.length,
    win_rate: round(wins.length / closed.length, 3),
    profit_factor: round(gp / gl, 2),
    expectancy: round((gp - gl) / closed.length),
    expectancy_r: round(closed.reduce((a, t) => a + (t.r_multiple ?? 0), 0) / closed.length, 2),
    max_drawdown: Math.min(...drawdown.map((d) => d.value)),
    total_pnl: round(gp - gl),
    discipline_score: 86,
    by_strategy: breakdown(closed, (t) => t.strategy_id),
    by_hour: breakdown(closed, hour),
    by_weekday: breakdown(closed, (t) => wd[new Date(new Date(t.entry_ts).getTime() + IST * 1000).getUTCDay()]),
    by_regime: breakdown(closed, (t) => (['trend_up', 'trend_down', 'range', 'high_vol'] as const)[(t.id.charCodeAt(4) ?? 0) % 4]),
    by_underlying: breakdown(closed, (t) => t.underlying),
  };
}

// ---------------------------------------------------------------- option chain
function normCdf(x: number) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp((-x * x) / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}
function optionChain(underlying: string, expiry?: string): OptionChainResponse {
  const spot = ltp(underlying);
  const step = underlying === 'BANKNIFTY' ? 100 : 50;
  const atm = Math.round(spot / step) * step;
  const expiries = ['2026-10-20', '2026-10-27', '2026-11-03', '2026-11-24'];
  const exp = expiry && expiries.includes(expiry) ? expiry : expiries[0];
  const T = Math.max((new Date(`${exp}T15:30:00+05:30`).getTime() - now()) / (365 * 864e5), 1 / 365);
  const r = rng(atm + exp.length);
  const rows: Array<{ strike: number; ce: OptionLeg; pe: OptionLeg }> = [];
  for (let k = -10; k <= 10; k++) {
    const K = atm + k * step;
    const leg = (opt: 'CE' | 'PE'): OptionLeg => {
      const iv = 0.12 + 0.0009 * Math.abs(k) ** 1.6 + (opt === 'PE' && k < 0 ? 0.01 : 0) + r() * 0.004;
      const sq = iv * Math.sqrt(T);
      const d1 = (Math.log(spot / K) + (0.065 + (iv * iv) / 2) * T) / sq;
      const d2 = d1 - sq;
      const pdf = Math.exp((-d1 * d1) / 2) / Math.sqrt(2 * Math.PI);
      const disc = Math.exp(-0.065 * T);
      const price = opt === 'CE' ? spot * normCdf(d1) - K * disc * normCdf(d2) : K * disc * normCdf(-d2) - spot * normCdf(-d1);
      const otm = opt === 'CE' ? k : -k;
      const oiBase = (otm >= 0 ? 1 : 0.35) * 9e6 * Math.exp(-Math.abs(otm - 3) / 4) * (0.7 + r() * 0.6);
      const oi = Math.round(oiBase / 75) * 75;
      const oiChg = Math.round(((r() - 0.4) * oi * 0.25) / 75) * 75;
      const pxChg = (r() - 0.5) * 2;
      const buildup = oiChg > 0 ? (pxChg > 0 ? 'long_buildup' : 'short_buildup') : pxChg > 0 ? 'short_covering' : 'long_unwinding';
      return {
        ltp: round(Math.max(price, 0.05)),
        oi,
        oi_change: oiChg,
        iv: round(iv * 100, 1),
        delta: round(opt === 'CE' ? normCdf(d1) : normCdf(d1) - 1, 3),
        gamma: round(pdf / (spot * sq), 5),
        theta: round((-(spot * pdf * iv) / (2 * Math.sqrt(T)) - 0.065 * K * disc * (opt === 'CE' ? normCdf(d2) : -normCdf(-d2))) / 365, 2),
        vega: round((spot * pdf * Math.sqrt(T)) / 100, 2),
        buildup: Math.abs(oiChg) < oi * 0.02 ? 'neutral' : buildup,
        volume: Math.round(oi * (0.5 + r())),
      };
    };
    rows.push({ strike: K, ce: leg('CE'), pe: leg('PE') });
  }
  const sum = (f: (x: (typeof rows)[0]) => number) => rows.reduce((a, x) => a + f(x), 0);
  const pcr = round(sum((x) => x.pe.oi ?? 0) / sum((x) => x.ce.oi ?? 0), 2);
  const byCe = rows.slice().sort((a, b) => (b.ce.oi ?? 0) - (a.ce.oi ?? 0));
  const byPe = rows.slice().sort((a, b) => (b.pe.oi ?? 0) - (a.pe.oi ?? 0));
  let best = Infinity,
    maxPain = atm;
  rows.forEach(({ strike: s }) => {
    const pain = sum((x) => (x.ce.oi ?? 0) * Math.max(s - x.strike, 0) + (x.pe.oi ?? 0) * Math.max(x.strike - s, 0));
    if (pain < best) {
      best = pain;
      maxPain = s;
    }
  });
  return {
    spot,
    expiry: exp,
    expiries,
    pcr,
    max_pain: maxPain,
    walls: { resistance: byCe.slice(0, 3).map((x) => x.strike), support: byPe.slice(0, 3).map((x) => x.strike) },
    rows,
  };
}

// ---------------------------------------------------------------- context / news / events
function context(): ContextSnapshot {
  const mk = (label: string, value: number, change_pct: number, age_s: number, weight: number, required = false, stale = false) => ({
    label, value, change_pct, age_s, stale, weight, required,
  });
  return {
    ts: isoIST(Math.floor(now() / 1000) - 20),
    macro_bias: 0.34,
    risk_off: false,
    event_risk: 0.35,
    usable: true,
    inputs: {
      gift_nifty: mk('GIFT Nifty', 25236, 0.31, 2520, 2.0, false, true),
      sp500_fut: mk('S&P 500 fut', 6712.5, 0.62, 140, 1.0, true),
      nasdaq_fut: mk('Nasdaq 100 fut', 24890, 0.84, 140, 0.7),
      nikkei: mk('Nikkei 225', 48210, 0.91, 9000, 0.5),
      hang_seng: mk('Hang Seng', 25840, -0.42, 600, 0.5),
      usdinr: mk('USD/INR', 88.62, -0.11, 300, -0.8),
      dxy: mk('Dollar index', 98.4, -0.2, 300, -0.4),
      us10y: mk('US 10Y yield', 4.08, 0.6, 300, -0.3),
      brent: mk('Brent crude', 63.4, 1.4, 300, -0.5),
      india_vix: mk('India VIX', 13.2, -3.1, 30, -1.0, true),
    },
    reasons: [
      'S&P 500 fut +0.62% (bullish)',
      'Nasdaq 100 fut +0.84% (bullish)',
      'Nikkei 225 +0.91% (bullish)',
      'Brent crude +1.4% (bearish for Nifty)',
      'India VIX 13.2, falling (calm)',
      'GIFT Nifty quote is 42 min old (stale, not required)',
    ],
    stale_required: [],
  };
}
function news(): NewsItem[] {
  const t = Math.floor(now() / 1000);
  const rows: Array<[string, string, number, number, 'low' | 'medium' | 'high']> = [
    ['Moneycontrol', 'RBI keeps repo rate unchanged at 5.50%, maintains neutral stance', 0.2, 0.95, 'high'],
    ['Economic Times', 'FIIs turn net buyers for third straight session, pump ₹2,140 crore', 0.55, 0.85, 'medium'],
    ['Reuters', 'Oil climbs 1.4% on Middle East supply worries', -0.35, 0.6, 'medium'],
    ['Business Standard', 'HDFC Bank Q2 profit beats estimates; NIM stable', 0.62, 0.9, 'high'],
    ['Livemint', 'Rupee edges higher against dollar in early trade', 0.15, 0.5, 'low'],
    ['Reuters', 'US CPI due today; markets price in steady Fed path', 0.0, 0.7, 'high'],
    ['Moneycontrol', 'SEBI tightens F&O position limits for index derivatives', -0.4, 0.8, 'high'],
    ['Economic Times', 'IT stocks drag as Nasdaq futures pare gains', -0.25, 0.55, 'low'],
  ];
  return rows.map(([source, title, sentiment, relevance, impact], i) => ({
    id: `n${i}`, ts: isoIST(t - i * 1700 - 60), source, title, url: 'https://example.com/news/' + i, sentiment, relevance, impact,
  }));
}
function events(): EventsResponse {
  const t = Math.floor(now() / 1000);
  const ev = [
    { id: 'e1', ts: isoIST(t + 720), title: 'US CPI (Sep)', category: 'cpi', impact: 'high' as const },
    { id: 'e2', ts: isoIST(t + 86400 * 4), title: 'NIFTY weekly expiry', category: 'expiry', impact: 'medium' as const },
    { id: 'e3', ts: isoIST(t + 86400 * 5), title: 'BANKNIFTY monthly expiry', category: 'expiry', impact: 'medium' as const },
    { id: 'e4', ts: isoIST(t + 86400 * 12), title: 'FOMC rate decision', category: 'fomc', impact: 'high' as const },
    { id: 'e5', ts: isoIST(t + 86400 * 6), title: 'Reliance Q2 results', category: 'results', impact: 'high' as const },
  ];
  return { events: ev, blackout: { active: true, event: ev[0] } };
}

// ---------------------------------------------------------------- replay / quiz / backtest
function replayDays() {
  return weekdays(11).slice(0, -1).map(dayStr).reverse();
}
function replay(underlying: string, day: string): ReplayResponse {
  const d = weekdays(11).find((x) => dayStr(x) === day) ?? weekdays(2)[0];
  const candles = genDay(underlying, d, BASE[underlying] ?? 25000, d + 7);
  const sig = (index: number, sid: string, side: 'BUY' | 'SELL') => {
    const c = candles[index];
    const risk = (underlying === 'BANKNIFTY' ? 0.0022 : 0.0016) * c.close;
    const s = side === 'BUY' ? 1 : -1;
    return {
      id: `${day}-${index}`, index, strategy_id: sid, strategy_name: STRATS.find((x) => x[0] === sid)?.[1] ?? sid, side,
      entry: c.close, stop: round(c.close - s * risk), targets: [round(c.close + s * risk), round(c.close + 2 * s * risk)],
      probability: 0.55, sample_size: 180, reasons: ['Conditions met at bar close'],
    };
  };
  const signals = [sig(4, 'orb15', candles[4].close > candles[0].open ? 'BUY' : 'SELL'), sig(22, 'vwap_pullback', 'BUY'), sig(48, 'rsi_divergence', 'SELL')];
  const outcomes = signals.map((s) => {
    const sgn = s.side === 'BUY' ? 1 : -1;
    for (let i = s.index + 1; i < candles.length; i++) {
      const c = candles[i];
      if ((sgn > 0 ? c.low <= s.stop : c.high >= s.stop)) return { signal_id: s.id, index: s.index, outcome: 'stop', r: -1, exit_index: i };
      if ((sgn > 0 ? c.high >= s.targets[1] : c.low <= s.targets[1])) return { signal_id: s.id, index: s.index, outcome: 'target2', r: 2, exit_index: i };
    }
    const last = candles[candles.length - 1].close;
    return { signal_id: s.id, index: s.index, outcome: 'time_exit', r: round((sgn * (last - s.entry)) / Math.abs(s.entry - s.stop)), exit_index: candles.length - 1 };
  });
  return { candles, signals, outcomes };
}
let quizN = 0;
function quiz(underlying: string): QuizResponse {
  const days = replayDays();
  const day = days[quizN++ % days.length];
  const rp = replay(underlying, day);
  const s = rp.signals[quizN % rp.signals.length];
  const o = (rp.outcomes as Array<{ outcome: string; r?: number }>)[quizN % rp.signals.length];
  const answer = (o.r ?? 0) > 0 ? (s.side === 'BUY' ? 'long' : 'short') : 'skip';
  return {
    day,
    upto_index: s.index,
    candles: rp.candles,
    question: `${underlying} ${day}, ${s.strategy_name ?? s.strategy_id} triggers a ${s.side} at ${s.entry} (stop ${s.stop}). Would you take this trade?`,
    choices: [
      { id: 'long', label: 'Take it long' },
      { id: 'short', label: 'Take it short' },
      { id: 'skip', label: 'Skip it' },
    ],
    answer_id: answer,
    explanation: `Outcome: ${o.outcome} (${o.r ?? 0}R).`,
  };
}
function backtest(strategyId: string, underlying: string, days: number): BacktestResponse {
  const r = rng(strategyId.length * 31 + days + underlying.length);
  const rep = report(strategyId) ?? { status: 'insufficient' as const, badges: [], metrics: {}, warnings: [] };
  const n = Math.max(20, Math.round(days * 0.9));
  const wr = rep.metrics.win_rate ?? 0.5;
  const rs: number[] = [];
  for (let i = 0; i < n; i++) rs.push(r() < wr ? round(0.8 + r() * 1.4) : round(-(0.7 + r() * 0.4)));
  const t0 = Math.floor(now() / 1000) - days * 86400;
  let eq = 0;
  const equity = rs.map((x, i) => ({ time: t0 + Math.floor((i * days * 86400) / n), value: round((eq += x), 2) }));
  const tradesOut = rs.slice(-25).map((x, i) => ({ n: n - 25 + i + 1, r: x, outcome: x > 0 ? 'target' : 'stop' }));
  return {
    metrics: rep.metrics,
    report: rep,
    trades: tradesOut,
    equity,
    monte_carlo: { dd_p50: round(6 + r() * 3, 1), dd_p95: round(11 + r() * 5, 1), ruin_prob: round(r() * 0.03, 3), final_r_p05: round(eq * 0.4, 1), final_r_p50: round(eq, 1) },
    walk_forward: {
      folds: [1, 2, 3, 4, 5].map((f) => ({ fold: f, n: Math.round(n / 5), win_rate: round(wr + (r() - 0.5) * 0.1, 3), expectancy_r: round((rep.metrics.expectancy_r ?? 0) + (r() - 0.5) * 0.1, 2), profit_factor: round(1 + r() * 0.8, 2) })),
      stability: round(0.6 + r() * 0.3, 2),
      warnings: strategyId === 'gap_fill' ? ['OOS expectancy negative in 3/5 folds'] : [],
    },
  };
}

// ---------------------------------------------------------------- misc endpoints
function status(): StatusResponse {
  const closedToday = 2;
  return {
    mode: state.mode,
    demo_mode: true,
    market_open: true,
    broker: { connected: true, name: state.mode === 'paper' ? 'paper' : 'angel_one', logged_in: state.mode === 'live' },
    static_ip: { ok: true, public_ip: '203.0.113.24', expected: '203.0.113.24', message: 'Public IP matches registered static IP' },
    feed: { last_tick_age_s: 0.8, ws: 'connected' },
    risk: { locked: state.locked, lock_reason: state.lockReason, daily_pnl: 2140, trades_today: closedToday + 1, max_trades: 5, daily_loss_used_pct: 22 },
    disclaimer_accepted: state.disclaimer,
    server_time: isoIST(Math.floor(now() / 1000)),
  };
}
function settings(): SettingsResponse {
  return {
    app: { demo_mode: true, disclaimer_accepted: state.disclaimer, host: '0.0.0.0', port: 8000 },
    broker: { static_ip: '203.0.113.24', login_time: '08:30', intraday_cutoff: '15:20', ip_check_url: 'https://api.ipify.org' },
    risk: {
      capital: 200000, risk_per_trade_pct: 1, max_daily_loss_pct: 3, max_trades_per_day: 5, max_consecutive_losses: 3,
      max_open_positions: 2, max_lots_per_index: { NIFTY: 4, BANKNIFTY: 4 }, no_trade_first_min: 5, no_trade_last_min: 20,
      cooldown_after_loss_min: 10, revenge_size_multiplier: 1, stale_feed_seconds: 10, lock_edits_during_market: true,
    },
    execution: { mode: state.mode, default_execution_mode: 'signal_only', limit_buffer_ticks: 4, preferred_instrument: 'option', option_moneyness: 'ATM' },
    context: {
      refresh_seconds: 60, risk_off_threshold: -0.5, vix_risk_off_level: 22,
      inputs: Object.fromEntries(Object.entries(context().inputs).map(([k, v]) => [k, { symbol: k.toUpperCase(), label: v.label, weight: v.weight, required: v.required, max_age_min: k === 'gift_nifty' ? 30 : 1440 }])),
    },
    news: {
      poll_seconds: 120, high_impact_pause: true,
      sources: [
        { name: 'Moneycontrol', url: 'https://www.moneycontrol.com/rss/marketreports.xml', kind: 'rss', enabled: true },
        { name: 'Economic Times', url: 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms', kind: 'rss', enabled: true },
        { name: 'RBI press releases', url: 'https://www.rbi.org.in/pressreleases_rss.xml', kind: 'rss', enabled: false },
      ],
    },
    notify: { telegram_enabled: false, telegram_chat_id: '', notify_signals: true, notify_high_impact_news: true, notify_eod: true },
    secrets: { ...state.secrets },
    can_edit_limits: false,
  };
}
function health(): HealthResponse {
  return {
    ws_status: 'connected', last_tick_age_s: 0.8, latency_ms: 42, broker_errors: 0, order_rate: 0.2,
    jobs: [
      { name: 'broker_login', next_run: isoIST(Math.floor(now() / 1000) + 64000), last_run: isoIST(Math.floor(now() / 1000) - 22000), ok: true },
      { name: 'instrument_master', next_run: isoIST(Math.floor(now() / 1000) + 63000), last_run: isoIST(Math.floor(now() / 1000) - 23000), ok: true },
      { name: 'static_ip_check', next_run: isoIST(Math.floor(now() / 1000) + 1800), last_run: isoIST(Math.floor(now() / 1000) - 1800), ok: true },
      { name: 'eod_report', next_run: isoIST(Math.floor(now() / 1000) + 20000), last_run: null, ok: true },
    ],
    log_tail: [
      '09:15:02 INFO  feed connected (2 tokens)',
      '09:30:00 INFO  orb15: opening range NIFTY 25142.1–25198.6',
      '10:02:11 INFO  signal sig-001 vwap_pullback NIFTY BUY p=0.63',
      '10:14:40 WARN  context: GIFT Nifty quote stale (42m)',
    ],
  };
}
function premarket(): PremarketResponse {
  return {
    date: dayStr(Math.floor(now() / 1000)),
    context: context(),
    gap_expectation: { underlying: 'NIFTY', points: 48, pct: 0.19, text: 'Mild gap-up of ~48 pts expected (GIFT Nifty +0.31%)' },
    key_levels: { NIFTY: levels(seriesFor('NIFTY')), BANKNIFTY: levels(seriesFor('BANKNIFTY')) },
    events_today: events().events.slice(0, 1),
    enabled_strategies: strategies().filter((s) => s.enabled).map((s) => ({ id: s.id, name: s.name, suits_regime: !s.regimes || s.regimes.includes('trend_up') })),
    regime: { NIFTY: 'trend_up', BANKNIFTY: 'range' },
  };
}
function eod(day?: string): EodReport {
  return {
    day: day ?? dayStr(Math.floor(now() / 1000)),
    pnl: 2140, trades: 3, wins: 2, losses: 1, win_rate: 0.67, max_drawdown: -1350, discipline_score: 92,
    signals_generated: 6, signals_taken: 2, signals_blocked: 1,
    summary: 'Trend-up day on NIFTY; VWAP pullback and EMA/Supertrend setups worked. One ORB short blocked by the US CPI blackout.',
    lessons: ['Waited for confirmation on VWAP pullback — good process', 'Skipped CPR short in a trending tape — correct'],
    by_strategy: { vwap_pullback: { n: 1, pnl: 1850 }, ema_supertrend_adx: { n: 1, pnl: 1027 }, orb15: { n: 1, pnl: -737 } },
  };
}

// ---------------------------------------------------------------- router
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function mockRequest(method: string, fullPath: string, body?: unknown): Promise<unknown> {
  await delay(120);
  const url = new URL(fullPath, 'http://mock');
  const p = url.pathname;
  const q = (k: string) => url.searchParams.get(k) ?? undefined;
  const b = (body ?? {}) as Record<string, unknown>;
  let m: RegExpMatchArray | null;

  if (method === 'GET') {
    switch (p) {
      case '/api/status': return status();
      case '/api/candles': return candlesResp(q('underlying') ?? 'NIFTY', Number(q('limit') ?? 500));
      case '/api/overview':
        return {
          indices: ['NIFTY', 'BANKNIFTY'].map((u) => ({
            underlying: u, ltp: ltp(u), change_pct: round(((ltp(u) - prevClose(u)) / prevClose(u)) * 100), regime: u === 'NIFTY' ? 'trend_up' : 'range',
            vix: 13.2, pcr: optionChain(u).pcr, max_pain: optionChain(u).max_pain,
          })),
        };
      case '/api/context': return context();
      case '/api/signals': {
        const st = q('status');
        return { signals: makeSignals().filter((s) => !st || s.status === st) };
      }
      case '/api/positions': {
        const open = trades().filter((t) => t.status === 'open');
        return { positions: open.map((t) => ({ symbol: t.instrument_symbol, qty: t.qty, avg_price: t.entry_price, ltp: t.ltp ?? t.entry_price, pnl: t.pnl ?? 0 })), open_trades: open };
      }
      case '/api/option-chain': return optionChain(q('underlying') ?? 'NIFTY', q('expiry'));
      case '/api/news': return { items: news().slice(0, Number(q('limit') ?? 50)) };
      case '/api/events': return events();
      case '/api/trades': {
        const s = q('strategy');
        const start = q('start');
        const end = q('end');
        return {
          trades: trades().filter((t) => (!s || t.strategy_id === s) && (!start || t.entry_ts.slice(0, 10) >= start) && (!end || t.entry_ts.slice(0, 10) <= end)),
        };
      }
      case '/api/analytics': return analytics();
      case '/api/strategies': return { strategies: strategies() };
      case '/api/replay/days': return { days: replayDays() };
      case '/api/replay': return replay(q('underlying') ?? 'NIFTY', q('day') ?? replayDays()[0]);
      case '/api/learn/quiz': return quiz(q('underlying') ?? 'NIFTY');
      case '/api/premarket': return premarket();
      case '/api/eod': return eod(q('day'));
      case '/api/settings': return settings();
      case '/api/health': return health();
    }
    if ((m = p.match(/^\/api\/trades\/([^/]+)$/))) {
      const t = trades().find((x) => x.id === decodeURIComponent(m![1]));
      if (!t) throw new Error('trade not found');
      const win = (t.pnl ?? 0) > 0;
      const detail: TradeDetail = {
        ...t,
        analysis: t.status === 'closed' ? {
          entry_quality: 'Entry within 0.1 ATR of the planned level — good.',
          mfe_mae: `MFE ${t.mfe}R / MAE ${t.mae}R`,
          plan_vs_actual: win ? 'Followed plan; exited at target.' : 'Stop hit as planned; no averaging down.',
          slippage: '0.35 pts vs signal entry',
          rule_violations: [],
          quadrant: win ? 'good trade · good result' : 'good trade · bad result',
          summary: win ? 'Setup and context aligned; trend continued.' : 'Valid setup, but momentum faded after a high-impact headline.',
        } : null,
        notes: state.notes[t.id] ?? [],
      };
      return detail;
    }
    if ((m = p.match(/^\/api\/models\/([^/]+)$/))) {
      if (m[1] === 'oi_pcr') return { metrics: null, reliability: [] };
      const r = rng(m[1].length);
      return {
        metrics: { auc: round(0.58 + r() * 0.06, 3), brier: round(0.22 + r() * 0.02, 3), n: 412 },
        reliability: [0.25, 0.35, 0.45, 0.55, 0.65, 0.75].map((pp) => ({ p_pred: pp, p_obs: round(Math.min(0.95, Math.max(0.05, pp + (r() - 0.5) * 0.12)), 2), n: 20 + Math.floor(r() * 90) })),
      };
    }
  }

  if (method === 'POST' || method === 'PUT') {
    if ((m = p.match(/^\/api\/signals\/([^/]+)\/(confirm|skip)$/))) {
      state.signalStatus[decodeURIComponent(m[1])] = m[2] === 'confirm' ? 'taken' : 'skipped';
      return { ok: true };
    }
    if ((m = p.match(/^\/api\/trades\/([^/]+)\/notes$/))) {
      (state.notes[m[1]] ??= []).push({ ...(b as unknown as TradeNote), ts: isoIST(Math.floor(now() / 1000)) });
      return { ok: true };
    }
    if ((m = p.match(/^\/api\/strategies\/([^/]+)$/))) {
      state.strategyOverrides[m[1]] = { ...state.strategyOverrides[m[1]], ...(b as Partial<StrategyInfo>) };
      return { ok: true };
    }
    switch (p) {
      case '/api/kill-switch':
        state.locked = true;
        state.lockReason = 'Kill switch activated';
        return { ok: true, cancelled: 2, squared_off: 1 };
      case '/api/backtest': return backtest(String(b.strategy_id), String(b.underlying ?? 'NIFTY'), Number(b.days ?? 250));
      case '/api/settings': return { ok: true };
      case '/api/secrets':
        state.secrets[String(b.name)] = Boolean(b.value);
        return { ok: true };
      case '/api/mode':
        if (b.mode === 'live' && b.confirm_text !== 'I UNDERSTAND THE RISK') throw new Error('confirm_text mismatch');
        state.mode = b.mode === 'live' ? 'live' : 'paper';
        return { ok: true };
      case '/api/disclaimer/accept':
        state.disclaimer = true;
        try {
          localStorage.setItem('ie-mock-disclaimer', '1');
        } catch {
          /* ignore */
        }
        return { ok: true };
      case '/api/login': return { ok: true, message: 'Mock login successful' };
    }
  }
  throw new Error(`mock: no handler for ${method} ${p}`);
}

// ---------------------------------------------------------------- websocket simulation
let tickN = 0;
export function mockTick(): WsMessage[] {
  tickN++;
  const out: WsMessage[] = [];
  for (const u of ['NIFTY', 'BANKNIFTY']) {
    const s = seriesFor(u);
    const last = s[s.length - 1];
    const r = rng(tickN * 13 + u.length);
    const px = round(last.close * (1 + gauss(r) * 0.00025));
    last.close = px;
    last.high = Math.max(last.high, px);
    last.low = Math.min(last.low, px);
    last.volume += Math.round(r() * 2000);
    out.push({ type: 'tick', data: { underlying: u, ltp: px, ts: isoIST(Math.floor(now() / 1000)) } });
    out.push({ type: 'candle', data: { ...last, underlying: u, tf: '5m' } });
  }
  if (tickN % 5 === 0) out.push({ type: 'health', data: { last_tick_age_s: 0.5, latency_ms: 30 + (tickN % 20) } });
  return out;
}
