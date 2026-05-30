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

# --- 1. BOOT SEQUENCE & CONFIG ---
# Force load from .env.local and override any system-cached keys
env_path = Path('.') / '.env.local'
load_dotenv(dotenv_path=env_path, override=True)

# Railway Persistent Storage Logic
DB_PATH = os.getenv("DATABASE_URL", "leads.db")

print(f"\n--- AUDITFLOW AI: MASTER BOOT SEQUENCE ---")
print(f"DEBUG: Database Path: {DB_PATH}")

current_groq = os.getenv("GROQ_API_KEY")
if current_groq:
    print(f"DEBUG: Groq API Key Detected: {current_groq[:10]}...")
else:
    print("DEBUG: !!! ERROR - GROQ_API_KEY NOT FOUND !!!")

def init_db():
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

# Initialize API Config from Environment
PSI_API_KEY = os.getenv("PAGESPEED_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELE_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- 2. THE AUDIT ENGINE ---

def get_brand_name(url):
    """Extracts a clean brand name from the URL for personalization."""
    try:
        domain = url.split("//")[-1].split("www.")[-1].split(".")[0]
        if "-" in domain:
            return " ".join([word.capitalize() for word in domain.split("-")])
        return domain.capitalize()
    except:
        return "the team"

def get_tech_audit(url):
    """Scrapes site for CMS platform and total external scripts."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
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
    """Fetches Mobile Lighthouse score, TTI, and FCP."""
    try:
        api_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&key={PSI_API_KEY}&strategy=mobile"
        r = requests.get(api_url).json()
        
        audits = r['lighthouseResult']['audits']
        score = r['lighthouseResult']['categories']['performance']['score'] * 100
        tti = audits['interactive']['displayValue']
        fcp = audits['first-contentful-paint']['displayValue']
        
        return {"score": int(score), "tti": tti, "fcp": fcp}
    except Exception as e:
        print(f"PSI API Error: {e}")
        return {"score": 0, "tti": "N/A", "fcp": "N/A"}

def push_to_telegram(message):
    """Sends the drafted DM and audit stats to your phone."""
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        payload = {"chat_id": TELE_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except:
        pass

# --- 3. THE DASHBOARD ROUTES (PYTHON 3.13 COMPLIANT) ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Fetch audit history
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, brand, url, score, cms, timestamp FROM audits ORDER BY id DESC LIMIT 15")
    history = c.fetchall()
    
    # Get total counter
    c.execute("SELECT COUNT(*) FROM audits")
    total_leads = c.fetchone()[0]
    conn.close()
    
    # CRITICAL FIX FOR PYTHON 3.13: Keyword arguments required
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

    # 2. Save Audit to SQLite
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO audits (brand, url, score, cms, scripts, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
              (brand, target_url, perf['score'], tech['cms'], tech['scripts'], datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

    # 3. AI Hyper-Personalization (The Full Human-Peer Prompt)
    prompt = f"""
    You are a developer who noticed a specific technical flaw on a site. 
    Write a casual, 2-sentence Instagram DM to the owner of {brand}.
    
    DATA:
    - Platform: {tech['cms']}
    - Mobile Load Time: {perf['tti']}
    - Score: {perf['score']}/100

    RULES:
    1. NO links or URLs.
    2. Start with "Hey {brand} team" or "Hi {brand}".
    3. Mention the {tech['cms']} setup feels heavy on mobile (taking {perf['tti']}).
    4. Reference that a 1s delay drops conversions by 7%.
    5. Offer a free 60-second video on how to defer the scripts to fix it.
    6. Sound like a human peer, NO marketing bot jargon.
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
    tele_msg = f"🚀 *Audit #{brand} Complete!*\n⚡ Score: {perf['score']}\n🛠 CMS: {tech['cms']}\n\n*Draft DM:*\n{dm_output}"
    push_to_telegram(tele_msg)

    # 5. Fetch fresh history for the UI refresh
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, brand, url, score, cms, timestamp FROM audits ORDER BY id DESC LIMIT 15")
    history = c.fetchall()
    c.execute("SELECT COUNT(*) FROM audits")
    total_leads = c.fetchone()[0]
    conn.close()

    # CRITICAL FIX FOR PYTHON 3.13: Keyword arguments required
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
