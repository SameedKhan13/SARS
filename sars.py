# main.py
import os
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ---- Settings ----
class Settings:
    def __init__(self):
        self.headless = True  # ✅ Headless is now required for EC2
        self.browser_timeout = 30
        self.keep_browser_open = False  # EC2 should not keep browser open
        self.chrome_user_data_dir = os.getenv("CHROME_USER_DATA_DIR")
        self.chrome_profile = os.getenv("CHROME_PROFILE_DIR")

settings = Settings()

# ---- FastAPI app ----
app = FastAPI(title="SARS eFiling Automation API")
executor = ThreadPoolExecutor(max_workers=2)

# ---- Selenium driver ----
persistent_driver: Optional[webdriver.Chrome] = None

def create_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # Headless mode
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")  # Required for EC2
    chrome_options.add_argument("--disable-dev-shm-usage")  # Shared memory issues
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    # Optional: use profile if provided
    if settings.chrome_user_data_dir and settings.chrome_profile:
        chrome_options.add_argument(f"--user-data-dir={settings.chrome_user_data_dir}")
        chrome_options.add_argument(f"--profile-directory={settings.chrome_profile}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(settings.browser_timeout + 30)
    return driver

def get_driver() -> webdriver.Chrome:
    global persistent_driver
    if persistent_driver is None:
        persistent_driver = create_driver()
    return persistent_driver

# ---- Login ----
def login_action(driver: webdriver.Chrome, username: str, password: str) -> dict:
    try:
        driver.get("https://secure.sarsefiling.co.za/app/login")
        wait = WebDriverWait(driver, settings.browser_timeout)

        username_input = wait.until(EC.presence_of_element_located((By.ID, "username")))
        username_input.clear()
        username_input.send_keys(username)

        next_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//button[.//span[normalize-space(text())='Next']]")))
        driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
        time.sleep(0.5)
        try:
            next_btn.click()
        except:
            driver.execute_script("arguments[0].click();", next_btn)

        wait.until(EC.url_contains("/app/auth/password"))
        password_input = wait.until(EC.presence_of_element_located((By.ID, "password")))
        password_input.clear()
        password_input.send_keys(password)

        login_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//button[.//span[normalize-space(text())='Login']]")))
        driver.execute_script("arguments[0].scrollIntoView(true);", login_btn)
        time.sleep(0.5)
        try:
            login_btn.click()
        except:
            driver.execute_script("arguments[0].click();", login_btn)

        wait.until(lambda d: "/app/login" not in d.current_url.lower() and "/app/auth/password" not in d.current_url.lower())

        return {"status": "ok", "message": "Login successful", "current_url": driver.current_url}

    except Exception as e:
        ts = int(time.time())
        screenshot_path = f"screenshot_{ts}.png"
        try:
            driver.save_screenshot(screenshot_path)
        except:
            pass
        return {"status": "error", "message": f"Login failed: {e}", "screenshot": screenshot_path}

# ---- Scrape Dashboard ----
def scrape_organization_dashboard(driver: webdriver.Chrome) -> dict:
    try:
        dashboard_url = "https://secure.sarsefiling.co.za/app/dashboard/organization"
        driver.get(dashboard_url)
        wait = WebDriverWait(driver, settings.browser_timeout)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3)

        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(separator="\n", strip=True)

        return {"status": "ok", "dashboard_url": dashboard_url, "text": page_text}

    except Exception as e:
        return {"status": "error", "message": f"scrape_organization_dashboard failed: {e}"}

# ---- Runner ----
def run_action_sync(action: str, payload: Optional[dict] = None) -> dict:
    driver = None
    payload = payload or {}
    try:
        driver = create_driver()  # Always headless on EC2

        username = payload.get("username")
        password = payload.get("password")

        if action.lower() == "login":
            if not username or not password:
                return {"status": "error", "message": "Missing 'username' or 'password'"}
            return login_action(driver, username, password)

        elif action.lower() == "scrape_dashboard":
            if not username or not password:
                return {"status": "error", "message": "Missing 'username' or 'password'"}
            # Login first
            login_resp = login_action(driver, username, password)
            if login_resp.get("status") != "ok":
                return login_resp
            # Scrape dashboard
            return scrape_organization_dashboard(driver)

        else:
            return {"status": "error", "message": f"Unknown action '{action}'"}

    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ---- FastAPI endpoints ----
@app.post("/run")
async def run(request: Request):
    try:
        body = await request.json()
        action = body.get("action")
        payload = body.get("payload", {})

        if not action:
            return {"status": "error", "message": "Missing 'action'"}

        future = executor.submit(run_action_sync, action, payload)
        result = future.result(timeout=300)
        return result
    except Exception as e:
        return {"status": "error", "message": f"Unexpected server error: {e}"}

@app.get("/ping")
def ping():
    return {"message": "ping - server up"}
