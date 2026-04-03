# LunchFund Tracker

A Python application using Playwright to automatically track and monitor daily school cafeteria spending via PaySchools Central and send an email update.

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
   ```

## How to Test

You can run an ultimate test mode. This runs the script in **headed mode** (meaning a browser window will pop open and you can watch it navigate and scrape in real-time). It performs the full job, updates the local records (`history.json`), and sends a test email.

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
   0 13 * * 1-5 cd /Users/yyu/yyu-ds/lunchfund && /Users/yyu/.cargo/bin/uv run scraper.py >> cron.log 2>&1
   ```
   *(Note: You may need to verify the absolute path to your `uv` installation via `which uv`. If it's located somewhere else like `/opt/homebrew/bin/uv`, replace it accordingly. Alternatively, you can point directly to the project's virtual environment python: `/Users/yyu/yyu-ds/lunchfund/.venv/bin/python`)*

### Cron Syntax Breakdown:
- **`0`** - Minute (0th minute)
- **`13`** - Hour (1:00 PM in 24-hour time)
- **`*`** - Day of the month (Any)
- **`*`** - Month (Any)
- **`1-5`** - Day of the week (1 to 5 corresponds to Monday to Friday)

This will ensure the application automatically scrapes the balance and notifies you, with any errors routing to a local `cron.log` file.
