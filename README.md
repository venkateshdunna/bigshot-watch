# Big Shot Watch

Telegram alerts when insiders, big investors, funds or members of Congress buy or sell stocks, in India (NSE) and the US (SEC, House). Each alert is sent within minutes of the filing going public.

```
🇮🇳🟢 INSIDER BUY · RELIANCE
Reliance Industries Limited
👤 Some Promoter Trust (Promoter Group)
1,00,000 sh · ₹2,850.00/sh · ₹28.50 cr
Holding: 0.10% → 0.11%
Trade: 29-Oct-2026 · Filed: 31-Oct-2026
⭐ Watchlist 💰 Large trade
View filing
```

## What it watches

| Feed | Who | How fresh | Runs on |
|---|---|---|---|
| `nse_insider` | Promoters, directors, KMP (SEBI PIT) | Within 2 trading days of the trade | Your PC |
| `nse_deals` | Anyone trading ≥0.5% (bulk) or ≥₹10 cr (block) | Same day, after market | Your PC |
| `nse_sast` | Big stake changes (takeover rules) | Within days | Your PC |
| `sec_form4` | US CEOs, directors, 10% owners | Within 2 business days | Cloud |
| `sec_13dg` | Anyone taking a 5%+ stake | Within 5 business days | Cloud |
| `sec_13f` | Hedge funds (Buffett, Ackman, etc.) | Quarterly, up to 45 days old | Cloud |
| `house_ptr` | US House members | Up to 45 days old | Cloud |

**When you get an alert.** You get an alert when an item matches any of these:

- a stock on your **watchlist**
- an **investor** you named, in any stock
- a trade above your **size threshold** (default ₹10 cr / $1M)
- any brand-new activist 13D
- any Congress trade

**Why India runs on your PC.** NSE blocks most cloud servers, so the India feeds run on your Windows PC. The US feeds run free in the cloud via GitHub Actions. *(Vercel's free plan only allows a once-a-day schedule, so GitHub Actions is the better fit here.)*

---

## Step 1 — Create your Telegram bot (5 min)

1. In Telegram, open **@BotFather**, send `/newbot`, and follow the prompts. Copy the **token** it gives you.
2. Open your new bot and send it any message (e.g. "hi").
3. In a browser, open `https://api.telegram.org/bot<TOKEN>/getUpdates`. Find `"chat":{"id": 123456789` — that number is your **chat id**.

## Step 2 — India feeds on your PC

1. Install Python 3.10+ from python.org and tick **"Add python.exe to PATH"**.
2. Unzip this folder somewhere permanent, e.g. `Documents\bigshot-watch`.
3. Double-click **`setup_windows.bat`**. This installs the dependencies, creates `config.yaml`, and adds the watcher to Windows startup.
4. Open **`config.yaml`** in Notepad and fill in:
   - `telegram.bot_token`
   - `telegram.chat_id`
   - `sec.user_agent` (your name and email — SEC requires this)
   - your **watchlist** and **investors**
5. Double-click **`test_windows.bat`**. You should get a Telegram test message, followed by a printout of recent matching items.
6. Restart Windows, or double-click `BigShotWatch.vbs` in your Startup folder. The watcher then runs hidden and logs to `watcher.log`.

To stop it, run `stop_windows.bat`.

**Note.** India alerts only arrive while your PC is on. Missed filings are picked up when it comes back, because the feed looks back 3 days.

## Step 3 — US feeds in the cloud (GitHub, free)

1. Create a **public** GitHub repo. Actions minutes are unlimited on public repos.
2. Upload every file in this folder **except** `config.yaml` and `state.json`. The `.gitignore` already excludes them.
3. Go to repo → **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `CONFIG_YAML` — paste your whole `config.yaml`. This keeps your watchlist private.
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SEC_USER_AGENT`
4. Go to the **Actions** tab, enable workflows, open **us-watch**, and click **Run workflow**.

From then on it runs every hour and polls continuously inside each run. Alerts arrive about 2–3 minutes after a filing appears on EDGAR.

**Note.** GitHub pauses scheduled workflows on public repos after 60 days with no commits, and emails you first. Re-enable it with one click, or push any small change.

*(Want everything on your PC instead? Change the startup line to `--feeds all`.)*

---

## Tuning

- **Too many alerts?** Raise `thresholds`, set `house_ptr.alert_all: false`, or set `us_initial_13d_anywhere: false`.
- **Too few?** Add more names to `investors`. Matching tolerates middle names, so `Vijay Kedia` matches `VIJAY KISHANLAL KEDIA`.
- **Form 4 sales.** Set `sec_form4.codes: [P]` to get only open-market **buys**. These are the strongest insider signal. Sales are often for taxes or are pre-planned; trades under a pre-set plan are flagged as "10b5-1".
- **Run the tests offline:** `python tests/test_parsers.py`

## Known limits

- NSE has no official API. This app uses the same JSON its website uses. If NSE renames a field or blocks requests, you'll get a "feed keeps failing" Telegram alert. The bulk/block feed falls back to NSE's CSV archive.
- US **Senate** trades aren't covered yet; that site needs an interactive login step. House trades are covered.
- Congress PDFs that were filed on paper (scanned) can't be read. You'll still get an alert with the link.
- Disclosures always lag the actual trade. Insiders and bulk deals are nearly real-time. 13F and Congress filings can be weeks old, so the investor may already have exited.

*This tool surfaces public disclosures. It isn't investment advice.*
