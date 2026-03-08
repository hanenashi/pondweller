import os
import time
import logging
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright, BrowserContext, Page
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
CONFIG = {
    "TARGET_CLUB_ID": "prezident_donald_trump_-_zivot_a_dilo",
    "FIREBASE_KEY_PATH": "serviceAccountKey.json",
    "USER_DATA_DIR": os.path.join("data", "browser_profile"),
    
    "OKOUN_USER": os.getenv("OKOUN_USER", "blaznik"),
    "OKOUN_PASS": os.getenv("OKOUN_PASS", ""), 
    
    "PAGES_TO_SCRAPE": 1,   
    "HEADLESS": False,  
    "KEEP_BROWSER_OPEN": True,
}

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pondweller.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==========================================
# MODULES
# ==========================================

NOISE_BLOCKLIST = [
    "xgemius", "gemius.pl", "googletagmanager", "google-analytics", "hit.gemius.pl", "lsget"
]

def launch_context(headless: bool = False) -> tuple[Any, BrowserContext, Page]:
    os.makedirs(CONFIG["USER_DATA_DIR"], exist_ok=True)
    pw = sync_playwright().start()
    
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-infobars",
        "--start-maximized" if not headless else "--window-size=1920,1080"
    ]

    context = pw.chromium.launch_persistent_context(
        user_data_dir=CONFIG["USER_DATA_DIR"],
        headless=headless,
        args=args,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport=None, 
    )
    
    page = context.pages[0] if context.pages else context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    
    def route_interceptor(route):
        if any(noise in route.request.url for noise in NOISE_BLOCKLIST):
            route.abort()
        else:
            route.continue_()
            
    page.route("**/*", route_interceptor)
    return pw, context, page

def teardown(pw, context):
    try: context.close()
    except: pass
    try: pw.stop()
    except: pass

def init_firebase():
    logger.info("Initializing Firebase Vault...")
    cred = credentials.Certificate(CONFIG["FIREBASE_KEY_PATH"])
    firebase_admin.initialize_app(cred)
    return firestore.client()

def ensure_login(page):
    logger.info("Checking authentication status...")
    page.goto("https://www.okoun.cz/myBoards.jsp", wait_until="domcontentloaded")
    
    if page.locator("form.login").count() > 0:
        logger.info(f"Login required. Authenticating as '{CONFIG['OKOUN_USER']}'...")
        page.fill("form.login input[name='login']", CONFIG["OKOUN_USER"])
        page.fill("form.login input[name='password']", CONFIG["OKOUN_PASS"])
        page.click("form.login button.submit")
        
        try:
            page.wait_for_selector("div.user b", timeout=5000)
            logger.info("Login successful.")
        except Exception:
            logger.warning("Login submitted but could not strictly verify. Proceeding...")
    else:
        logger.info("Session active. Already logged in.")

def scrape_club(page, club_id: str, pages_to_scrape: int) -> List[Dict]:
    url = f"https://www.okoun.cz/boards/{club_id}"
    logger.info(f"Navigating to target club: {url}")
    
    page.goto(url, wait_until="domcontentloaded")
    
    all_posts = []

    for current_page in range(1, pages_to_scrape + 1):
        logger.info(f"Harvesting page {current_page}/{pages_to_scrape}...")
        
        try:
            # ---> FIXED WAIT SELECTOR <---
            page.wait_for_selector("div.item[id^='article-']", timeout=15000)
        except Exception as e:
            logger.warning(f"Failed to find posts on page! Error: {e}")
            break

        # ---> FIXED OKOUN PARSER BASED ON HTML DUMP <---
        posts_on_page = page.evaluate("""() => {
            const results = [];
            // Select all divs that have an ID starting with 'article-'
            const elements = document.querySelectorAll('div.item[id^="article-"]'); 
            
            elements.forEach(el => {
                try {
                    // Extract ID directly from the div's ID attribute
                    const p_id = parseInt(el.id.replace('article-', ''), 10);

                    // Extract author from span.user
                    const auth_el = el.querySelector('span.user');
                    const auth = auth_el ? auth_el.innerText.trim() : 'Anon';

                    // Extract the HTML content from div.content
                    const body_el = el.querySelector('div.content');
                    let html = body_el ? body_el.innerHTML.trim() : '';

                    if (p_id && html) {
                        results.push({ p_id, auth, html, ts: Date.now() });
                    }
                } catch (err) {}
            });
            return results;
        }""")
        
        all_posts.extend(posts_on_page)
        logger.info(f"Found {len(posts_on_page)} posts on this page.")

        if current_page < pages_to_scrape:
            try:
                older_link = page.locator("a.older, li.older a").first
                if older_link.count() > 0:
                    logger.info("Navigating to older posts...")
                    older_link.click()
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)
                else:
                    logger.info("No older pages found. Reached the beginning.")
                    break
            except Exception as e:
                logger.warning(f"Could not navigate to older page: {e}")
                break

    unique_posts = {p['p_id']: p for p in all_posts}.values()
    final_list = list(unique_posts)
    logger.info(f"Total unique posts harvested: {len(final_list)}")
    return final_list

def push_to_vault(db, club_id: str, posts: List[Dict]):
    if not posts:
        logger.warning("No posts to sync. Skipping Vault upload.")
        return

    logger.info(f"Syncing {len(posts)} posts to Vault (Firestore)...")
    collection_ref = db.collection('clubs').document(club_id).collection('posts')
    
    chunks = [posts[i:i + 450] for i in range(0, len(posts), 450)]
    
    for chunk in chunks:
        batch = db.batch()
        for post in chunk:
            doc_ref = collection_ref.document(str(post['p_id']))
            batch.set(doc_ref, post, merge=True)
        batch.commit()
        
    logger.info("Vault Sync Complete.")

def run_harvester():
    db = init_firebase()
    
    logger.info("Booting Playwright Engine (PIKER Engine)...")
    pw, context, page = launch_context(headless=CONFIG["HEADLESS"])
    
    try:
        ensure_login(page)
        posts = scrape_club(page, CONFIG["TARGET_CLUB_ID"], CONFIG["PAGES_TO_SCRAPE"])
        push_to_vault(db, CONFIG["TARGET_CLUB_ID"], posts)
    except Exception as e:
        logger.error(f"Harvester encountered a failure: {e}")
    finally:
        # ---> FIXED: Browser stays open at the END of the run <---
        if CONFIG.get("KEEP_BROWSER_OPEN") and not CONFIG["HEADLESS"]:
            logger.info("⏸️ KEEP_BROWSER_OPEN is True. Browser will stay open for 5 minutes. Close window manually to end early.")
            try:
                for _ in range(300):
                    page.wait_for_timeout(1000)
            except Exception:
                pass # Fails silently if you manually click the 'X' on the browser
                
        logger.info("Shutting down Harvester.")
        teardown(pw, context)
        
if __name__ == "__main__":
    logger.info("=== MURKYPOND HARVESTER STARTED ===")
    run_harvester()