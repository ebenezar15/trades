// lightweight-charts (v4) wrappers: candlestick chart with overlays/levels/markers, and a simple line/area chart.
// Backend times are true unix seconds; we shift by +05:30 so the axis reads IST on any device.
import {
  createChart,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
  type LineData,
} from 'lightweight-charts';
import { useEffect, useRef } from 'react';
import type { Candle, CandleMarker, CandlesResponse, KeyLevels, LinePoint, TradeLine } from '../api/types';
import { IST_OFFSET_S } from '../lib/format';
import { chartColors, useTheme, type Theme } from '../lib/theme';

const t = (s: number) => (s + IST_OFFSET_S) as UTCTimestamp;

function baseOptions(theme: Theme, height: number) {
  const c = chartColors(theme);
  return {
    height,
    autoSize: true,
    layout: { background: { color: c.bg }, textColor: c.text, fontSize: 11 },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border, timeVisible: true, secondsVisible: false, rightOffset: 4 },
    crosshair: { mode: CrosshairMode.Normal },
    // explicit locale: some devices report tags like "en-US@posix" that Intl rejects
    localization: { locale: 'en-IN' },
  };
}

const LEVEL_STYLE: Record<keyof KeyLevels, { color: string; title: string }> = {
  pivot: { color: '#a78bfa', title: 'P' },
  bc: { color: '#a78bfa', title: 'BC' },
  tc: { color: '#a78bfa', title: 'TC' },
  r1: { color: '#f87171', title: 'R1' },
  s1: { color: '#4ade80', title: 'S1' },
  prev_high: { color: '#fb923c', title: 'PDH' },
  prev_low: { color: '#fb923c', title: 'PDL' },
  or_high: { color: '#38bdf8', title: 'ORH' },
  or_low: { color: '#38bdf8', title: 'ORL' },
};

export interface ChartMarker {
  time: number;
  position: 'aboveBar' | 'belowBar' | 'inBar';
  color: string;
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square';
  text?: string;
}
export interface ChartLine {
  price: number;
  color: string;
  title: string;
  dashed?: boolean;
}

interface CandleChartProps {
  candles: Candle[];
  overlays?: CandlesResponse['overlays'] | null;
  levels?: KeyLevels | null;
  show?: { ema?: boolean; vwap?: boolean; supertrend?: boolean; levels?: boolean; volume?: boolean; signals?: boolean };
  markers?: ChartMarker[];
  lines?: ChartLine[];
  /** signal markers from /api/candles (hidden when show.signals === false) */
  signalMarkers?: CandleMarker[] | null;
  /** open-trade entry/stop/target lines from /api/candles (hidden when show.signals === false) */
  tradeLines?: TradeLine[] | null;
  live?: Candle | null;
  height?: number;
  /** change this to force a fitContent (e.g. when switching underlying) */
  fitKey?: string;
  /** keep a fixed window of N bars from the first bar (replay: whole session stays in view) */
  fixedBars?: number;
}

const toUnix = (v: number | string) => (typeof v === 'number' ? v : Math.floor(Date.parse(v) / 1000));

/** Price lines for open trades: entry (blue), stop (red), targets (green), e.g. "T1 VWAP Pullback". */
export function tradeLinesToChartLines(tls: TradeLine[] | null | undefined): ChartLine[] {
  const out: ChartLine[] = [];
  (tls ?? []).forEach((tl) => {
    const lab = tl.label ? ` ${tl.label}` : '';
    if (Number.isFinite(tl.entry)) out.push({ price: tl.entry, color: '#3b82f6', title: `Entry${lab}` });
    if (Number.isFinite(tl.stop)) out.push({ price: tl.stop, color: '#ef4444', title: `SL${lab}`, dashed: true });
    (tl.targets ?? []).forEach((p, i) => Number.isFinite(p) && out.push({ price: p, color: '#22c55e', title: `T${i + 1}${lab}`, dashed: true }));
  });
  return out;
}

export function CandleChart({ candles, overlays, levels, show = {}, markers, lines, signalMarkers, tradeLines, live, height = 380, fitKey, fixedBars }: CandleChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const s = useRef<{
    candle?: ISeriesApi<'Candlestick'>;
    vol?: ISeriesApi<'Histogram'>;
    ema9?: ISeriesApi<'Line'>;
    ema21?: ISeriesApi<'Line'>;
    vwap?: ISeriesApi<'Line'>;
    stUp?: ISeriesApi<'Line'>;
    stDn?: ISeriesApi<'Line'>;
    priceLines: IPriceLine[];
  }>({ priceLines: [] });
  const [theme] = useTheme();
  const lastFit = useRef<string | undefined>(undefined);

  // create once
  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, baseOptions(theme, height));
    const col = chartColors(theme);
    s.current.vol = c.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
    c.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    s.current.candle = c.addCandlestickSeries({
      upColor: col.up,
      downColor: col.down,
      borderVisible: false,
      wickUpColor: col.up,
      wickDownColor: col.down,
    });
    const line = (color: string, width: 1 | 2 = 1, style: LineStyle = LineStyle.Solid) =>
      c.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    s.current.ema9 = line('#f59e0b');
    s.current.ema21 = line('#a855f7');
    s.current.vwap = line('#38bdf8', 2, LineStyle.Dashed);
    s.current.stUp = line('#22c55e', 2);
    s.current.stDn = line('#ef4444', 2);
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      s.current = { priceLines: [] };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // theme / height
  useEffect(() => {
    chart.current?.applyOptions(baseOptions(theme, height));
    const col = chartColors(theme);
    s.current.candle?.applyOptions({ upColor: col.up, downColor: col.down, wickUpColor: col.up, wickDownColor: col.down });
  }, [theme, height]);

  // data
  useEffect(() => {
    const S = s.current;
    if (!S.candle) return;
    S.candle.setData(candles.map((c) => ({ time: t(c.time), open: c.open, high: c.high, low: c.low, close: c.close })));
    const showVol = show.volume !== false;
    S.vol?.setData(
      showVol ? candles.map((c) => ({ time: t(c.time), value: c.volume, color: c.close >= c.open ? 'rgba(34,197,94,0.35)' : 'rgba(239,68,68,0.35)' })) : [],
    );
    const lp = (xs: LinePoint[] | undefined, on: boolean | undefined) => (on !== false && xs ? xs.map((p) => ({ time: t(p.time), value: p.value })) : []);
    S.ema9?.setData(lp(overlays?.ema9, show.ema));
    S.ema21?.setData(lp(overlays?.ema21, show.ema));
    S.vwap?.setData(lp(overlays?.vwap, show.vwap));
    const st = show.supertrend !== false ? overlays?.supertrend ?? [] : [];
    const split = (dir: number): Array<LineData | WhitespaceData> => st.map((p) => (p.direction === dir ? { time: t(p.time), value: p.value } : { time: t(p.time) }));
    S.stUp?.setData(split(1));
    S.stDn?.setData(split(-1));
    if (fixedBars && candles.length) {
      chart.current?.timeScale().setVisibleLogicalRange({ from: -1, to: Math.max(fixedBars, candles.length) + 1 });
    } else if (candles.length && lastFit.current !== (fitKey ?? 'x')) {
      lastFit.current = fitKey ?? 'x';
      const ts = chart.current?.timeScale();
      if (candles.length > 150) ts?.setVisibleLogicalRange({ from: candles.length - 150, to: candles.length + 4 });
      else ts?.fitContent();
    }
  }, [candles, overlays, show.ema, show.vwap, show.supertrend, show.volume, fitKey, fixedBars]);

  // price lines (levels + extra)
  useEffect(() => {
    const S = s.current;
    if (!S.candle) return;
    S.priceLines.forEach((pl) => S.candle!.removePriceLine(pl));
    S.priceLines = [];
    if (levels && show.levels !== false) {
      (Object.keys(LEVEL_STYLE) as Array<keyof KeyLevels>).forEach((k) => {
        const v = levels[k];
        if (v === null || v === undefined) return;
        const st = LEVEL_STYLE[k];
        S.priceLines.push(S.candle!.createPriceLine({ price: v, color: st.color, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: st.title }));
      });
    }
    const all = [...(lines ?? []), ...(show.signals !== false ? tradeLinesToChartLines(tradeLines) : [])];
    all.forEach((l) =>
      S.priceLines.push(
        S.candle!.createPriceLine({ price: l.price, color: l.color, lineWidth: l.color === '#3b82f6' ? 2 : 1, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title: l.title }),
      ),
    );
  }, [levels, lines, tradeLines, show.levels, show.signals]);

  useEffect(() => {
    const S = s.current;
    if (!S.candle) return;
    if (!candles.length) {
      S.candle.setMarkers([]);
      return;
    }
    const sig: ChartMarker[] =
      show.signals !== false
        ? (signalMarkers ?? [])
            .filter((m) => m && m.time != null)
            .map((m) => ({ time: toUnix(m.time), position: m.position, shape: m.shape, color: m.color, text: m.text }))
            .filter((m) => Number.isFinite(m.time))
        : [];
    const ms: SeriesMarker<Time>[] = [...(markers ?? []), ...sig].map((m) => ({ ...m, time: t(m.time) }));
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    try {
      S.candle.setMarkers(ms);
    } catch {
      /* marker outside data range — ignore */
    }
  }, [markers, signalMarkers, show.signals, candles]);

  // live update of last candle
  useEffect(() => {
    if (!live || !s.current.candle) return;
    const last = candles[candles.length - 1];
    if (last && live.time < last.time) return;
    try {
      s.current.candle.update({ time: t(live.time), open: live.open, high: live.high, low: live.low, close: live.close });
    } catch {
      /* out-of-order update — ignore */
    }
  }, [live, candles]);

  return <div ref={el} style={{ height }} className="w-full min-w-0 overflow-hidden rounded-lg" />;
}

export interface SeriesSpec {
  type: 'line' | 'area' | 'histogram';
  data: LinePoint[];
  color: string;
  title?: string;
  /** times in this series are dates/unix seconds; set false if already "chart time" */
  shiftIST?: boolean;
}

export function SeriesChart({ series, height = 240, percent }: { series: SeriesSpec[]; height?: number; percent?: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const [theme] = useTheme();

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, { ...baseOptions(theme, height), timeScale: { ...baseOptions(theme, height).timeScale, timeVisible: false } });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chart.current?.applyOptions({ ...baseOptions(theme, height), timeScale: { ...baseOptions(theme, height).timeScale, timeVisible: false } });
  }, [theme, height]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const created: Array<ISeriesApi<'Line'> | ISeriesApi<'Area'> | ISeriesApi<'Histogram'>> = [];
    series.forEach((sp) => {
      const pf = percent ? { type: 'percent' as const } : undefined;
      const opts = { title: sp.title, priceLineVisible: false, ...(pf ? { priceFormat: pf } : {}) };
      const api =
        sp.type === 'area'
          ? c.addAreaSeries({ ...opts, lineColor: sp.color, topColor: sp.color + '55', bottomColor: sp.color + '05', lineWidth: 2 })
          : sp.type === 'histogram'
            ? c.addHistogramSeries({ ...opts, color: sp.color })
            : c.addLineSeries({ ...opts, color: sp.color, lineWidth: 2 });
      // ensure strictly increasing times
      const seen = new Set<number>();
      const data = sp.data
        .map((p) => ({ time: (sp.shiftIST === false ? p.time : p.time + IST_OFFSET_S) as UTCTimestamp, value: p.value }))
        .sort((a, b) => a.time - b.time)
        .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true)));
      api.setData(data);
      created.push(api);
    });
    c.timeScale().fitContent();
    return () => {
      created.forEach((x) => {
        try {
          c.removeSeries(x);
        } catch {
          /* chart already removed */
        }
      });
    };
  }, [series, percent]);

  return <div ref={el} style={{ height }} className="w-full min-w-0 overflow-hidden rounded-lg" />;
}
