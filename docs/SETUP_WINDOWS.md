# Setting up IndexEdge on Windows

Time needed: ~20 minutes. You need a Windows 10/11 PC that is on during market hours, and an Angel One account
with F&O enabled.

## 1. Install the app

1. Download the repository ZIP and extract it to a folder without spaces in odd places, e.g. `C:\IndexEdge`.
2. Double-click `install.bat`. If Windows SmartScreen warns, click *More info → Run anyway*.
   The installer:
   - installs [uv](https://docs.astral.sh/uv/) and a private Python 3.11 (nothing system-wide, no admin needed);
   - installs IndexEdge's libraries into `.venv`;
   - runs the setup wizard (step 3; press Enter to skip any question);
   - creates the desktop shortcut;
   - optionally schedules IndexEdge to start at 08:25 on weekdays;
   - optionally adds a firewall rule so your phone on home Wi-Fi can open the app (asks for admin once).
     Phones must then be paired once: **Settings → Phone access** shows a QR code. Unpaired devices on your
     network are refused.
3. Start it with the **IndexEdge** desktop shortcut. Without broker details it runs in **demo mode**.

## 2. Create your SmartAPI app (Angel One)

1. Sign in at <https://smartapi.angelone.in> with your Angel One client code.
2. *Create an App* → type **Trading APIs**. Enter any redirect URL (e.g. `http://localhost`).
   In the **Primary static IP** field enter your static IP (step 4). Save and copy the **API key**.
3. Enable TOTP: on the SmartAPI site choose *Enable TOTP*, sign in, and you will see a QR code **and a text key**.
   Scan the QR with Google Authenticator if you like, but **also copy the text key** — that is the
   "TOTP secret" IndexEdge needs to log in for you each morning.

## 3. Enter your details

Either answer the installer's wizard, or later in the app: **Settings → Broker & secrets**. You need:

| Field | Where it comes from |
|---|---|
| SmartAPI API key | step 2 |
| Client code | your Angel One login ID |
| PIN | your Angel One login PIN |
| TOTP secret | the text key from step 2.3 |

Secrets are stored in **Windows Credential Manager** (never in files or logs). Restart IndexEdge after entering
them; it will then log in automatically at 08:30 every trading day (or press **Login for today** in Settings).

## 4. Static IP (required only for live orders)

Since 2025–26 Angel One accepts API **orders** only from the IPv4 address registered on your SmartAPI app
(data and paper trading work from any IP).

- Ask your internet provider for a **static IPv4** address (many Indian ISPs offer it for a small monthly fee).
- Find it at <https://api.ipify.org> from the PC, register it in your SmartAPI app (step 2.2) and enter it in
  **Settings → Broker → Static IP**.
- IndexEdge checks your public IP at startup and every hour. If it doesn't match, **live orders are blocked**
  (paper trading continues) and you're alerted.

## 5. Telegram alerts (optional, recommended)

Alerts, one-tap ✅ Confirm / ❌ Skip buttons, the pre-market checklist and the end-of-day report on your phone —
anywhere, not just on home Wi-Fi.

1. In Telegram talk to **@BotFather** → `/newbot` → copy the **bot token**.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id": …}`.
3. In **Settings → Notifications** paste the token (stored in Credential Manager) and the chat id, and enable.

## 6. First week

1. Leave **Paper** mode on. Read the pre-market checklist each morning.
2. In **Strategy Lab** press *Revalidate* once real history has downloaded (the first login backfills ~60 days
   of 5-minute data; history keeps growing every day). Report cards switch from "synthetic" to your real data.
3. Choose execution modes per strategy: *Signal only*, *Confirm* (one tap) or *Auto*.
4. Review **Trades & Journal** and **Analytics** each evening.
5. Only after you're satisfied with paper results: **Settings → Mode → Live**, type `I UNDERSTAND THE RISK`.
   Live mode also requires a connected broker, a passing static-IP check and no open paper trades.

## Updating, backup, uninstall

- Update: double-click `update.bat`.
- Your data (journal, settings, candles, models) lives in the `data\` folder — back it up by copying it.
- Uninstall: delete the folder, the desktop shortcut and (if created) the *IndexEdge* task in Task Scheduler.
  Remove the stored secrets in *Credential Manager → Windows Credentials → IndexEdge*.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Daily login failed" alert | Check PIN / TOTP secret in Settings; make sure the PC clock is correct (TOTP is time-based). Press *Login for today*. |
| Live orders blocked: static IP | Your public IP changed or isn't registered. Compare Settings → Static IP with <https://api.ipify.org>. |
| "External context unavailable or stale" on every signal | Global market data couldn't be fetched (internet / Yahoo Finance down). Signals are blocked by design until it recovers. |
| Phone can't open the app / "device not paired" | Same Wi-Fi? Firewall rule added (re-run installer)? Open **Settings → Phone access** on the PC and scan the QR code (pairing link). |
| Health page shows "safe mode" | Market data stopped. Existing stops stay at the broker; new entries are paused until data resumes. |
