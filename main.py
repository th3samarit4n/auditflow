import os
import requests
import uvicorn
import sqlite3
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from groq import Groq
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --- 1. BOOT SEQUENCE & FORCED CONFIG ---
# override=True ensures that Railway environment variables take priority over local files
env_path = Path('.') / '.env.local'
load_dotenv(dotenv_path=env_path, override=True)

# Railway Volume Logic (Defaults to leads.db in root)
DB_PATH = os.getenv("DATABASE_URL", "leads.db")

print(f"\n--- AUDITFLOW AI: MASTER BOOT SEQUENCE ---")
print(f"DEBUG: Database Path: {DB_PATH}")

current_groq = os.getenv("GROQ_API_KEY")
if current_groq:
    print(f"DEBUG: Groq Key Detected: {current_groq[:10]}...")
else:
    print("DEBUG: ERROR - GROQ_API_KEY NOT FOUND!")

def init_db():
    """Initializes the SQLite lead-tracking database."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS audits 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  brand TEXT, url TEXT, score INTEGER, 
                  cms TEXT, scripts INTEGER, timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_db()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# API Config
PSI_API_KEY = os.getenv("PAGESPEED_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELE_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- 2. ENGINE LOGIC ---

def get_brand_name(url):
    """Cleanly extracts the brand name from the URL."""
    try:
        domain = url.split("//")[-1].split("www.")[-1].split(".")[0]
        if "-" in domain:
            return " ".join([word.capitalize() for word in domain.split("-")])
        return domain.capitalize()
    except:
        return "the team"

def get_tech_audit(url):
    """Scrapes for CMS and counts all <script> tags."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        html = res.text.lower()
        
        cms = "Custom/Modern"
        if 'wp-content' in html: cms = "WordPress"
        elif 'shopify' in html: cms = "Shopify"
        elif 'wix' in html: cms = "Wix"
        elif 'webflow' in html: cms = "Webflow"
        
        script_count = len(soup.find_all('script'))
        return {"cms": cms, "scripts": script_count}
    except:
        return {"cms": "Unknown", "scripts": 0}

def get_performance_audit(url):
    """Fetches full Lighthouse metrics from Google."""
    try:
        api_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&key={PSI_API_KEY}&strategy=mobile"
        r = requests.get(api_url).json()
        
        audits = r['lighthouseResult']['audits']
        score = r['lighthouseResult']['categories']['performance']['score'] * 100
        tti = audits['interactive']['displayValue']
        fcp = audits['first-contentful-paint']['displayValue']
        
        return {"score": int(score), "tti": tti, "fcp": fcp}
    except:
        return {"score": 0, "tti": "N/A", "fcp": "N/A"}

def push_to_telegram(message):
    """Pushes the result to your phone via Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        payload = {"chat_id": TELE_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except:
        pass

# --- 3. ROUTES (FIXED FOR PYTHON 3.13) ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Fetch Audit History and Total Count
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, brand, url, score, cms, timestamp FROM audits ORDER BY id DESC LIMIT 15")
    history = c.fetchall()
    c.execute("SELECT COUNT(*) FROM audits")
    total_leads = c.fetchone()[0]
    conn.close()
    
    # CRITICAL FIX: Use named keywords (request=request, name=..., context=...)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"history": history, "total": total_leads}
    )

@app.post("/analyze")
async def run_audit(request: Request, url: str = Form(...)):
    target_url = url.strip()
    if not target_url.startswith("http"):
        target_url = "https://" + target_url
    
    # 1. Run Data Ingestion
    brand = get_brand_name(target_url)
    tech = get_tech_audit(target_url)
    perf = get_performance_audit(target_url)

    # 2. Save Audit to Database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO audits (brand, url, score, cms, scripts, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
              (brand, target_url, perf['score'], tech['cms'], tech['scripts'], datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

    # 3. AI Personalization (Human Peer-to-Peer Prompt)
    prompt = f"""
    You are a developer. Write a casual, 2-sentence Instagram DM to the owner of {brand}.
    Data: Site uses {tech['cms']}, Mobile Score is {perf['score']}/100, Load Time is {perf['tti']}.
    
    Rules:
    - NO links/URLs.
    - Start with "Hi {brand} team" or "Hey {brand}".
    - Mention the site feels slow on mobile (taking {perf['tti']}).
    - Offer a free 60-second video on one specific fix.
    - Tone: Expert peer, helpful, zero marketing bot-speak.
    """

    try:
        groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        )
        dm_output = chat_completion.choices[0].message.content
    except Exception as e:
        dm_output = f"AI Error: {str(e)}"

    # 4. Telegram Notification
    tele_msg = f"🚀 *Audit #{brand} Done!*\n⚡ Score: {perf['score']}\n🛠 CMS: {tech['cms']}\n\n*Draft DM:*\n{dm_output}"
    push_to_telegram(tele_msg)

    # 5. Refresh History
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, brand, url, score, cms, timestamp FROM audits ORDER BY id DESC LIMIT 15")
    history = c.fetchall()
    c.execute("SELECT COUNT(*) FROM audits")
    total_leads = c.fetchone()[0]
    conn.close()

    # CRITICAL FIX: Use named keywords
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "result": {"brand": brand, "url": target_url, "perf": perf, "tech": tech, "dm": dm_output},
            "history": history,
            "total": total_leads
        }
    )

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)