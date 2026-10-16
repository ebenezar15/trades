# Using IndexEdge from your iPad (100% free)

**How it works:** your Windows PC runs IndexEdge (the engine: live data, strategies, alerts). Your iPad — or any phone,
tablet or browser — is the screen and remote control. You place the orders yourself in Angel One on the PC (or the
Angel One app); IndexEdge gives you a ready-filled trade ticket, tracks the trade and tells you when to exit.

Nothing costs money: Angel One SmartAPI market data is free, the Cloudflare link is free and needs no account,
Telegram is free. Because IndexEdge never places orders itself in this setup, **no static IP is needed.**

## 1. On the PC (once, ~10 minutes)

1. Extract the IndexEdge ZIP to `C:\IndexEdge` and double-click `install.bat`.
2. In the setup questions, **create your IndexEdge login** (username + password ≥ 8 characters) — this is what you
   type on the iPad. Answer **Y** to the firewall question (lets the iPad reach the PC on Wi-Fi) and to "stop this PC
   from sleeping while plugged in" (the iPad only works while the PC is on).
3. Start IndexEdge from the desktop icon. The black window shows the address for the iPad, e.g.
   `http://192.168.1.25:8000`.

## 2. On the iPad — at home

1. Open **Safari** → type the address from the PC window → log in.
2. Tap **Share → Add to Home Screen**. IndexEdge now opens full-screen like an app.
3. Tap **🔔 Enable sound alerts** once (iPad requires a tap before a web app may play sounds).

## 3. On the iPad — anywhere (free)

1. **Settings → Use IndexEdge from anywhere** → switch **ON**. The first time the PC downloads Cloudflare's free
   connector (~40 MB), then shows a link like `https://quiet-river-blue-moon.trycloudflare.com` with a QR code.
2. Open the link on the iPad (or scan the QR) and log in. Every request through the link needs your password; 5 wrong
   passwords lock that device out for 15 minutes.
3. The link **changes every time the PC restarts** IndexEdge. If Telegram is set up, IndexEdge sends you the new link
   automatically each morning. (A permanent link is possible with a free Cloudflare account — see Troubleshooting.)

## 4. Connect Angel One — from the iPad

**Settings → Connect Angel One** walks you through it:

| SmartAPI "Create App" field | What to type |
|---|---|
| App type | Trading APIs |
| Redirect URL | `https://smartapi.angelone.in/` (Angel One rejects localhost / IP addresses; IndexEdge never uses it) |
| Postback URL | leave empty |
| Primary static IP | not needed when you trade manually — if the form insists, enter the IP shown in the wizard |

Then enable TOTP, paste the API key, client code, PIN and TOTP text key into the wizard, press **Test connection**
and **Connect now**. From then on the PC logs in to Angel One by itself every morning at 08:30 and streams
real-time Nifty / Bank Nifty / Sensex prices. Sensex trades are BSE contracts: the ticket shows exchange
**BFO** — pick BFO (not NFO) when you search the symbol in Angel One.

## 5. A trading day

| When | On the iPad | On the PC |
|---|---|---|
| Before 09:15 | Check **Pre-market**: global cues, expected gap, levels, today's events | IndexEdge running (auto-starts at 08:25 if you chose that) |
| A signal appears (sound + Telegram) | Open it → **Why?** to see every reason → **Trade on Angel One** | — |
| Trade ticket | Shows symbol, BUY, INTRADAY, LIMIT, quantity, limit price, SL-L trigger/limit, target — each with a copy button | Place exactly that order in Angel One; after it fills, place the SL-L order |
| After placing | Tap **I placed this trade**, enter your fill price | — |
| While open | Live price and P&L; red **STOP HIT — exit now** or green **TARGET REACHED** banners, sound and Telegram | Exit in Angel One when told |
| After exit | Tap **I exited**, enter the exit price → the journal, analysis and statistics update | — |
| 15:15 | **SQUARE OFF NOW** alert (Angel One auto-squares intraday F&O at 15:20) | Close anything still open |

You can still use **Paper** mode and the **Agent** for simulated practice in parallel.

## Troubleshooting

| Problem | Fix |
|---|---|
| iPad can't open the address at home | Same Wi-Fi? PC on and IndexEdge window open? Firewall rule added (re-run `install.bat`)? |
| "Login required" loop | Make sure you use the latest address; the Cloudflare link changes when IndexEdge restarts |
| "Too many attempts" | Wait 15 minutes; check the username (not case-sensitive) |
| Forgot the password | On the PC open **Settings → Security** and set a new one (no old password needed on the PC itself) |
| Remote link says "Tunnel stopped" | PC internet down or a firewall blocks Cloudflare; it retries automatically |
| Want a permanent link | Optional: create a free Cloudflare account and a "named tunnel" to `http://localhost:8000` (Cloudflare docs → Tunnels). Turn the built-in quick link off; IndexEdge's login still protects every request |
| No sound on iPad | Tap **Enable sound alerts** again after the iPad was locked; keep the IndexEdge tab/app open |
