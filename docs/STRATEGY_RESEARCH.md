# IndexEdge: Strategy Research (Nifty 50 / Bank Nifty)

Survey date: October 2026. Scope: the intraday and short-term positional index strategies most widely discussed on the Indian trading internet (Zerodha Varsity and Zerodha's "In The Money" / TradingQnA, Groww, Upstox, Angel One, 5paisa, Elearnmarkets, TradingView India scripts and ideas, OptionX, AlgoTest, Moneycontrol, Quora, YouTube, Medium/LinkedIn write-ups, Sensibull and Opstra tooling).

---

## Honesty note (read first)

- **No publicly available intraday index strategy has independently verified 95–98% accuracy.** We found many "90% accuracy" and "95% accuracy" claims on YouTube, Instagram, Facebook groups and Telegram-style channels (examples: [YouTube "Bank Nifty Options Strategy | 90% Accuracy"](https://www.youtube.com/watch?v=h6hYrunPu0s), [Facebook "90% / 95% Accuracy strategy"](https://www.facebook.com/ANILFLP/posts/90-accuracy-strategy-stockmarketindia-nifty-tradingstrategy-viralreels-fyppppppp/3578105822364318/), [Instagram "90% accuracy indicator"](https://www.instagram.com/reel/DRmgIwjAeUt/), [Quora thread asking whether 90% is possible](https://www.quora.com/Is-it-possible-to-achieve-90-accuracy-in-Banknifty-and-Nifty-options-trading-and-be-consistently-profitable)). None of them gave an audited, out-of-sample record that included costs.
- **A high win rate usually comes from small targets and large stops.** For example, a strategy that targets 10 points with a 60-point stop can win 85% of the time and still lose money. The best-documented strategy here, the 9:20 short straddle, has a reported win rate of about 50–70%. Its average loss is reported at 2–3× its average win ([Quintalmind](https://www.quintalmind.com/blog/time-based-straddle-strategy-nifty-options), [LinkedIn write-up](https://www.linkedin.com/pulse/920-short-straddle-truth-accuracy-right-way-trade-nishant-kumar-m6gwc)).
- **Context.** SEBI's studies found that over 91% of individual F&O traders lost money in FY24. Net individual losses grew to about ₹1.05 lakh crore in FY25 ([Business Standard](https://www.business-standard.com/amp/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html), [Moneylife](https://moneylife.in/article/91-percentage-of-retail-traders-lost-money-in-derivatives-losses-in-fo-surged-41-percentage-to-rs105-lakh-crore-in-fy25-sebi-study/77613.html), [Value Research](https://www.valueresearchonline.com/stories/222075/sebi-report-91-per-cent-f-o-traders-lost-money-in-fy-24)).
- **IndexEdge policy.** IndexEdge displays only its **own measured, out-of-sample statistics, after costs**. Costs include brokerage, STT, exchange and SEBI fees, GST, stamp duty and modelled slippage. Every claimed figure in this document is labelled **"claimed, unverified"**. These figures are never shown to users as a property of the strategy.

### Common red flags seen across sources

| Flag | What it looks like |
|---|---|
| No costs | Backtests on spot/index points with no brokerage, STT or slippage. Zerodha's own ORB test says so explicitly. |
| Cherry-picked period | Results from a trending year only, or stopping just before a drawdown. The 9:20 straddle was profitable to 2020, then hit a 105-day drawdown in 2021–22. |
| Tiny sample | "Worked 9 of the last 10 days" screenshots, and single-day TradingView "ideas". |
| Very wide stops | High accuracy bought with a stop many times larger than the target. |
| Hindsight levels | Chart walkthroughs drawn after the fact, with divergence or CPR "predicting" a day that already happened. |
| Regime changes ignored | Bank Nifty weekly options ended in Nov 2024. Nifty weekly expiry moved to Tuesday. Lot sizes and STT changed. Old 0DTE Bank Nifty results do not carry forward. |
| Index vs instrument mismatch | Signal on spot, execution in options (theta/IV/spread), or on Heikin Ashi synthetic prices. |

### Market-structure caveat (affects expiry strategies)

- SEBI's Oct 2024 circular allowed each exchange to offer weekly options on only **one** benchmark index. **Bank Nifty weekly options were discontinued in Nov 2024**, and Bank Nifty now has monthly expiry only ([Zerodha support](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/weekly-expiries-discontinued-for-certain-index-derivatives), [Angel One](https://www.angelone.in/news/market-updates/bank-nifty-weekly-expiry-bids-adieu-2), [5paisa](https://5paisa.com/news/sebi-directive-to-end-bank-nifty-weekly-options-impact-on-nse-volumes)).
- **Nifty 50 weekly options now expire on Tuesday.** This comes from secondary reporting ([AlgoTest](https://algotest.in/blog/bank-nifty-expiry-day.md), [OneTradeJournal](https://onetradejournal.com/glossary/weekly-expiry)). IndexEdge must read the expiry calendar from the NSE contract master, not hard-code the weekday.

---

## Strategy catalogue

Popularity scale: **Very high**: covered by broker education portals and Zerodha, with many YouTube/TradingView items. **High**: many blogs and videos plus tooling or scripts. **Medium**: a recurring niche.

### 1. Opening Range Breakout (ORB), 15-minute range → `orb15`

- **Description.** Mark the high and low of the first N minutes. Trade a candle close beyond the range in the breakout direction.
- **Rules (the most common retail version):**
  - Instrument: Nifty / Bank Nifty spot for the signal. Execution in futures, or ATM/ITM options bought in the breakout direction.
  - Range: 9:15–9:30 (first 15-min candle). Signal timeframe: 5-min (some sources use 3-min).
  - Entry: long on a 5-min **close** above the range high; short on a close below the range low. Many sources add a volume filter: the breakout candle's volume must exceed the recent average.
  - Stop-loss: the opposite end of the range. Tighter variants use the range midpoint or the breakout candle's low/high.
  - Target: 1R–2R, or a trail.
  - Trailing: by the previous candle low/high or VWAP.
  - Time filter: entries only until about 11:00–13:00. Exit everything by 15:15. Some skip the day when the range is wider than about 1–1.2× ATR(14, daily) × some fraction, because the stop is too wide.
  - Max trades: usually one per direction per day.
- **Zerodha's tested variant.** 3-min chart, range 9:15–10:15 (also 9:15–11:15 … 14:15), entry on close beyond the range, stop at the opposite end, January 2019 to February 2026.
- **Popularity: Very high.**
  - [Zerodha "In The Money": All About ORB Part 1](https://inthemoneybyzerodha.substack.com/p/all-about-opening-range-breakout)
  - Zerodha YouTube: [Part 1](https://www.youtube.com/watch?v=_ePkfG6aXqU) and [ORB with Nifty options, Part 2](https://www.youtube.com/watch?v=erjnU13EYjk)
  - [LinkedIn 9:15–9:30 ORB post](https://www.linkedin.com/posts/vishalsubandh_intradaytrading-stockmarket-tradingstrategy-activity-7412341816898863104-kTKP)
  - [Quora ORB thread](https://www.quora.com/How-do-we-trade-the-15-minute-opening-range-breakout-intraday-strategy-with-good-accuracy)
- **Claimed win rate (claimed, unverified):**
  - Zerodha's backtest reports about **60% on the long side and about 50% on the short side**. It states that it **does not account for transaction costs, brokerage, taxes or slippage**.
  - Nothing else credible was found.
- **Red flags.** No costs. The long-side bias reflects Nifty's 2019–2026 uptrend (regime dependence). The stop at the opposite end can be very wide on gap days.

### 2. First 5-/15-minute candle high-low break → folded into `orb15` (variant `first_candle_high_low`)

- **Description.** The same idea as ORB, but the reference is the single first candle. This is popular as the "15-min candle strategy" on YouTube and Instagram.
- **Rules:**
  - The first 15-min (or 5-min) candle sets the high and low.
  - Enter when a later 5-min candle closes beyond it, or on a stop order at high + buffer / low − buffer.
  - SL at the other side of the candle, or its midpoint.
  - Target 1:2.
  - Instrument: Bank Nifty / Nifty ATM options.
  - No entries after about 11:30.
- **Popularity: Very high.**
  - [OptionX: Bank Nifty 15-min breakout](https://optionx.trade/blogs/bank-nifty-15-min-breakout-strategy)
  - [Scribd: Bank Nifty 15-min setup](https://www.scribd.com/document/935032894/Nifty-Bank-Nifty-Strategy)
  - Many daily TradingView "NIFTY 15-min breakout" ideas, for example [in.tradingview.com](https://in.tradingview.com/chart/NIFTY/7iAIDqZZ-Nifty-Trading-Strategy-for-01st-December-2025)
  - [YouTube "First 15 Minutes Strategy"](https://www.youtube.com/watch?v=NMUd0oX_7Pg)
- **Claimed win rate:** Not stated in the sources we found. YouTube titles often imply 70–90% (claimed, unverified; no methodology).
- **Mapping.** This is not a distinct strategy. It is `orb15` with `range_minutes ∈ {5, 15}` and `entry_mode ∈ {close, stop}`. No new ID is needed.

### 3. VWAP pullback (trend continuation) → `vwap_pullback`

- **Description.** On a trend day, wait for price to pull back to the session VWAP, then join the trend on rejection.
- **Rules:**
  - Signal timeframe: 5-min (some sources use 3-min). Instrument: futures (true VWAP needs volume; spot index has no volume), or options.
  - Trend definition: price above VWAP for most of the session, making higher highs and higher lows. Often also requires 9 EMA > 21 EMA, or price above the opening range.
  - Entry (long): a candle touches or pierces VWAP (within about 0.1% or 0.25×ATR), then closes back above it. A bullish rejection candle (hammer/engulfing) is preferred. Shorts mirror this.
  - SL: below the pullback swing low, or VWAP − 0.5×ATR.
  - Target: the previous swing high (about 1.5R–2R). Trail with VWAP or the previous candle.
  - Time filter: from about 9:45 (after VWAP stabilises) to 14:30.
- **Popularity: High.**
  - [TradeSmart VWAP strategy](https://tradesmartonline.in/blog/vwap-trading-strategy-a-smart-tool-for-intraday-traders/)
  - [Scribd VWAP pullback guide](https://www.scribd.com/document/729701887/vwap-signed)
  - [TradeMomentum VWAP pullback](https://www.trademomentum.org/blog/vwap-pullback-trading-strategy-for-day-traders)
  - [YouTube ORB + VWAP pullback](https://www.youtube.com/watch?v=9hWKM8bAWl4)
  - VWAP is also covered across Zerodha Varsity, Groww and Upstox.
- **Claimed win rate:** None credible found.
- **Red flags.** VWAP on spot index is undefined; sources rarely say which VWAP they use. Pullback definitions are discretionary.

### 4. EMA crossover + Supertrend (10,3) + ADX filter → `ema_supertrend_adx`

- **Description.** The most common indicator-stack trend follower on Indian YouTube and Elearnmarkets. Variants include 5/20 EMA, 9/21 EMA and 20/50 EMA.
- **Rules (consensus):**
  - Timeframe: 5-min (also 15-min).
  - Long when fast EMA > slow EMA (9/21, or 5/20 per Elearnmarkets), close > Supertrend(10,3) (green), and ADX(14) > 20–25 with +DI > −DI. Short mirrors this.
  - SL: the Supertrend line, or the signal candle's low/high.
  - Exit: Supertrend flip, opposite EMA cross, or a 1:2 target.
  - Optional 200-EMA bias filter.
  - Time filter: 9:30–14:45. Square off by 15:15.
- **Popularity: Very high.**
  - [Elearnmarkets Supertrend guide (Supertrend + 5/20 EMA)](https://blog.elearnmarkets.com/supertrend-indicator-strategy-trading/)
  - [Scribd EMA + Supertrend logic](https://www.scribd.com/document/453669964/EMA-SUPERTREND-CHANNEL-TRADING-LOGIC)
  - [Quora: best Supertrend settings](https://www.quora.com/What-are-the-best-Supertrend-settings-parameters-for-3-5-10-and-15-minutes-chart-for-intraday-trading)
  - [LuxAlgo/TradingView "BankNifty 5min Supertrend" strategy](https://www.luxalgo.com/library/indicator/LTRBSQI1-banknifty-5min-supertrend-based-strategy/)
  - [Spider Software Supertrend + EMA](https://www.facebook.com/spidersoftwareindia/posts/decode-the-market-with-clarity-supertrend-ema-simple-powerful-signals-for-smarte/1385005100308105/)
- **Claimed win rate (claimed, unverified):**
  - TradingRush's 200-trade test of Supertrend with a 200-MA filter and a 1.5R target reports **46% in a "mostly good" market and 57% in an "extremely good" market** ([TradingRush](https://tradingrush.net/i-tested-supertrend-indicator-200-times/)). This is not India-specific and has a small sample.
  - Quora threads ask about a "70% accuracy" strategy, with no evidence.
- **Red flags.** Whipsaws in ranges. The ADX filter is what keeps the strategy out of sideways days.

### 5. Triple Supertrend → new ID `triple_supertrend`

- **Description.** Three Supertrends with different sensitivity. Enter only when all three agree. This is a popular Bank Nifty options-buying setup.
- **Rules:**
  - Timeframe: 5-min.
  - Supertrends: ST(10,1), ST(11,2), ST(12,3). This is the common TradingView/Spider "triple" set. A TradingQnA variant uses (7,3), (10,3), (11,2).
  - Long (buy CE) when all three turn green on a closed candle. Short (buy PE) when all three are red.
  - Exit when **any one** flips colour, or at 15:00.
  - SL: the slowest ST line.
  - Optional RSI(7) or 200-EMA filter.
- **Popularity: Medium–High.**
  - [TradingView Triple SuperTrend + RSI + Fib BB](https://fr.tradingview.com/scripts/statistics/page-24)
  - [TradingQnA Supertrend strategy creation (7,3 / 10,3 / 11,2, exit 3 PM)](https://tradingqna.com/t/supertrend-strategy-creation/146250)
  - Spider Software "Triple SuperTrend" post (linked above)
- **Claimed win rate:** None found.
- **Why it is distinct.** The exit logic (any one flips) and the 3-way agreement are different enough from `ema_supertrend_adx` that it gets its own ID.

### 6. CPR (Central Pivot Range) + floor pivots → `cpr_pivot`

- **Description.** Compute daily levels from the previous day's H/L/C:
  - P = (H+L+C)/3
  - BC = (H+L)/2
  - TC = 2P − BC (swap TC and BC if TC < BC)
  - R1/S1, R2/S2: standard floor pivots
  
  A narrow CPR suggests a trending day. A wide CPR suggests a range day.
- **Rules (common retail form):**
  - Timeframe: 5-/15-min.
  - CPR width % = |TC − BC| / P × 100. Sources call it "narrow" below about 0.1–0.2% for Nifty and "wide" above about 0.5%. An adaptive version compares width against its 20-day percentile.
  - Narrow-CPR breakout: long on a 15-min close above TC (and above the previous day's high, optionally). Target R1, then R2. SL below BC, or the breakout candle low. Short mirrors this below BC toward S1/S2.
  - Wide-CPR mean reversion: fade moves at R1/S1 back toward P.
  - Virgin CPR, inside/outside CPR (relative to the previous day) and 2-day relationships are used as bias filters.
- **Popularity: Very high.**
  - [Groww CPR guide](https://groww.in/blog/central-pivot-range)
  - [OptionX CPR explained](https://optionx.trade/blogs/central-pivot-range-cpr-explained)
  - [YouTube "The only CPR video you will ever need"](https://www.youtube.com/watch?v=Ji1HM6SJJYY)
  - Instagram reels
  - Pivots are covered in Zerodha Varsity.
- **Claimed win rate:** Not stated with methodology. [TradingDirection's fixed vs adaptive narrow-CPR test](https://www.tradingdirection.in/blog/narrow-cpr-trading-strategy-for-nifty-fixed-threshold-vs-adaptive-which-works) concludes that **"a narrow CPR does not predict direction"**. It may predict range expansion only.
- **Red flags.** Examples are almost always picked after the fact (for example, Groww's narrow-CPR gap-down day).

### 7. CPR + VWAP confluence → new ID `cpr_vwap`

- **Description.** A widespread YouTube combination. The CPR sets the day's bias, VWAP gives the entry, and pivots give the targets.
- **Rules:**
  - Bias long when price opens or holds above TC **and** above VWAP.
  - Entry on a 5-min pullback to VWAP or TC (whichever is higher) with a bullish close.
  - SL below BC, or below VWAP − 0.5×ATR.
  - Target R1/R2.
  - Only on narrow or normal CPR days. Short mirrors this.
  - Entries 9:30–13:30.
- **Popularity: Medium–High.** This is a common pairing in CPR videos (see the CPR sources above). OptionX discusses CPR breaks together with volume and VWAP.
- **Claimed win rate:** None found.
- **Mapping.** Kept as a distinct ID because it requires both conditions. It reuses the `cpr_pivot` level calculation and the `vwap_pullback` trigger.

### 8. Volatility squeeze breakout (TTM / BB-inside-KC) → `squeeze_breakout`

- **Description.** John Carter's TTM Squeeze. When Bollinger Bands contract inside Keltner Channels, volatility is compressed. Trade the "fire" in the direction of momentum.
- **Rules:**
  - Squeeze ON: BB(20, 2.0) upper < KC(20, 1.5×ATR) upper **and** BB lower > KC lower.
  - Fire: the first bar where the squeeze turns OFF after at least N (≥5–6) consecutive ON bars.
  - Direction: the sign and slope of the momentum histogram, i.e. linreg(close − avg(avg(HH,LL), SMA20), 20).
  - SL: the opposite side of the squeeze range.
  - Exit: when the histogram loses momentum (two declining bars) or the price closes back inside the KC.
  - Timeframe: 5-/15-min.
  - Squeeze Pro variants use KC multipliers of 1.0, 1.5 and 2.0 for "high/medium/low" compression.
- **Popularity: High** (globally popular; widely used on Bank Nifty charts via TradingView).
  - [TTM Squeeze Pro (Beardy_Fred)](https://www.tradingview.com/script/0drMdHsO-TTM-Squeeze-Pro/)
  - [Squeeze Range BB/KC (Whvntr)](https://www.tradingview.com/script/MkDw5E2T-Squeeze-Range-Bollinger-Bands-Keltner-Channels-Whvntr/)
  - [TrendSpider explainer](https://trendspider.com/learning-center/introduction-to-ttm-squeeze/)
- **Claimed win rate:** None India-specific.
- **Red flags.** The direction of the fire is ambiguous, and there are many false fires around 9:15–9:45 because ATR is distorted by the open.

### 9. RSI(14) divergence reversal → `rsi_divergence`

- **Description.** A regular bearish divergence is price making a higher high while RSI makes a lower high. A regular bullish divergence is price making a lower low while RSI makes a higher low. Confirm with a candle pattern or a break of structure.
- **Rules (Upstox intraday form):**
  - Timeframe: 3-/5-min on Nifty / Bank Nifty spot.
  - Swing pivots detected with left/right = 3–5 bars. Compare the last two pivots within a 5–60 bar lookback.
  - RSI at the first pivot should be ≥ 70 (bearish) or ≤ 30 (bullish). Stricter versions use 60/40.
  - Entry on a confirmation candle (engulfing, or a close beyond the prior candle's low/high).
  - SL beyond the divergence swing extreme.
  - Target VWAP, the opposite pivot level, or 1.5–2R.
  - Hidden divergence (continuation) is optional.
- **Popularity: High.**
  - Upstox: [negative divergence](https://upstox.com/market-talk/intraday-rsi-negative-divergence-strategy/) and [positive divergence](https://upstox.com/?p=22446)
  - [Marketcalls AFL](https://www.marketcalls.in/amibroker/rsi-divergence-indicator-optimized-afl-code.html)
  - [TradingView RSI-14 divergence scripts](https://in.tradingview.com/script/tjQ7jMcv-RSI-14-Divergence-Enhanced-Hidden-Detection-v6-exterm-hid)
- **Claimed win rate (claimed, unverified):** Marketcalls claims **">75% for Nifty"**. A reader comment notes the code is an **EOD scanner, not intraday**.
- **Red flags.** Divergence often repeats 2–3 times in a strong trend before the reversal comes. Pivot confirmation needs right-side bars, which creates look-ahead risk in naive backtests.

### 10. Open Interest / PCR bias → `oi_pcr`

- **Description.** Use the option chain (PCR, highest call/put OI strikes, change in OI, max pain) to set a directional bias or support/resistance. This is mostly a **filter**, not a standalone trigger.
- **Rules (as taught):**
  - PCR = total put OI / total call OI (current weekly expiry for Nifty).
  - Zerodha Varsity reads PCR contrarian at extremes: **PCR > 1.3 means oversold, so look for an upside reversal**; **PCR < 0.5 (sometimes < 0.7) means overbought**.
  - Intraday traders often read it the other way, as momentum: rising PCR with fresh put writing is bullish. IndexEdge should treat PCR **change** and PCR **extremes** as separate features.
  - Support = the strike with max put OI. Resistance = the strike with max call OI.
  - Entry: a price trigger (for example VWAP or ORB) only when the OI bias agrees.
- **Popularity: Very high.**
  - [Zerodha Varsity: Max Pain & PCR](https://zerodha.com/varsity/chapter/max-pain-pcr-ratio/)
  - Sensibull [live OI/PCR charts](https://web.sensibull.com/live-options-charts?tradingsymbol=NIFTY)
  - [Opstra (Definedge)](https://opstra.definedgesecurities.com/)
  - [ArthaLearn PCR](https://www.arthalearn.com/learn/put-call-ratio)
- **Claimed win rate:** None credible.
- **Red flags.**
  - Varsity readers noted that Sensibull at one point displayed the ratio in a different convention.
  - OI data updates only every few minutes, and historical intraday OI is hard to get, which makes backtests hard.
  - The contrarian and momentum interpretations directly conflict.

### 11. 9:20 AM short straddle → new ID `straddle_920`

- **Description.** The best-known Indian algo strategy. Sell the ATM CE and ATM PE at 9:20, with a fixed percentage SL on each leg, and exit at a fixed time.
- **Rules (canonical):**
  - Instrument: index options, current weekly expiry. Historically Bank Nifty; now Nifty, since Bank Nifty is monthly only.
  - At 9:20:00, sell 1 lot ATM CE + 1 lot ATM PE. ATM = the strike nearest spot (some use the synthetic futures price).
  - SL on each leg independently at **25% (some use 30%, 35% or 50%) above that leg's entry premium**.
  - Optional: when one leg stops out, move the other leg's SL to cost.
  - Exit both legs at 15:15 (some use 15:20 or 14:59).
  - No target.
  - Common variants: entry at 9:16, or after 10:00. Re-entry after SL. Skipping expiry day.
- **Popularity: Very high.**
  - Zerodha video ["Seven Years of the 9:20 Straddle"](https://www.youtube.com/watch?v=g_vbR6oHz3s)
  - [TradingQnA/Scribd analysis "Is it stopped working?"](https://www.scribd.com/document/626700540/Detailed-analysis-of-920-Short-Straddle-Strategy-Is-it-stopped-working-Algos-strategies-code-Trading-Q-A-by-Zerodha-All-your-queries-on-trad)
  - [Moneycontrol backtest](https://www.moneycontrol.com/news/business/markets/making-losses-on-9-20-short-straddle-backtest-suggests-adjustments-to-improve-strategy-11896461.html)
  - [Medium "Optimized 920 straddle"](https://medium.com/@amit179.iitk2/optimized-920-straddle-strategy-to-get-more-than-80-return-annually-e977453dca80)
  - Ships as a template on AlgoTest, Stockmock, Tradetron and Quantiply.
- **Claimed win rate / returns (claimed, unverified):**
  - About **50% win rate**; "losses are fewer but…" ([LinkedIn, Nifty/Bank Nifty 2019–2024](https://www.linkedin.com/pulse/920-short-straddle-truth-accuracy-right-way-trade-nishant-kumar-m6gwc)).
  - **65–75% win rate with a 25% SL, but average loss 2–3× average win** ([Quintalmind](https://www.quintalmind.com/blog/time-based-straddle-strategy-nifty-options)).
  - **">80% return annually"** for an "optimized" version ([Medium](https://medium.com/@amit179.iitk2/optimized-920-straddle-strategy-to-get-more-than-80-return-annually-e977453dca80)). This is a parameter-fit result.
  - Moneycontrol/StocksEmoji (Bank Nifty, Oct 2018 to Jan 2022, 0.5% slippage): profitable until end-2020, then **a 3-month drawdown (Feb–Apr 2021) and a 105-day drawdown (Aug 2021 to Jan 2022)**.
- **Red flags.** Edge decay as the strategy got crowded. Gap and event risk. Results depend heavily on SL fill slippage. The Bank Nifty weekly history no longer applies. It needs large margin, or a hedged iron-fly version.

### 12. Expiry-day straddle / iron fly → `expiry_straddle`

- **Description.** Sell the ATM straddle (or a hedged iron fly) on expiry day to capture the rapid theta decay into the close.
- **Rules (common):**
  - Instrument: Nifty weekly options on expiry day (Tuesday per current reporting; verify against the NSE calendar).
  - Entry: after the open settles, typically 9:30–10:30. Some enter at 13:00–14:00 for "last-hours decay".
  - Position: ATM CE + PE short. An iron fly adds long wings at about ±1–1.5× the straddle premium, or ±200–300 Nifty points.
  - SL: a combined-premium SL of 20–30% over the entry straddle premium, **or** per-leg 30–40%. Hard exit on spot breaking ± the straddle premium from the strike.
  - Exit by 15:15. No overnight risk.
- **Popularity: High.**
  - [AlgoTest: Nifty expiry day rules](https://algotest.in/blog/nifty-expiry-day.md)
  - [AlgoTest: Bank Nifty expiry day](https://algotest.in/blog/bank-nifty-expiry-day.md)
  - [TradingView expiry short straddle / iron fly discussion](https://il.tradingview.com/chart/NIFTY/GMWXeRtB-NIFTY-Weekly-expiry-discussion-Short-straddle-Iron-Fly)
  - [TradingQnA iron condor test](https://tradingqna.com/t/is-iron-condor-a-profitable-option-trading-strategy/81708)
- **Claimed win rate (claimed, unverified):**
  - Quintalmind's 9:20 straddle **on expiry day: about 65–70% with a 25% SL; average loss 2–3× average win**.
  - [PL India](https://www.plindia.com/?p=21074) claims options lose "70–80% of value between 12 PM and 3:30 PM" (broker marketing).
  - A Stockmock-based Bank Nifty iron condor test (2017–2020) showed only a small profit that "will give negative results after transaction costs".
- **Red flags.**
  - Gamma risk: expiry-day moves of 1–2% wipe out weeks of gains.
  - SEBI's expiry-day rules (higher ELM on expiry day, no calendar-spread margin benefit on expiry day) change the economics.

### 13. Opening gap fill / gap fade → `gap_fill`

- **Description.** When the index opens with a gap against the previous close, fade the gap back toward the previous close. Alternatively, trade "gap-and-go" when the gap holds.
- **Rules (common):**
  - Gap % = (open − prev close) / prev close × 100. Trade only when |gap| ≥ 0.3–0.5% for Nifty, and ≥ 0.4–0.6% for Bank Nifty.
  - Fade entry (gap-up): wait for the first 15-min candle. Short on a 5-min close below the first-candle low, or below VWAP.
  - Target: the previous day's close (the gap fill). Partial at 50% of the gap.
  - SL: the day's high so far.
  - Mirror the rules for gap-down.
  - Skip when |gap| > about 1.5% (event-driven gaps fill less often).
  - Time filter: entries 9:30–11:00. Exit by 15:15.
- **Popularity: High.**
  - [OptionX Nifty/Bank Nifty gap fill](https://optionx.trade/blogs/nifty-bank-nifty-gap-fill-strategy)
  - [TrueData gap strategy](https://www.truedata.in/blog/Gap-up-and-gap-down-intraday-trading-strategy)
  - [Enrich Money](https://enrichmoney.in/knowledge-center-chapter/gap-trading-strategy)
  - [TradingQnA gap filling statistics](https://tradingqna.com/t/gap-filling-statistics/151053)
- **Claimed fill rates (claimed, unverified; useful as base rates):**
  - TradingQnA user study: **44% of gap-ups and 24% of gap-downs filled the same day**.
  - OptionX: same-day fill rates of "around 40–50% for gap-ups" and "20–30% for gap-downs" (rough, self-described as estimates).
  - [IntradayLab, 10 years](https://intradaylab.com/blog/nifty-gap-up-history-analysis): **only 29.4% of Nifty gap-ups ≥ 1% filled the same day (25 of 85 events)**. Gaps above 2% filled in 25% of cases. 55.3% of ≥1% gap-up days closed below the open.
- **Red flags.** "Gaps always fill" is contradicted by the data for larger gaps. Small samples for large gaps.

### 14. Heikin Ashi trend (flat-bottom candles) → proposed ID `heikin_ashi_trend` (deferred)

- **Description.** Ride runs of same-colour Heikin Ashi (HA) candles.
- **Rules:**
  - Timeframe: 5-/15-min.
  - Long (buy CE) after 2 consecutive green HA candles with **no lower wick** (HA open == HA low, within tolerance).
  - Exit on the first red HA candle, or the first green candle with a lower wick.
  - Short mirrors this with no upper wick.
  - Often combined with Supertrend or a 20 EMA.
- **Popularity: Medium–High.**
  - [OneTradeJournal: HA trend strategy for Indian markets](https://onetradejournal.com/strategies/heikin-ashi-trend-strategy)
  - [OneTradeJournal: HA indicator page](https://onetradejournal.com/indicators/heikin-ashi)
  - [TradingView HA no-wick scripts](https://it.tradingview.com/script/nInq2RU4-Heikin-Ashi-No-Wick-Consecutive-Candles)
- **Claimed win rate:** None found.
- **Red flags.** HA prices are synthetic. Fills happen at the real LTP, and backtests on HA closes are optimistic. HA lags real price by 1–2 bars.
- **Recommendation.** Do not ship as a standalone strategy. It can be added later as a trend filter.

### 15. Inside bar breakout → new ID `inside_bar_breakout`

- **Description.** An inside bar has its high/low entirely within the previous ("mother") bar. Trade the break of the mother bar.
- **Rules:**
  - Timeframe: 15-min (the 5-min trigger is common).
  - Inside bar: H < mother H and L > mother L.
  - Entry: long on a close (or stop) above the mother high; short below the mother low.
  - SL: the opposite side of the mother bar, or the inside bar's opposite side (tighter).
  - Target: 1:2.
  - Skip if the mother range is > 1.2×ATR(14) on that timeframe.
  - Entries 9:45–14:00. Avoid major events.
- **Popularity: Medium–High.**
  - [TradingQnA: inside bar strategy in Zerodha Streak (Nifty ATM options, 15-min)](https://tradingqna.com/t/inside-bar-strategy-setting-in-streak-zerodha/140367)
  - Numerous TradingView Nifty breakout ideas (for example [this one](https://in.tradingview.com/chart/NIFTY/eJz8kuyX-Nifty-Trading-Strategy-for-27th-June-2025))
- **Claimed win rate:** None found.

---

## Summary table

| # | Strategy | IndexEdge ID | Popularity | Claimed win rate / stat (source), all claimed, unverified | Notes |
|---|---|---|---|---|---|
| 1 | Opening Range Breakout | `orb15` | Very high | Long ~60%, short ~50%, no costs (Zerodha In The Money, 2019–Feb 2026) | Regime-dependent; wide stops on gap days |
| 2 | First 5/15-min candle high-low | `orb15` (variant) | Very high | None with methodology; YouTube titles imply 70–90% | Same engine as ORB |
| 3 | VWAP pullback | `vwap_pullback` | High | None credible | Needs futures volume for VWAP |
| 4 | EMA + Supertrend(10,3) + ADX | `ema_supertrend_adx` | Very high | 46% / 57% Supertrend + 200MA, 1.5R (TradingRush, non-India) | Whipsaws in ranges; ADX gate essential |
| 5 | Triple Supertrend | `triple_supertrend` (new) | Medium–High | None | Exit on any flip |
| 6 | CPR / floor pivots | `cpr_pivot` | Very high | None; TradingDirection: narrow CPR "does not predict direction" | Hindsight examples common |
| 7 | CPR + VWAP confluence | `cpr_vwap` (new) | Medium–High | None | Reuses CPR + VWAP modules |
| 8 | TTM squeeze | `squeeze_breakout` | High | None India-specific | False fires near the open |
| 9 | RSI divergence | `rsi_divergence` | High | ">75% Nifty" (Marketcalls; EOD scanner, not intraday) | Pivot look-ahead risk |
| 10 | OI / PCR bias | `oi_pcr` | Very high | None | Filter, not trigger; contrarian vs momentum conflict |
| 11 | 9:20 short straddle | `straddle_920` (new) | Very high | ~50% (LinkedIn); 65–75% with avg loss 2–3× win (Quintalmind); ">80%/yr" (Medium); 105-day DD 2021–22 (Moneycontrol) | Crowded; Bank Nifty weekly gone |
| 12 | Expiry-day straddle / iron fly | `expiry_straddle` | High | 65–70% with 25% SL, loss 2–3× win (Quintalmind); IC negative after costs (TradingQnA) | Gamma/event risk; Nifty weekly only |
| 13 | Gap fill / fade | `gap_fill` | High | Same-day fill 44% up / 24% down (TradingQnA); 29.4% for ≥1% gap-ups, n=85 (IntradayLab) | Big gaps rarely fill |
| 14 | Heikin Ashi trend | `heikin_ashi_trend` (deferred) | Medium–High | None | Synthetic prices; filter only |
| 15 | Inside bar breakout | `inside_bar_breakout` (new) | Medium–High | None | Skip oversized mother bars |

---

## Selection for implementation

All times are IST. The signal runs on the index spot (or near-month futures where volume/VWAP is needed). The default execution instrument is near-month futures for direction trades and the current Nifty weekly options for the premium-selling strategies. Every strategy squares off by **15:15**. Costs are always applied: brokerage, STT, exchange/SEBI fees, GST, stamp duty, plus slippage of 1 tick + 0.05% for futures and 0.5% of premium for options. Statistics are shown only from walk-forward, out-of-sample runs.

1. **`orb15`**
   - Range 9:15–9:30 (`range_minutes=15`; variant 5 = first-candle mode). Signal timeframe 5-min.
   - Entry on a candle **close** beyond the range high/low ± buffer of 0.05% (`entry_mode=close`; alternative `stop`).
   - Volume confirmation: breakout-candle futures volume ≥ **1.5× the 20-bar average**.
   - SL at the opposite end of the range, capped at **1.0×ATR(14, 5-min)×3**. If the range width is > 0.8% (Nifty) or > 1.0% (Bank Nifty), skip the day.
   - Target 2R, with a 50% partial at 1R and the trail moved to breakeven.
   - Entries until 11:30. Maximum 1 trade per direction per day.
2. **`vwap_pullback`**
   - 5-min. Session VWAP from futures. Trend gate: close above VWAP for ≥ 70% of the bars since 9:15 **and** EMA(9) > EMA(21) (mirror for short).
   - Pullback: low ≤ VWAP + 0.1×ATR(14). Trigger: the candle closes back above VWAP, with a bullish body ≥ 40% of the range.
   - SL: the pullback swing low − 0.1×ATR, or VWAP − 0.5×ATR(14) (whichever is tighter but ≥ 0.3×ATR).
   - Target 2R. Trail by the prior candle low once at 1R.
   - Window 9:45–14:30. Maximum 2 trades per day.
3. **`ema_supertrend_adx`**
   - 5-min. EMA(9)/EMA(21) cross, or fast-above-slow state. Close beyond **Supertrend(10, 3)**. **ADX(14) > 22** (configurable 20–25), with +DI > −DI for long (−DI > +DI for short).
   - Optional EMA(200) bias filter (on by default).
   - SL: the Supertrend line at entry. Exit on Supertrend flip or a 2.5R target.
   - Window 9:30–14:45.
4. **`triple_supertrend`**
   - 5-min. Supertrends **(10, 1), (11, 2), (12, 3)**. Enter when all three agree on a closed bar (first bar of agreement only).
   - Exit when any one flips, or at 15:00.
   - SL: the ST(12,3) line.
   - Filter: ADX(14) > 20.
   - Window 9:30–14:30.
5. **`cpr_pivot`**
   - Daily P = (H+L+C)/3, BC = (H+L)/2, TC = 2P − BC (ordered). Width% = (TC − BC)/P × 100.
   - Narrow if width% < **0.15** (Nifty) / **0.20** (Bank Nifty), **or** below the 20-day 25th percentile. Wide if width% > **0.50** **or** above the 20-day 75th percentile.
   - Narrow day: breakout mode. 15-min close above TC (below BC); target R1 then R2 (S1/S2); SL at BC (TC) or the signal-candle extreme.
   - Wide day: reversion mode. Fade the first touch of R1/S1 with a 5-min rejection close; target P; SL 0.5×ATR(14, 15-min) beyond the level.
   - Window 9:30–14:00.
6. **`cpr_vwap`**
   - Bias long only if the 9:30 price > TC **and** > VWAP (short: < BC and < VWAP).
   - Only on narrow or normal CPR days (width% ≤ 0.35).
   - Entry: a 5-min pullback to max(VWAP, TC), then a close back in the trend direction.
   - SL: below min(BC, VWAP − 0.5×ATR(14)).
   - Targets R1 / R2.
   - Window 9:30–13:30. Maximum 1 trade per day.
7. **`squeeze_breakout`**
   - 15-min (a 5-min option is available). Squeeze ON when **BB(20, 2.0) is fully inside KC(20, 1.5×ATR(20))**. Requires ≥ **6** consecutive ON bars.
   - Fire = the first OFF bar. Direction from the momentum histogram, linreg(close − mean(mean(HH20, LL20), SMA20), 20), > 0 and rising (long) or < 0 and falling (short).
   - Ignore fires before 9:45.
   - SL: the opposite side of the squeeze-period range, or 1.5×ATR(14) (whichever is tighter).
   - Exit after 2 consecutive bars of decreasing histogram magnitude, or at 3R.
8. **`rsi_divergence`**
   - 5-min. **RSI(14)**. Swing pivots use left = 3, right = 3. The right-side confirmation lag is enforced, so there is no look-ahead.
   - Compare the latest two pivots **5–40 bars apart** (lookback 40).
   - Regular bearish: price higher high, RSI lower high, first RSI pivot ≥ **65**. Bullish mirrors this with ≤ **35**.
   - Trigger: the next candle closes beyond the low/high of the second-pivot bar.
   - SL: the divergence extreme + 0.1×ATR(14).
   - Target: VWAP, or 2R (whichever is first). Trail at 1R.
   - Window 9:45–14:30.
9. **`oi_pcr`** (bias filter plus an optional standalone)
   - PCR = ΣPut OI / ΣCall OI, current Nifty weekly expiry, ATM ± 10 strikes. Snapshots every 3–5 min.
   - Contrarian extremes: **PCR ≥ 1.30 = oversold (long bias)**; **PCR ≤ 0.70 = overbought (short bias)**. Neutral is 0.8–1.2.
   - Momentum mode: ΔPCR over 30 min ≥ +0.15 with rising put OI at or below ATM = bullish (mirror for bearish).
   - Support/resistance = the max put-OI / max call-OI strikes.
   - Standalone trade only when the OI bias agrees with a 5-min close beyond VWAP. SL 0.6×ATR(14) beyond VWAP. Target the nearest max-OI wall.
   - **Paper-only until IndexEdge has ≥ 1 year of its own intraday OI history.**
10. **`straddle_920`**
    - Nifty current weekly expiry. At **9:20:00**, sell ATM CE + ATM PE. ATM = the strike nearest the synthetic futures price.
    - Per-leg SL **25%** of that leg's entry premium (configurable 25–30%), executed as SL-limit with a 2% buffer.
    - After one leg stops out, move the surviving leg's SL to its entry price (on by default).
    - Hard exit at 15:15. No re-entry.
    - Skip if VIX-proxy/ATM IV jumped > 15% intraday by 9:20, or on scheduled event days (RBI policy, Budget, election results).
    - Optional hedge wings ±2% OTM to cut margin (iron-fly mode).
11. **`expiry_straddle`**
    - Nifty weekly options **on expiry day only**, using the NSE calendar (currently Tuesday).
    - Entry at **10:00** (configurable 9:30–13:30): sell ATM CE + PE.
    - Mandatory wings at ±1.0× the combined straddle premium (rounded to strike), forming an iron fly.
    - SL: combined premium **+25%** over entry, **or** spot moving beyond strike ± 0.8× the entry straddle premium.
    - Profit lock: once the combined MTM reaches +30%, trail the SL to +10%.
    - Exit at 15:15.
12. **`gap_fill`**
    - Gap% = (open − prev close)/prev close. Trade only when **0.30% ≤ |gap| ≤ 1.50%** (Nifty), **0.40–1.80%** (Bank Nifty).
    - Fade mode (default): after the 9:15–9:30 candle, fade on a 5-min close back through the first-candle low (gap-up) or high (gap-down) **and** through VWAP.
    - Target: the previous close (full fill), with a 50% partial at half the gap.
    - SL: the day's extreme so far + 0.1×ATR(14).
    - Entries 9:30–11:00. Exit 15:15.
    - Gap-and-go mode (opposite) is selected automatically when the first 15-min candle closes in the gap direction with range > 1.2× the 10-day average first-candle range.
13. **`inside_bar_breakout`**
    - 15-min mother/inside detection (inside: H < mother H and L > mother L). The trigger is a 5-min close beyond the mother high/low ± 0.03%.
    - Skip if the mother range > **1.2×ATR(14, 15-min)** or < 0.3×ATR.
    - SL: the opposite side of the inside bar (tight), falling back to the mother bar.
    - Target 2R.
    - Entries 9:45–14:00. Maximum 2 trades per day.

**Deferred / folded:** `first_candle_high_low` is folded into `orb15` (`range_minutes=5|15`). `heikin_ashi_trend` is deferred: it will be available as an optional trend filter (2 consecutive no-opposite-wick HA candles, 5-min), not as a standalone strategy, because HA prices are synthetic.

### Global risk defaults (all strategies)

- Risk per trade 0.5% of capital (configurable). Daily loss cap 1.5%. Maximum 4 trades per day across all strategies.
- No new entries 15 min either side of scheduled macro events.
- Every reported statistic includes: number of trades, win rate, average win / average loss, profit factor, expectancy in R and ₹ after costs, maximum drawdown, and the out-of-sample period. Results with fewer than **100 out-of-sample trades** are flagged "insufficient sample".

---

## Iteration 2 strategies

Survey date: 16 October 2026. Same honesty rules as above: every win-rate figure quoted from a source is **claimed, unverified**. The sources are broker education portals, vendor scripts and community pages. None of them published an audited, out-of-sample, after-cost record for Nifty or Bank Nifty. The rule sets below are the **common core** across sources. IndexEdge defaults are listed separately, and every one of them is a parameter.

General findings:
- **Pullback trading** is the most-taught continuation idea in Indian retail education. The common recipe has three parts: a trend filter (EMAs or Supertrend), a "value zone" (EMA 20, a Fibonacci 38.2–61.8% retracement, VWAP or the Supertrend line) and a **trigger candle** (rejection, engulfing, or a close back in the trend direction). Sources: [Kotak Neo StockShaala: EMA pullback swing](https://www.kotakneo.com/stockshaala/swing-trading-strategies/ema-pullback-swing-strategy/), [Kotak Neo: EMA pullback scalping](https://www.kotakneo.com/stockshaala/scalping-trading-strategies/ema-pullback-scalping/), [OneTradeJournal: Pullback trading strategy in Indian markets](https://onetradejournal.com/strategies/pullback-trading-strategy).
- Sources differ on EMA pairs: 9/20 for intraday scalping, 20/50 for swing trading. None gave a Nifty-specific backtest.
- The common caveat is to stay out when the EMAs are flat or tangled, which describes range days.

### 16. EMA pullback → `ema_pullback`
- **Common rules:**
   - Trend: EMA 20 above EMA 50, with price above EMA 200 or a rising ADX as the trend-health check.
   - Pullback: price returns to the EMA 20 zone without breaking EMA 50.
   - Entry: a rejection candle that closes back in the trend direction, often entered on a break of that candle's high.
   - Stop: below the pullback swing low, not below the EMA.
   - Exits: a partial at the prior swing or 1R, then trail the rest or exit at 2R.
- **Sources:** [Kotak Neo swing](https://www.kotakneo.com/stockshaala/swing-trading-strategies/ema-pullback-swing-strategy/), [Kotak Neo scalping](https://www.kotakneo.com/stockshaala/scalping-trading-strategies/ema-pullback-scalping/) (micro-swing stop, volume pickup on the resumption candle), [NinjaTrader forum "pullback to 20 EMA"](https://forum.ninjatrader.com/forum/ninjatrader-8/strategy-development/1111258-pullback-to-20-ema-strategy).
- **Claimed win rates:** none quoted with data.
- **IndexEdge defaults:**
   - Trend: EMA 21 > EMA 50, close > EMA 200 and ADX(14) ≥ 18. EMA 21 stands in for EMA 20.
   - Touch: within the last 3 bars, price came within EMA 21 + 0.15×ATR without breaking EMA 50 − 0.25×ATR.
   - Trigger: a bullish candle that closes above EMA 21, in the top 40% of its range.
   - Stop: the pullback extreme − 0.1×ATR.
   - Targets: 1R (partial, then breakeven) and 2R.
   - Entries 09:45–14:45, maximum 2 per day.

### 17. Fibonacci retracement pullback → `fib_pullback`
- **Common rules:**
   - Draw the retracement on a clear impulse leg (swing low to swing high).
   - Wait for a 38.2–61.8% pullback, with 61.8% as the "golden" level.
   - Enter on a candlestick confirmation at the level.
   - Stop: below the swing, or below 78.6%.
   - Target: the prior swing extreme, or the 1.272 / 1.618 extension.
- **Sources:** [Upstox: Fibonacci intraday strategy](https://upstox.com/market-talk/intraday-3-mins-fibonacci-retracement-short-strategy/), [Jainam: Fibonacci retracement entry points](https://www.jainam.in/blog/blog-fibonacci-retracement-trading/) (Nifty example: 61.8% entry, stop below the swing low, 1.618 target), [Shriram Finance](https://www.shriramfinance.in/investments/fibonacci-retracement) (confirmations: candle pattern, RSI turn, volume), [Zerodha Varsity (Hindi) Fibonacci chapter](https://zerodha.com/varsity/chapter/%E0%A4%AB%E0%A4%BF%E0%A4%AC%E0%A5%8B%E0%A4%A8%E0%A4%BE%E0%A4%9A%E0%A5%80-%E0%A4%B0%E0%A5%80%E0%A4%9F%E0%A5%8D%E0%A4%B0%E0%A5%87%E0%A4%B8%E0%A4%AE%E0%A5%87%E0%A4%82%E0%A4%9F%E0%A5%8D%E0%A4%B8-fibonacc/), [StockSharp Fibonacci reversal](https://stocksharp.com/store/strategies.0076_fibonacci_retracement_reversal/) (5-minute bars, 20-bar swing lookback).
- **Claimed win rates:** none quoted with data.
- **Main hindsight risk:** the levels are drawn after the fact. IndexEdge therefore uses only swings **confirmed** 3 bars after the pivot.
- **IndexEdge defaults:**
   - Leg: today's last confirmed swing low → swing high, at least 1.5×ATR.
   - Zone: the deepest retracement since the high must sit inside 38.2–61.8%. A break of 78.6% cancels the leg.
   - Trigger: a reversal close above the prior bar's high.
   - Stop: 78.6% − 0.1×ATR.
   - Targets: the swing high and the 1.272 extension.
   - One trade per leg, maximum 2 per day.

### 18. Supertrend pullback → `supertrend_pullback`
- **Common rules:**
   - Supertrend(10, 3) is the standard NSE setting. It is used as a trend filter, not as a standalone entry.
   - Confirm on the candle close, because 5-minute Bank Nifty wicks pierce the line.
   - Buy dips toward the line while the trend holds, with a trigger candle.
   - Stop: just beyond the line.
- **Sources:** [OneTradeJournal: Supertrend on Bank Nifty and Nifty](https://onetradejournal.com/learn/how-to-use-supertrend-indicator), [Kotak Neo: Supertrend indicator](https://www.kotakneo.com/stockshaala/swing-trading-strategies/the-supertrend-indicator/), [MarketCalls: Supertrend intraday](https://www.marketcalls.in/amibroker/supertrend-intraday-trading-strategy-high-volatile-scrips.html) (the author warns Supertrend is a carry-forward tool that needs intraday exits, and recommends 2–3 years of backtest), [TradingQnA Supertrend thread](https://tradingqna.com/t/supertrend-strategy-creation/146250).
- **Claimed win rates:** none with data.
- **IndexEdge defaults:**
   - Trend: ST direction held for at least 6 bars, and close on the trend side of EMA 50.
   - Pullback: within the last 3 bars, price came within 0.6×ATR of the line.
   - Resume: the candle closes beyond the previous close and at least 0.3×ATR clear of the line.
   - Stop: line − 0.1×ATR.
   - Targets: 1R and 2R.

### 19. Breakout + retest (PDH/PDL, 15-minute opening range) → `breakout_retest`
- **Common rules:**
   - Mark the key levels: previous-day high/low, and the 09:15–09:30 opening range for Indian indices.
   - Wait for the break, then **enter on the retest**, not on the first break.
   - Confirm the hold with a rejection wick or a close back beyond the level.
   - Stop: just beyond the retest structure.
   - Target: the next intraday swing, or partials.
   - Some versions treat the zone between the previous-day high and the premarket low as chop.
- **Sources:** [TradeZella: Break & retest playbook](https://www.tradezella.com/playbooks/break-retest), [LuxAlgo: Previous Day High and Low Breakout](https://www.luxalgo.com/library/indicator/dn6ogsKO-previous-day-high-and-low-breakout-strategy/) (plain PDH/PDL break, no retest), [TradingView India Nifty scripts](https://cn.tradingview.com/scripts/nifty/page-2) (first-15-minute range combined with previous-day levels; backtest claims are claimed, unverified), [StockSharp "Breaks and Retests"](https://doc.stocksharp.com/en/api-examples/0563_Breaks_and_Retests).
- **Claimed win rates:** none for Nifty.
- **IndexEdge defaults:**
   - Break: a close at least 0.1×ATR beyond the level, with the previous close on the other side.
   - Retest: within 12 bars, price comes within 0.15×ATR of the level and closes back beyond it with a bullish (bearish) candle.
   - Cancel the setup on a close more than 0.25×ATR back through the level.
   - Stop: the level (or the retest extreme) − 0.25×ATR.
   - Targets: 1R and 2R.
   - One trade per level per day.
   - Gap opens beyond the level have no break candle and are skipped.

### 20. Bollinger Band mean reversion (range days) → `bollinger_reversion`
- **Common rules:**
   - Bands: BB(20, 2).
   - Trade only in sideways or range conditions. Some versions use an ADX/DMI filter, for example |DI+ − DI−| < 20.
   - Fade a close outside a band once price closes back inside. Some versions add an RSI extreme.
   - Target: the middle band.
   - Stop: beyond the extreme, or ATR-based.
   - Use breakout logic on trend days and mean reversion on flat days.
- **Sources:** [AlgoTest: 7 Bollinger Bands strategies for Indian traders](https://algotest.in/blog/bollinger-bands-strategies-for-indian-traders.md), [Sahi: Bollinger scalping, squeeze and mean reversion](https://www.sahi.com/blogs/bollinger-bands-scalping-squeeze-breakouts-and-mean-reversion-setups), [TradingView: BB mean reversion with RSI](https://www.tradingview.com/script/XRPeqEdA-Bollinger-Bands-Mean-Reversion-using-RSI-Krishna-Peri), [LuxAlgo: Sideways strategy DMI + BB](https://www.luxalgo.com/library/indicator/IMfJIbbG-sideways-strategy-dmi-bollinger-bands/), [Alice Blue](https://aliceblueonline.com/blog/how-to-use-bollinger-bands-for-stock-trading-in-india).
- **Claimed win rates:** none for Nifty.
- **IndexEdge defaults:**
   - The IndexEdge regime must read `range` on the signal bar.
   - Setup: a close outside the band within the last 3 bars, then a re-entry close inside with a candle in the trade direction.
   - Stop: the extreme ± 0.1×ATR.
   - Target: the middle band. Skip if it is less than 0.8R away.

### 21. MACD + EMA 200 trend → `macd_trend`
- **Common rules:**
   - MACD(12, 26, 9) crosses its signal line on the side of the EMA 200: longs above it, shorts below.
   - Classic variant: long crosses must occur below the zero line.
   - Stop: the recent swing.
   - Target: 1–2R.
- **Sources:** [TradingView "Simple MACD strategy"](https://in.tradingview.com/script/yF94AKKh-Simple-MACD-strategy/), [Backtrex MACD + 200 EMA test](https://backtrex.com/en/backtests/macd-200-ema-usd-jpy).
- **Claimed win rates:**
   - TradingView script: ~62% with a 1:1 2%/2% target and stop, no sample size or market given. **Claimed, unverified.**
   - Backtrex: a 10-year USD/JPY 4-hour test (not Nifty) reported a 32.7% win rate, profit factor 0.78 and −22.1% over 385 trades. **Claimed, unverified.** This counter-example shows the combo is not a reliable edge on its own.
- **IndexEdge defaults:**
   - Entry: a cross on this bar on the EMA 200 side, with the histogram expanding for 2 bars.
   - Zero-line filter: optional, off by default.
   - Stop: the 10-bar swing − 0.1×ATR. Skip if the stop is more than 3×ATR away.
   - Target: 2R.

### Considered, not added
- **Heikin Ashi trend**: still deferred, for the reasons in §14 (synthetic prices).
- **RSI-50 / Stochastic pullback**: popular, but largely the same as `ema_pullback` with a different oscillator trigger. It is better added later as an optional filter than as a separate ID.

### Iteration 2 implementation notes
- All six strategies share these settings:
   - Entries no later than 14:45 (bar-close time). `time_exit` 15:15.
   - `backtest_quality="full"`.
   - Partial at 1R where specified: the simulator moves the stop to breakeven after T1.
- **Synthetic sanity backtest.** The table below uses 250 synthetic NIFTY days, after costs, via `StrategyLab.backtest`. It checks the plumbing only and says nothing about real performance. The synthetic generator has no exploitable structure; for comparison, existing strategies such as `vwap_pullback` (−0.56R) and `ema_supertrend_adx` (−0.21R) are also negative on it.

| ID | n | win % | expectancy (R) |
|---|---|---|---|
| `ema_pullback` | 301 | 23.3 | −0.53 |
| `fib_pullback` | 260 | 22.7 | −0.36 |
| `supertrend_pullback` | 253 | 29.2 | −0.37 |
| `breakout_retest` | 245 | 19.2 | −0.81 |
| `bollinger_reversion` | 246 | 41.1 | −0.70 |
| `macd_trend` | 252 | 38.1 | −0.23 |

Real evaluation requires Angel One / NSE history and walk-forward testing.
