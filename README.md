# LunchFund Tracker 

A Python application using Playwright to automatically track and monitor daily school cafeteria spending via PaySchools Central and send an email update.

## Features

- **Daily balance email** — HTML with per-kid cards and a 30-day balance trend chart (plus a plain-text fallback), including the classic "who spent more" subject line.
- **Failure alerts** — if the scrape breaks (login change, layout change, timeout), you get a 🚨 email with the error and debug screenshots instead of silence.
- **Spending history** — every run appends a dated record to `history.json` (up to a year kept), powering the trends and recaps below.
- **Low-balance warning** — when a balance drops below `LOW_BALANCE_THRESHOLD` (default `$20`), the subject flips to a ⚠️ warning with an estimated "lunch days left" based on that kid's average daily spend.
- **Weekly recap** — Friday emails include per-kid totals for the week, a comparison to last week, and the 🏆 biggest spender.
- **No-school-day inference** — if nobody spent anything (and there's nothing else to report), the run records the data but skips the email, so holidays and summer break don't spam you.

## Prerequisites

This project uses `uv` for easy dependency management. Make sure you have `uv` installed.

1. Install project dependencies:
   ```bash
   uv sync
   ```

2. Install the required Playwright browsers (if you haven't already):
   ```bash
   uv run playwright install chromium
   ```

3. Ensure you have your `.env` file set up properly with your credentials:
   ```env
   PAYSCHOOLS_USER=your_email
   PAYSCHOOLS_PASS=your_password
   EMAIL_SENDER=sender_email
   EMAIL_APP_PASSWORD=sender_app_password
   EMAIL_RECEIVER=receiver_email
   # Optional: low-balance warning threshold in dollars (default 20)
   LOW_BALANCE_THRESHOLD=20
   ```

## How to Test

You can run an ultimate test mode. This runs the script in **headed mode** (meaning a browser window will pop open and you can watch it navigate and scrape in real-time). It performs the full job, updates the local records (`history.json`), and always sends the email — even on a zero-spending day when the normal run would skip it.

Run the following command:

```bash
uv run scraper.py --test
```

For quiet, headless execution, simply run:
```bash
uv run scraper.py
```

## How to Deploy the Cron Job

You can configure a cron job to automatically run this scraper daily at **1:00 PM** from **Monday to Friday** (5 days a week).

1. Open your crontab configuration by running:
   ```bash
   crontab -e
   ```

2. Add the following entry to the bottom of the file (this assumes `uv` is installed and accessible in your environment):
   ```cron
   0 13 * * 1-5 cd /Users/yyu/yyu-ds/lunchfund && /Users/yyu/.local/bin/uv run scraper.py >> cron.log 2>&1
   ```
   *(Note: The absolute path used for `uv` is `/Users/yyu/.local/bin/uv`. Alternatively, you can point directly to the project's virtual environment python: `/Users/yyu/yyu-ds/lunchfund/.venv/bin/python`)*

### Cron Syntax Breakdown:
- **`0`** - Minute (0th minute)
- **`13`** - Hour (1:00 PM in 24-hour time)
- **`*`** - Day of the month (Any)
- **`*`** - Month (Any)
- **`1-5`** - Day of the week (1 to 5 corresponds to Monday to Friday)

This will ensure the application automatically scrapes the balance and notifies you, with any errors routing to a local `cron.log` file.

## How to Pause the Cron Job

Thanks to the no-school-day inference, you don't strictly need to pause anything during breaks — the scraper will quietly record unchanged balances without emailing you. But if you'd rather not have it log in at all (for example, during summer break):

1. Open the crontab editor:
   ```bash
   crontab -e
   ```
2. Add a `#` at the very beginning of the line to "comment it out". It should look like this:
   ```cron
   # 0 13 * * 1-5 cd /Users/yyu/yyu-ds/lunchfund && /Users/yyu/.local/bin/uv run scraper.py >> cron.log 2>&1
   ```
3. Save and exit. 

When school starts again, you can simply edit the crontab (using `crontab -e`) and remove the `#` to re-enable it!
