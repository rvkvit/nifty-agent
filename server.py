"""
Nifty Trading Agent — Render.com Production Server
"""

import os, json, hashlib, logging, csv, io, secrets, threading, time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, redirect, send_from_directory, session
from flask_cors import CORS
import requests as req

# ─── CONFIG (set in Render dashboard → Environment) ─────────
API_KEY = os.environ.get("KITE_API_KEY", "YOUR_API_KEY_HERE")
API_SECRET = os.environ.get("KITE_API_SECRET", "YOUR_API_SECRET_HERE")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:5000")
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "nifty2026")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(24))
PORT = int(os.environ.get("PORT", 5000))

KITE_BASE = "https://api.kite.trade"
REDIRECT_URL = f"{PUBLIC_URL}/callback"

app = Flask(__name__, static_folder="static")
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
CORS(app, origins=["*"])
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

state = {"access_token": None, "instruments": None, "inst_date": None}

# ─── KEEP-ALIVE (prevents Render free tier from sleeping) ───
def keep_alive():
    """Ping self every 10 min during market hours to stay awake."""
    while True:
        try:
            now = datetime.now()
            # Only ping during Indian market hours (IST = UTC+5:30)
            # Adjust if your Render server is in a different timezone
            hour = now.hour
            if 3 <= hour <= 11:  # ~9 AM to 5 PM IST roughly in UTC
                req.get(f"{PUBLIC_URL}/api/ping", timeout=5)
                logger.info("Keep-alive ping sent")
        except:
            pass
        time.sleep(600)  # Every 10 minutes

# Start keep-alive thread
if PUBLIC_URL != "http://localhost:5000":
    t = threading.Thread(target=keep_alive, daemon=True)
    t.start()
    logger.info("Keep-alive thread started")

# ─── HELPERS ────────────────────────────────────────────────
def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"error": "Not logged in"}), 401
        return f(*args, **kwargs)
    return decorated

def auth_header():
    if not state["access_token"]: return None
    return {"X-Kite-Version":"3","Authorization":f"token {API_KEY}:{state['access_token']}"}

def kite_get(ep, params=None):
    h = auth_header()
    if not h: return {"error":"Kite not connected"}
    try:
        r = req.get(f"{KITE_BASE}{ep}", headers=h, params=params, timeout=15)
        return r.json() if r.status_code == 200 else {"error":f"Kite {r.status_code}"}
    except Exception as e:
        return {"error":str(e)}

def load_instruments():
    today = datetime.now().strftime("%Y-%m-%d")
    if state["instruments"] and state["inst_date"] == today:
        return state["instruments"]
    h = auth_header()
    if not h: return []
    logger.info("Downloading NFO instruments...")
    try:
        r = req.get(f"{KITE_BASE}/instruments/NFO", headers=h, timeout=30)
        if r.status_code != 200: return []
        reader = csv.DictReader(io.StringIO(r.text))
        instruments = list(reader)
        state["instruments"] = instruments
        state["inst_date"] = today
        logger.info(f"Loaded {len(instruments)} instruments")
        return instruments
    except Exception as e:
        logger.error(f"Error: {e}")
        return []

def get_option_symbols(index_name, spot, num_strikes=10):
    instruments = load_instruments()
    if not instruments: return [], None
    name_filter = "NIFTY" if index_name.upper() == "NIFTY" else "BANKNIFTY"
    step = 50 if index_name.upper() == "NIFTY" else 100
    atm = round(spot / step) * step
    strikes_wanted = set(atm + i * step for i in range(-num_strikes, num_strikes + 1))
    today = datetime.now().strftime("%Y-%m-%d")
    matching, expiry_dates = [], set()
    for inst in instruments:
        if inst.get("instrument_type") not in ("CE", "PE"): continue
        if inst.get("name") != name_filter: continue
        if not inst.get("expiry") or inst["expiry"] < today: continue
        try: sv = float(inst["strike"])
        except: continue
        if sv in strikes_wanted:
            matching.append(inst)
            expiry_dates.add(inst["expiry"])
    if not matching: return [], None
    nearest = min(expiry_dates)
    return [m for m in matching if m["expiry"] == nearest], nearest

def find_specific_option(index_name, spot, direction):
    instruments = load_instruments()
    if not instruments: return None
    name_filter = "NIFTY" if index_name.upper() == "NIFTY" else "BANKNIFTY"
    step = 50 if index_name.upper() == "NIFTY" else 100
    atm = round(spot / step) * step
    today = datetime.now().strftime("%Y-%m-%d")
    opt_type = "CE" if direction == "BULLISH" else "PE"
    candidates = [atm, atm + step] if direction == "BULLISH" else [atm, atm - step]
    found = [i for i in instruments if i.get("name") == name_filter and i.get("instrument_type") == opt_type
             and i.get("expiry", "") >= today and float(i.get("strike", 0)) in candidates]
    if not found: return None
    nearest = min(set(f["expiry"] for f in found))
    found = [f for f in found if f["expiry"] == nearest]
    symbols = [f"NFO:{f['tradingsymbol']}" for f in found]
    quotes = kite_get("/quote", params=[("i", s) for s in symbols])
    qd = quotes.get("data", {})
    best, best_sc = None, -1
    for inst in found:
        sym = f"NFO:{inst['tradingsymbol']}"
        q = qd.get(sym, {})
        ltp, vol, oi = q.get("last_price", 0) or 0, q.get("volume", 0) or 0, q.get("oi", 0) or 0
        depth = q.get("depth", {})
        bid = depth.get("buy", [{}])[0].get("price", 0) if depth.get("buy") else 0
        ask = depth.get("sell", [{}])[0].get("price", 0) if depth.get("sell") else 0
        spread = abs(ask - bid) if ask and bid else 999
        sc = vol * 0.4 + oi * 0.4 + (1000 / (spread + 1)) * 0.2
        if 50 <= ltp <= 300: sc *= 1.5
        elif 30 <= ltp <= 500: sc *= 1.2
        if sc > best_sc:
            best_sc = sc
            best = {"tradingsymbol": inst["tradingsymbol"], "strike": float(inst["strike"]),
                     "type": opt_type, "expiry": inst["expiry"], "lot_size": int(inst.get("lot_size", 25)),
                     "ltp": ltp, "volume": vol, "oi": oi, "bid": bid, "ask": ask, "spread": round(spread, 2)}
    return best

# ─── PUBLIC ROUTES ──────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("authenticated"):
        return redirect("/access")
    return send_from_directory("static", "index.html")

@app.route("/access", methods=["GET", "POST"])
def access_page():
    error_html = ""
    if request.method == "POST":
        if request.form.get("password") == ACCESS_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            return redirect("/")
        error_html = '<div style="color:#ff1744;font-size:12px;margin-bottom:8px">Wrong password</div>'
    return f'''<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="NiftyAgent"><link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect fill='%23060a10' width='100' height='100' rx='20'/><text y='65' x='50' text-anchor='middle' font-size='50' fill='%2300e5ff'>N</text></svg>">
    <title>Nifty Agent</title>
    <style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,'SF Mono',monospace;background:#060a10;color:#e4eaf6;display:grid;place-items:center;height:100vh;padding:20px}}
    .box{{background:#111a2e;border:1px solid #1a2744;border-radius:16px;padding:32px;width:100%;max-width:360px;text-align:center}}
    input{{width:100%;padding:14px;border-radius:10px;border:1px solid #1a2744;background:#0c1220;color:#e4eaf6;font-size:16px;margin:16px 0;font-family:inherit;-webkit-appearance:none}}
    button{{width:100%;padding:14px;border-radius:10px;border:none;background:#00e5ff;color:#060a10;font-weight:700;font-size:16px;cursor:pointer;font-family:inherit;-webkit-appearance:none}}
    input:focus{{outline:none;border-color:#00e5ff}}</style></head>
    <body><div class="box"><div style="font-size:36px;margin-bottom:8px">📊</div>
    <div style="font-size:18px;font-weight:700;margin-bottom:4px">Nifty Trading Agent</div>
    <div style="font-size:12px;color:#5a6a8a;margin-bottom:4px">Smart Signals · Max 2/Day</div>
    {error_html}
    <form method="POST"><input name="password" type="password" placeholder="Enter access password" autofocus autocomplete="off">
    <button type="submit">Enter</button></form>
    <div style="margin-top:16px;font-size:10px;color:#5a6a8a">Add to Home Screen for app-like experience</div>
    </div></body></html>'''

# ─── ADMIN (only you) ──────────────────────────────────────
@app.route("/admin/login")
def admin_login():
    return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}")

@app.route("/callback")
def callback():
    rt = request.args.get("request_token")
    if not rt: return jsonify({"error": "No token"}), 400
    ck = hashlib.sha256(f"{API_KEY}{rt}{API_SECRET}".encode()).hexdigest()
    try:
        r = req.post(f"{KITE_BASE}/session/token",
                     data={"api_key": API_KEY, "request_token": rt, "checksum": ck},
                     headers={"X-Kite-Version": "3"}, timeout=10)
        d = r.json()
        if d.get("status") == "success":
            state["access_token"] = d["data"]["access_token"]
            load_instruments()
            count = len(state.get("instruments") or [])
            return f'''<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
            <body style="background:#060a10;color:#00e5ff;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;padding:20px;text-align:center">
            <div style="font-size:48px;margin-bottom:16px">✅</div>
            <h2>Kite Connected!</h2>
            <p style="color:#5a6a8a;margin-top:8px">{count} instruments loaded</p>
            <p style="color:#5a6a8a;margin-top:4px">Dashboard is now live for all users.</p>
            <p style="color:#5a6a8a;margin-top:4px">Close this tab.</p></body></html>'''
        return jsonify({"error": "Auth failed", "detail": d}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── API ────────────────────────────────────────────────────
@app.route("/api/ping")
def ping():
    return jsonify({"ok": True, "kite": state["access_token"] is not None})

@app.route("/api/status")
@require_login
def status():
    return jsonify({"authenticated": state["access_token"] is not None,
                    "instruments_loaded": state["instruments"] is not None,
                    "instruments_count": len(state["instruments"]) if state["instruments"] else 0,
                    "kite_connected": state["access_token"] is not None})

@app.route("/api/market-data/<index_name>")
@require_login
def market_data(index_name):
    h = auth_header()
    if not h: return jsonify({"error": "Kite not connected. Admin: visit /admin/login"}), 503
    spot_sym = "NSE:NIFTY 50" if index_name.upper() == "NIFTY" else "NSE:NIFTY BANK"
    sr = kite_get("/quote", params=[("i", spot_sym)])
    if "error" in sr: return jsonify(sr), 500
    sf = sr.get("data", {}).get(spot_sym, {})
    spot = sf.get("last_price", 0)
    if not spot: return jsonify({"error": "No spot"}), 500
    step = 50 if index_name.upper() == "NIFTY" else 100
    atm = round(spot / step) * step
    oi, expiry = get_option_symbols(index_name, spot, 10)
    rows, pcr_val, mp = [], 0, atm
    if oi:
        syms = [f"NFO:{i['tradingsymbol']}" for i in oi]
        cd = {}
        for i in range(0, len(syms), 500):
            br = kite_get("/quote", params=[("i", s) for s in syms[i:i+500]])
            if "data" in br: cd.update(br["data"])
        sl = {(float(i["strike"]), i["instrument_type"]): f"NFO:{i['tradingsymbol']}" for i in oi}
        for strike in sorted(set(float(i["strike"]) for i in oi)):
            ce, pe = cd.get(sl.get((strike,"CE"),""),{}), cd.get(sl.get((strike,"PE"),""),{})
            rows.append({"strike":int(strike),
                "ce":{"ltp":ce.get("last_price",0)or 0,"oi":ce.get("oi",0)or 0,"oiChange":(ce.get("oi",0)or 0)-(ce.get("oi_day_low",0)or 0),"volume":ce.get("volume",0)or 0,"iv":0},
                "pe":{"ltp":pe.get("last_price",0)or 0,"oi":pe.get("oi",0)or 0,"oiChange":(pe.get("oi",0)or 0)-(pe.get("oi_day_low",0)or 0),"volume":pe.get("volume",0)or 0,"iv":0}})
        tpo,tco = sum(r["pe"]["oi"] for r in rows),sum(r["ce"]["oi"] for r in rows)
        pcr_val = round(tpo/tco,2) if tco>0 else 0
        mv = float("inf")
        for r in rows:
            p = sum(max(0,o["ce"]["oi"]*(r["strike"]-o["strike"]))+max(0,o["pe"]["oi"]*(o["strike"]-r["strike"])) for o in rows)
            if p<mv: mv,mp = p,r["strike"]
    return jsonify({"data":{"spot":spot,"spot_ohlc":sf.get("ohlc",{}),"atm":atm,"pcr":pcr_val,"max_pain":mp,"chain":rows,"expiry":expiry,
        "recommended_ce":find_specific_option(index_name,spot,"BULLISH"),"recommended_pe":find_specific_option(index_name,spot,"BEARISH"),
        "timestamp":datetime.now().isoformat()}})

@app.route("/api/option-chain/<index_name>")
@require_login
def oc_compat(index_name):
    return market_data(index_name)

@app.route("/<path:path>")
def serve_static(path):
    if not session.get("authenticated"): return redirect("/access")
    return send_from_directory("static", path)

if __name__ == "__main__":
    print(f"\n{'='*55}\n  NIFTY TRADING AGENT\n  URL: {PUBLIC_URL}\n  Password: {ACCESS_PASSWORD}\n  Admin: {PUBLIC_URL}/admin/login\n{'='*55}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
