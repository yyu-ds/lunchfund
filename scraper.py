import os
import re
import json
import smtplib
import time
import traceback
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from statistics import mean
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

BASE_DIR = Path(__file__).parent
HISTORY_FILE = BASE_DIR / "history.json"

KIDS = ["Melody Yu", "Micah Yu"]
KID_COLORS = {"Melody Yu": "#7c5cbf", "Micah Yu": "#2a9d8f"}

# Warn when a balance drops below this (override in .env)
LOW_BALANCE_THRESHOLD = float(os.environ.get("LOW_BALANCE_THRESHOLD", "20"))


# ---------------------------------------------------------------------------
# History
#
# history.json format:
#   {
#     "balances": {kid: latest_balance},        # baseline for spend calc
#     "last_updated": "YYYY-MM-DD",
#     "records": [{"date": ..., "balances": {...}, "spent": {...}}, ...]
#   }
# ---------------------------------------------------------------------------

def get_history():
    if not HISTORY_FILE.exists():
        return {"balances": {}, "last_updated": None, "records": []}
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
    if "records" not in history:
        # Migrate the old flat {kid: balance} format
        history = {"balances": history, "last_updated": None, "records": []}
    return history


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def append_record(history, today_str, balances, spending):
    record = {"date": today_str, "balances": balances, "spent": spending}
    records = [r for r in history["records"] if r["date"] != today_str]
    records.append(record)
    history["records"] = records[-365:]


def avg_daily_spend(history, kid, lookback=10):
    """Average over the last `lookback` days the kid actually spent money."""
    spends = [
        r["spent"][kid]
        for r in history["records"]
        if r.get("spent", {}).get(kid, 0) > 0
    ]
    return mean(spends[-lookback:]) if spends else None


def week_spend_totals(history, today):
    """Total positive spending per kid for this week and last week."""
    this_week, last_week = {}, {}
    for r in history["records"]:
        r_date = date.fromisoformat(r["date"])
        age = (today - r_date).days
        for kid, spent in r.get("spent", {}).items():
            if spent <= 0:
                continue
            if age < 7:
                this_week[kid] = this_week.get(kid, 0) + spent
            elif age < 14:
                last_week[kid] = last_week.get(kid, 0) + spent
    return this_week, last_week


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def scrape_balances(headless=True):
    user = os.environ.get("PAYSCHOOLS_USER")
    password = os.environ.get("PAYSCHOOLS_PASS")

    if not user or not password:
        raise RuntimeError("Missing PAYSCHOOLS_USER/PAYSCHOOLS_PASS in .env")

    balances = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        try:
            print("Navigating to PaySchools Central...")
            page.goto("https://www.payschoolscentral.com/")

            # PaySchools now uses a two-step login: email -> Continue -> password
            print("Logging in (step 1: email)...")
            page.locator("#emailInputSsoDesktop, input[name='Email']:visible").first.fill(user)
            page.locator("button:has-text('Continue'):visible").first.click()

            print("Logging in (step 2: password)...")
            page.locator("#passwordInputDesktop").wait_for(state="visible", timeout=20000)
            email_step2 = page.locator("#emailInputDesktop")
            if email_step2.count() and not email_step2.input_value():
                email_step2.fill(user)
            page.locator("#passwordInputDesktop").fill(password)
            page.locator("button:text-is('Login'):visible").first.click()

            print("Waiting for dashboard to load...")
            # Wait for the meals table to have actual data rows (not just the header)
            page.wait_for_selector("app-meals td", timeout=30000)
            print("Dashboard loaded.")

            with open(BASE_DIR / "dashboard_text.txt", "w", encoding="utf-8") as f:
                f.write(page.locator("body").inner_text())
            with open(BASE_DIR / "dashboard_source.html", "w", encoding="utf-8") as f:
                f.write(page.content())

            # Find which column index is "Cafeteria Balance" from the header row
            header_cells = page.locator("app-meals th").all_text_contents()
            caf_col_idx = next(
                (i for i, h in enumerate(header_cells) if "Cafeteria Balance" in h),
                -1,
            )
            print(f"Cafeteria Balance column index: {caf_col_idx} (headers: {header_cells})")

            if caf_col_idx != -1:
                rows = page.locator("app-meals tr:has(td)").all()
                for row in rows:
                    cells = row.locator("td").all_text_contents()
                    if not cells or len(cells) <= caf_col_idx:
                        continue
                    # Normalize whitespace (the name may contain &nbsp;)
                    row_name = re.sub(r"\s+", " ", cells[0]).strip()
                    for kid in KIDS:
                        if row_name.lower() == kid.lower():
                            val_str = cells[caf_col_idx].replace("$", "").replace(",", "").strip()
                            try:
                                balances[kid] = float(val_str)
                            except ValueError:
                                pass

            for kid in KIDS:
                if kid not in balances:
                    print(f"Could not find Cafeteria Balance for {kid} via DOM selectors.")

        except Exception:
            page.screenshot(path=str(BASE_DIR / "error_screenshot.png"))
            print("Saved error_screenshot.png for debugging.")
            raise
        finally:
            browser.close()

    return balances


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject, body, html_body=None, attachments=None):
    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_APP_PASSWORD")
    receiver = os.environ.get("EMAIL_RECEIVER")

    if not sender or not password or not receiver:
        print("Email credentials not fully configured in .env")
        return

    msg = EmailMessage()
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver

    for path in attachments or []:
        path = Path(path)
        if path.exists():
            msg.add_attachment(
                path.read_bytes(),
                maintype="image",
                subtype=path.suffix.lstrip(".") or "png",
                filename=path.name,
            )

    max_retries = 10
    retry_delay_seconds = 180  # 3 minutes

    for attempt in range(1, max_retries + 1):
        try:
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            server.login(sender, password)
            server.send_message(msg)
            server.quit()
            print("Email sent successfully!")
            return
        except Exception as e:
            print(f"Failed to send email (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                print(f"Retrying in {retry_delay_seconds // 60} minutes...")
                time.sleep(retry_delay_seconds)
            else:
                print("Max retries reached. Giving up.")


def send_failure_alert(error_text):
    """Instead of dying silently, email the error and any fresh debug screenshots."""
    attachments = []
    for name in ("error_screenshot.png", "login_error.png"):
        path = BASE_DIR / name
        if path.exists() and time.time() - path.stat().st_mtime < 600:
            attachments.append(path)

    body = (
        "The PaySchools lunch fund scraper failed to run.\n\n"
        f"Time: {datetime.now():%Y-%m-%d %H:%M}\n\n"
        f"Error:\n{error_text}\n"
    )
    if attachments:
        body += "\nDebug screenshot(s) attached.\n"
    send_email("🚨 LunchFund scraper FAILED", body, attachments=attachments)


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def trend_chart_html(history, kid, days=30):
    """Email-safe bar chart (plain tables + inline styles) of recent balances."""
    recent = [r for r in history["records"] if kid in r.get("balances", {})][-days:]
    if len(recent) < 2:
        return ""
    max_bal = max(r["balances"][kid] for r in recent) or 1
    color = KID_COLORS.get(kid, "#666666")
    cells = ""
    for r in recent:
        bal = r["balances"][kid]
        h = max(2, round(bal / max_bal * 60))
        cells += (
            '<td style="vertical-align:bottom;padding:0 1px;">'
            f'<div style="height:{h}px;width:8px;background:{color};'
            f'border-radius:2px 2px 0 0;" title="{r["date"]}: ${bal:.2f}"></div></td>'
        )
    return (
        f'<div style="font-size:12px;color:#888;margin:12px 0 2px;">Balance trend '
        f'(last {len(recent)} checks)</div>'
        f'<table cellpadding="0" cellspacing="0" style="border-bottom:1px solid #ddd;">'
        f'<tr>{cells}</tr></table>'
    )


def build_report(history, balances, spending, missing_kids, today, since_label):
    """Returns (subject, plain_text_body, html_body)."""
    low_alerts = []
    text = f"PaySchools Lunch Balance Summary — {today:%A, %b %d, %Y}\n\n"
    cards_html = ""

    for kid, balance in balances.items():
        spent = spending[kid]
        if spent > 0:
            spent_line = f"Spent {since_label}: ${spent:.2f}"
        elif spent < 0:
            spent_line = f"Added to account: ${-spent:.2f}"
        else:
            spent_line = f"No spending {since_label}."

        days_left_line = ""
        avg = avg_daily_spend(history, kid)
        if avg:
            days_left = int(balance / avg)
            days_left_line = f"~{days_left} lunch days left (avg ${avg:.2f}/day)"
            if balance < LOW_BALANCE_THRESHOLD:
                low_alerts.append(f"{kid.split()[0]} ${balance:.2f} (~{days_left} days left)")
        elif balance < LOW_BALANCE_THRESHOLD:
            low_alerts.append(f"{kid.split()[0]} ${balance:.2f}")

        text += f"{kid}:\n  Current Balance: ${balance:.2f}\n  {spent_line}\n"
        if days_left_line:
            text += f"  {days_left_line}\n"
        if balance < LOW_BALANCE_THRESHOLD:
            text += f"  ⚠️ Below ${LOW_BALANCE_THRESHOLD:.0f} — time to top up!\n"
        text += "\n"

        color = KID_COLORS.get(kid, "#666666")
        warn_html = (
            f'<div style="color:#c0392b;font-weight:bold;margin-top:6px;">'
            f'⚠️ Below ${LOW_BALANCE_THRESHOLD:.0f} — time to top up!</div>'
            if balance < LOW_BALANCE_THRESHOLD else ""
        )
        days_html = (
            f'<div style="color:#888;font-size:13px;">{days_left_line}</div>'
            if days_left_line else ""
        )
        cards_html += f"""
        <div style="border:1px solid #e0e0e0;border-left:4px solid {color};
                    border-radius:6px;padding:14px 18px;margin-bottom:14px;">
          <div style="font-size:15px;font-weight:bold;color:{color};">{kid}</div>
          <div style="font-size:26px;font-weight:bold;margin:4px 0;">${balance:.2f}</div>
          <div style="font-size:14px;color:#444;">{spent_line}</div>
          {days_html}{warn_html}
          {trend_chart_html(history, kid)}
        </div>"""

    for kid in missing_kids:
        text += f"{kid}:\n  ⚠️ Balance not found on dashboard today.\n\n"
        cards_html += (
            f'<div style="border:1px solid #e0e0e0;border-radius:6px;padding:14px 18px;'
            f'margin-bottom:14px;color:#c0392b;">⚠️ {kid}: balance not found on the '
            f'dashboard today.</div>'
        )

    # Friday weekly recap
    recap_html = ""
    if today.weekday() == 4:
        this_week, last_week = week_spend_totals(history, today)
        if this_week or last_week:
            text += "📅 Weekly Recap:\n"
            recap_rows = ""
            for kid in KIDS:
                tw, lw = this_week.get(kid, 0), last_week.get(kid, 0)
                text += f"  {kid}: ${tw:.2f} this week (last week ${lw:.2f})\n"
                recap_rows += (
                    f'<tr><td style="padding:4px 12px 4px 0;">{kid}</td>'
                    f'<td style="padding:4px 12px;font-weight:bold;">${tw:.2f}</td>'
                    f'<td style="padding:4px 12px;color:#888;">${lw:.2f} last week</td></tr>'
                )
            if this_week:
                champ = max(this_week, key=this_week.get)
                text += f"  🏆 Biggest spender: {champ.split()[0]}\n"
                recap_rows += (
                    f'<tr><td colspan="3" style="padding:6px 0;">🏆 Biggest spender: '
                    f'<b>{champ.split()[0]}</b></td></tr>'
                )
            text += "\n"
            recap_html = (
                '<div style="border-top:1px solid #e0e0e0;margin-top:8px;padding-top:12px;">'
                '<div style="font-weight:bold;margin-bottom:6px;">📅 Weekly Recap</div>'
                f'<table style="font-size:14px;">{recap_rows}</table></div>'
            )

    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:520px;
                margin:0 auto;color:#222;">
      <h2 style="font-weight:600;">🍱 Lunch Fund — {today:%A, %b %d}</h2>
      {cards_html}
      {recap_html}
      <div style="color:#aaa;font-size:11px;margin-top:16px;">
        Automated report from the LunchFund scraper.</div>
    </div>"""

    # Subject line
    if low_alerts:
        subject = "⚠️ Lunch fund low: " + ", ".join(low_alerts)
    else:
        melody_spent = spending.get("Melody Yu", 0)
        micah_spent = spending.get("Micah Yu", 0)
        if melody_spent > micah_spent > 0 or (melody_spent > 0 >= micah_spent):
            subject = f"Melody spent more than Micah! (${melody_spent:.2f} vs ${micah_spent:.2f})"
        elif micah_spent > melody_spent > 0 or (micah_spent > 0 >= melody_spent):
            subject = f"Micah spent more than Melody! (${micah_spent:.2f} vs ${melody_spent:.2f})"
        elif melody_spent > 0 and melody_spent == micah_spent:
            subject = f"Melody and Micah spent the same amount! (${melody_spent:.2f})"
        else:
            subject = "Daily PaySchools Lunch Balance"

    return subject, text, html


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

def run_job(headless=True, force_send=False):
    print("Running PaySchools Scraper Job...")
    try:
        balances = scrape_balances(headless=headless)
        if not balances:
            raise RuntimeError(
                "Scrape finished but no balances were found — the dashboard layout "
                "may have changed. See dashboard_source.html / error_screenshot.png."
            )
    except Exception:
        error_text = traceback.format_exc()
        print(error_text)
        send_failure_alert(error_text)
        return

    history = get_history()
    today = date.today()
    today_str = today.isoformat()

    since_label = "today"
    if history.get("last_updated"):
        last = date.fromisoformat(history["last_updated"])
        if (today - last).days > 1:
            since_label = f"since {last:%b %d}"

    spending = {}
    for kid, balance in balances.items():
        last_balance = history["balances"].get(kid, balance)
        spending[kid] = round(last_balance - balance, 2)
        history["balances"][kid] = balance

    missing_kids = [k for k in KIDS if k not in balances]

    append_record(history, today_str, balances, spending)
    history["last_updated"] = today_str
    save_history(history)

    subject, text, html = build_report(
        history, balances, spending, missing_kids, today, since_label
    )
    print(text)

    # Inferred no-school day: nobody spent anything and nothing else is
    # noteworthy — record it, but skip the email.
    nothing_happened = (
        all(s == 0 for s in spending.values())
        and not missing_kids
        and not subject.startswith("⚠️")
        and not (today.weekday() == 4 and "Weekly Recap" in text)
    )
    if nothing_happened and not force_send:
        print("No activity for any kid (likely no school today). Skipping email.")
        return

    send_email(subject, text, html_body=html)


def test():
    """Ultimate test: full job in headed mode; always sends the email."""
    print("Running ULTIMATE test (headed browser, sends email, updates history)...")
    run_job(headless=False, force_send=True)


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv[1:]:
        test()
    else:
        run_job()
