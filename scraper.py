import os
import re
import json
import smtplib
from email.message import EmailMessage
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

HISTORY_FILE = Path(__file__).parent / "history.json"

def get_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def scrape_balances(headless=True):
    user = os.environ.get("PAYSCHOOLS_USER")
    password = os.environ.get("PAYSCHOOLS_PASS")
    
    if not user or not password:
        print("Missing credentials in .env")
        return None

    balances = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        
        print("Navigating to PaySchools Central...")
        page.goto("https://www.payschoolscentral.com/")
        
        print("Logging in...")
        # Uses explicit locators and filters out hidden fields to be more robust
        try:
            page.locator("input[type='email']:visible, input[type='text']:visible, input[name*='user']:visible, input[name*='email']:visible").first.fill(user)
            page.locator("input[type='password']:visible, input[name*='pass']:visible").first.fill(password)
            page.locator("button[type='submit']:visible, button:has-text('Log In'):visible, button:has-text('Login'):visible, button:has-text('Sign In'):visible").first.click()
        except Exception as e:
            print("Failed to find login fields. Trying to dump page content for debugging.")
            page.screenshot(path="login_error.png")
            raise e
        
        print("Waiting for dashboard to load...")
        try:
            # Wait for at least one kid to show up
            page.wait_for_selector("text='Melody Yu'", timeout=15000)
            print("Dashboard loaded.")
            
            # Since the exact HTML structure is unknown, we dump the page text and parse it
            page_text = page.locator("body").inner_text()
            
            with open("dashboard_text.txt", "w", encoding="utf-8") as f:
                f.write(page_text)
            with open("dashboard_source.html", "w", encoding="utf-8") as f:
                f.write(page.content())
                
            lines = page_text.split('\n')
            
            # Find the header row to know which exact column is "Cafeteria Balance"
            caf_col_idx = -1
            for line in lines:
                if "Cafeteria Balance" in line:
                    cols = line.split('\t')
                    for i, col_name in enumerate(cols):
                        if "Cafeteria Balance" in col_name:
                            caf_col_idx = i
                            break
                    if caf_col_idx != -1:
                        break
            
            for kid in ["Melody Yu", "Micah Yu"]:
                kid_pattern = kid.replace(" ", r"\s+")
                found = False
                
                # Use strict table-column lookups to avoid "Bonus Balance"
                if caf_col_idx != -1:
                    for line in lines:
                        if re.match(rf"^{kid_pattern}", line.strip(), re.IGNORECASE):
                            cols = line.split('\t')
                            # Ensure the row has enough columns
                            if len(cols) > caf_col_idx:
                                val_str = cols[caf_col_idx].replace('$', '').replace(',', '').strip()
                                try:
                                    balances[kid] = float(val_str)
                                    found = True
                                    break
                                except ValueError:
                                    pass
                
                # Fallback purely in case table formatting changes dramatically
                if not found:
                    print(f"Could not find exact Cafeteria table column for {kid}. Falling back to loose search.")
                    pattern = re.compile(rf"{kid_pattern}\s+\$(\d+\.\d{{2}})", re.IGNORECASE)
                    match = pattern.search(page_text)
                    if match:
                        balances[kid] = float(match.group(1))
                    else:
                        print(f"Total failure finding balance for {kid}")
                        balances[kid] = 0.0

        except Exception as e:
            print(f"Error scraping balances: {e}")
            page.screenshot(path="error_screenshot.png")
            print("Saved error_screenshot.png for debugging.")
        
        browser.close()
    
    return balances

def send_email(subject, body):
    sender = os.environ.get("EMAIL_SENDER")
    password = os.environ.get("EMAIL_APP_PASSWORD")
    receiver = os.environ.get("EMAIL_RECEIVER")
    
    if not sender or not password or not receiver:
        print("Email credentials not fully configured in .env")
        return
        
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver
    
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

def run_job(headless=True):
    print("Running PaySchools Scraper Job...")
    current_balances = scrape_balances(headless=headless)
    
    if not current_balances:
        print("No balances retrieved. Exiting.")
        return
        
    history = get_history()
    email_body = "PaySchools Daily Lunch Balance Summary:\n\n"
    
    spending_data = {}
    
    for kid, balance in current_balances.items():
        last_balance = history.get(kid, balance)
        spending = round(last_balance - balance, 2)
        spending_data[kid] = spending
        
        email_body += f"{kid}:\n"
        email_body += f"  Current Balance: ${balance:.2f}\n"
        if spending > 0:
            email_body += f"  Spent Yesterday: ${spending:.2f}\n"
        elif spending < 0:
            email_body += f"  Added to account: ${-spending:.2f}\n"
        else:
            email_body += f"  No spending yesterday.\n"
        email_body += "\n"
        
        history[kid] = balance
        
    save_history(history)
    print(email_body)
    
    melody_spent = spending_data.get("Melody Yu", 0)
    micah_spent = spending_data.get("Micah Yu", 0)
    
    if melody_spent > micah_spent:
        subject = f"Melody spent more than Micah! (${melody_spent:.2f} vs ${micah_spent:.2f})"
    elif micah_spent > melody_spent:
        subject = f"Micah spent more than Melody! (${micah_spent:.2f} vs ${melody_spent:.2f})"
    elif melody_spent > 0 and melody_spent == micah_spent:
        subject = f"Melody and Micah spent the same amount! (${melody_spent:.2f})"
    else:
        subject = "Daily PaySchools Lunch Balance"
        
    send_email(subject, email_body)

def test():
    """Ultimate test: Runs the full job but in headed mode so you can watch."""
    print("Running ULTIMATE test (headed browser, sends email, updates history)...")
    run_job(headless=False)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test()
    else:
        run_job()
