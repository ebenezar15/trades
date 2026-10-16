# Angel One SmartAPI — verified notes (2026-10-16)

Sources: https://smartapi.angelone.in/docs (single page), `smartapi-python` 1.5.5 wheel source,
SmartAPI forum, Angel One news (April 2026 algo changes). Items marked ⚠ could not be verified.

## Login / session
- `SmartConnect(api_key=...)` → `generateSession(clientCode, password=PIN, totp)`.
  SDK returns the **getProfile** response augmented with `data.jwtToken` (prefixed `"Bearer "`),
  `data.refreshToken`, `data.feedToken`. On failure returns raw login response (`status`, `message`, `errorcode`).
- `getfeedToken()`, `generateToken(refresh_token)`, `terminateSession(clientCode)`.
- Session valid until midnight; log out daily. Login 1 req/s.
- **Importing `SmartApi` makes a network call (public IP lookup) — import lazily.**

## Instrument master
- URL: `https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json` (regenerated daily).
- Fields: `token, symbol, name, expiry, strike, lotsize, instrumenttype, exch_seg, tick_size, freeze_qty`.
- Index row: `{"token":"99926000","symbol":"Nifty 50","name":"NIFTY","instrumenttype":"AMXIDX","exch_seg":"NSE"}`.
- **strike is ×100** (17500 → "1750000.000000"); **tick_size in paise** ("5.000000" → ₹0.05).
- instrumenttype: OPTIDX, FUTIDX, AMXIDX (+ OPTSTK, FUTSTK). exch_seg: NSE, NFO, BSE, BFO, MCX, nse_cm.
- expiry format `DDMMMYYYY` e.g. `25NOV2025` ⚠ (parse defensively).
- Index tokens: NIFTY 99926000, BANKNIFTY 99926009, INDIA VIX 99926017 (exchange NSE); SENSEX 99919000 with
  exchange **BSE** (SmartAPI team on the forum: "token is correct '99919000' and segment is BSE"). SENSEX
  options/futures are on **BFO** (WebSocket exchangeType 4; BSE = 3).

## Historical candles
- `getCandleData({"exchange","symboltoken","interval","fromdate","todate"})`, dates `"YYYY-MM-DD HH:MM"`.
- Intervals & max days/request: ONE_MINUTE 30, THREE_MINUTE 60, FIVE_MINUTE 100, TEN_MINUTE 100,
  FIFTEEN_MINUTE 200, THIRTY_MINUTE 200, ONE_HOUR 400, ONE_DAY 2000.
- Response `data`: list of `[ "2023-09-06T11:15:00+05:30", o, h, l, c, v ]`. Rate 3/s, 120/min, 5000/h.
- `getOIData` exists for historical OI. Max lookback ⚠ undocumented.

## Orders
- `placeOrder(params)` → order id string; `placeOrderFullResponse` → full JSON. `modifyOrder` (+`orderid`),
  `cancelOrder(order_id, variety)`.
- Params (strings): variety (NORMAL / STOPLOSS), tradingsymbol, symboltoken, exchange, transactiontype,
  ordertype (LIMIT / STOPLOSS_LIMIT — IndexEdge never uses MARKET / STOPLOSS_MARKET), producttype
  (INTRADAY / CARRYFORWARD), duration DAY (never IOC), price, triggerprice, quantity, ordertag (<20 chars).
- Stop-loss-limit orders use **variety=STOPLOSS, ordertype=STOPLOSS_LIMIT**.
- `orderBook()`, `tradeBook()`, `position()`, `rmsLimit()`, `ltpData(exch, symbol, token)`,
  `getMarketData(mode, {"NSE":[...],"NFO":[...]})` (≤50 tokens), `optionGreek({"name","expirydate":"25JAN2024"})`,
  `putCallRatio()`, `oIBuildup`.
- Rate limits: place+modify+cancel **combined 9/s** (500/min, 1000/h); orderBook/tradeBook/position 1/s;
  getRMS 2/s; LTP/quote 10/s ⚠ (docs conflict: 1/s). 403 on breach.

## WebSockets
- `SmartWebSocketV2(auth_token, api_key, client_code, feed_token, max_retry_attempt=...)`;
  override `on_open/on_data/on_error/on_close`; `connect()` blocks (run in a thread).
- `subscribe(correlation_id, mode, [{"exchangeType": 1|2, "tokens": [...]}])`; modes 1 LTP, 2 Quote, 3 SnapQuote.
- exchangeType 1 nse_cm, 2 nse_fo, 3 bse_cm, 4 bse_fo, 5 mcx_fo.
- Tick keys: `token, exchange_timestamp (ms), last_traded_price, volume_trade_for_the_day, open_interest,
  open_price_of_the_day, high_price_of_the_day, low_price_of_the_day, closed_price, best_5_buy_data,
  best_5_sell_data` — **prices in paise (/100)**. SDK swaps best_5 buy/sell in SnapQuote ⚠.
- Limits: 1000 subscriptions, 3 connections per client.
- Order updates: `SmartApi.smartWebSocketOrderUpdate.SmartWebSocketOrderUpdate(auth_token, api_key,
  client_code, feed_token)`, override `on_message`.

## SEBI retail algo framework @ Angel One
- Static IP (IPv4) required for place/modify/cancel & GTT since 2025-09-26; other APIs don't need it.
- From 2026-04-01: orders only from registered primary static IP; **MARKET and IOC prohibited for algos**;
  ≤10 OPS uses generic algo ID (IndexEdge caps at 8 OPS).

## Intraday cutoffs (from 2026-08-03, closing auction session)
- F&O intraday auto square-off **15:20**; NFO trades till 15:40; SmartAPI rejects entries 15:15–15:20 for CAS scrips.

## Contract facts (derive from master at runtime)
- NIFTY lot 65, weekly expiry Tuesday. BANKNIFTY lot 30, **monthly only** (last Tuesday). SENSEX lot 20, weekly
  expiry **Thursday** (since 2025-09-01), monthly last Thursday. ⚠ secondary sources — real lot sizes and expiries
  are always read from the instrument master.

## Free data (⚠ untested from build environment)
- NSE (needs browser headers + cookie from homepage): `/api/option-chain-indices?symbol=NIFTY`
  (possibly `/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=DD-Mon-YYYY`), `/api/fiidiiTradeReact`,
  `/api/holiday-master?type=trading`. SmartAPI `putCallRatio`/`optionGreek`/`oIBuildup` as fallback.
