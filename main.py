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

# --- 1. CONFIG & DATABASE INITIALIZATION ---
env_path = Path('.') / '.env.local'
load_dotenv(dotenv_path=env_path, override=True)

# DEBUG: Verify key loading
current_key = os.getenv("GROQ_API_KEY")
print(f"\n--- AUDITFLOW AI BOOT SEQUENCE ---")
if current_key:
    print(f"STATUS: SUCCESS - Groq Key Loaded: {current_key[:10]}...")
else:
    print("STATUS: ERROR - No GROQ_API_KEY found in .env.local!")

def init_db():
    conn = sqlite3.connect('leads.db')
    c = conn.cursor()
    # Stores: Brand Name, URL, Performance Score, CMS, Script Count, Timestamp
    c.execute('''CREATE TABLE IF NOT EXISTS audits 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  brand TEXT, url TEXT, score INTEGER, 
                  cms TEXT, scripts INTEGER, timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_db()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize API Config
PSI_API_KEY = os.getenv("PAGESPEED_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELE_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- 2. HELPER FUNCTIONS ---

def get_brand_name(url):
    """Extracts a clean brand name from the URL."""
    try:
        domain = url.split("//")[-1].split("www.")[-1].split(".")[0]
        if "-" in domain:
            return " ".join([word.capitalize() for word in domain.split("-")])
        return domain.capitalize()
    except:
        return "the team"

def get_tech_audit(url):
    """Detects CMS and counts external scripts."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
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
    """Fetches full Lighthouse metrics."""
    try:
        api_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&key={PSI_API_KEY}&strategy=mobile"
        r = requests.get(api_url).json()
        
        score = r['lighthouseResult']['categories']['performance']['score'] * 100
        tti = r['lighthouseResult']['audits']['interactive']['displayValue']
        fcp = r['lighthouseResult']['audits']['first-contentful-paint']['displayValue']
        
        return {"score": int(score), "tti": tti, "fcp": fcp}
    except:
        return {"score": 0, "tti": "N/A", "fcp": "N/A"}

def push_to_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        payload = {"chat_id": TELE_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except:
        pass

# --- 3. ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Fetch History from Database
    conn = sqlite3.connect('leads.db')
    c = conn.cursor()
    c.execute("SELECT id, brand, url, score, cms, timestamp FROM audits ORDER BY id DESC LIMIT 15")
    history = c.fetchall()
    
    # Get Total Lead Count
    c.execute("SELECT COUNT(*) FROM audits")
    total_leads = c.fetchone()[0]
    conn.close()
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "history": history, 
        "total": total_leads
    })

@app.post("/analyze")
async def run_audit(request: Request, url: str = Form(...)):
    target_url = url.strip()
    if not target_url.startswith("http"):
        target_url = "https://" + target_url
    
    # 1. Gather All Data
    brand = get_brand_name(target_url)
    tech = get_tech_audit(target_url)
    perf = get_performance_audit(target_url)

    # 2. Save Audit to SQLite Database
    conn = sqlite3.connect('leads.db')
    c = conn.cursor()
    c.execute("INSERT INTO audits (brand, url, score, cms, scripts, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
              (brand, target_url, perf['score'], tech['cms'], tech['scripts'], datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

    # 3. Generate AI Outreach (The Human-Peer Prompt)
    prompt = f"""
    Context: You are a high-level developer. Write a casual, 1-2 sentence Instagram DM.
    Data: Brand is {brand}, Platform is {tech['cms']}, Load Time is {perf['tti']}.
    
    Rules:
    - NEVER include any URLs or links.
    - Start with "Hi {brand} team" or "Hey {brand}".
    - Mention that the {tech['cms']} site is lagging (taking {perf['tti']}).
    - Offer a 60-second video on one specific technical fix.
    - Sound like a real human peer, not an agency bot. No "checking out your site" or "hope this finds you well".
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

    # Re-fetch history for the template refresh
    conn = sqlite3.connect('leads.db')
    c = conn.cursor()
    c.execute("SELECT id, brand, url, score, cms, timestamp FROM audits ORDER BY id DESC LIMIT 15")
    history = c.fetchall()
    c.execute("SELECT COUNT(*) FROM audits")
    total_leads = c.fetchone()[0]
    conn.close()

    return templates.TemplateResponse("index.html", {
        "request": request, 
        "result": {"brand": brand, "url": target_url, "perf": perf, "tech": tech, "dm": dm_output},
        "history": history,
        "total": total_leads
    })

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)