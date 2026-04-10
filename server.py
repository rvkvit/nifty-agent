"""
Nifty Trading Agent v5 — Production Server
Clean architecture. Multi-user API keys. Admin controls.
"""
import os,json,hashlib,logging,csv,io,secrets,threading,time,copy
from datetime import datetime,timedelta
from functools import wraps
from flask import Flask,jsonify,request,redirect,send_from_directory,session
from flask_cors import CORS
import requests as rq

# ─── CONFIG ─────────────────────────────────────────────────
ADMIN_API_KEY=os.environ.get("KITE_API_KEY","")
ADMIN_API_SECRET=os.environ.get("KITE_API_SECRET","")
PUBLIC_URL=os.environ.get("PUBLIC_URL","http://localhost:5000")
ACCESS_PASSWORD=os.environ.get("ACCESS_PASSWORD","nifty2026")
ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD","admin2026")
SECRET_KEY=os.environ.get("SECRET_KEY",secrets.token_hex(24))
PORT=int(os.environ.get("PORT",5000))
TG_BOT_TOKEN=os.environ.get("TG_BOT_TOKEN","")
TG_CHANNEL_ID=os.environ.get("TG_CHANNEL_ID","")
TG_ENABLED=bool(TG_BOT_TOKEN and TG_CHANNEL_ID)
KITE="https://api.kite.trade"

app=Flask(__name__,static_folder="static")
app.secret_key=SECRET_KEY
app.config.update(SESSION_COOKIE_SAMESITE="Lax",PERMANENT_SESSION_LIFETIME=timedelta(hours=14))
CORS(app,origins=["*"])
logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)

# ─── STATE ──────────────────────────────────────────────────
admin_state={"token":None,"instruments":None,"inst_date":None}
user_sessions={}  # user_id -> {token, api_key, api_secret}
signal_history=[]
HIST_FILE="/tmp/nifty_signals.json"
def save_signals():
    try:
        with open(HIST_FILE,"w") as f:json.dump(signal_history[:100],f)
    except:pass
def load_signals():
    global signal_history
    try:
        with open(HIST_FILE,"r") as f:signal_history=json.load(f)
    except:signal_history=[]
load_signals()
scan_results={"picks":[],"last_scan":None,"scanning":False}
scanner_cfg={"enabled":False,"auto":False,"interval":10}

NIFTY50=["ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
"BAJAJ-AUTO","BAJFINANCE","BAJAJFINSV","BEL","BPCL","BHARTIARTL","BRITANNIA",
"CIPLA","COALINDIA","DRREDDY","EICHERMOT","GRASIM","HCLTECH","HDFCBANK",
"HDFCLIFE","HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
"INFY","ITC","JSWSTEEL","KOTAKBANK","LT","M&M","MARUTI","NESTLEIND","NTPC",
"ONGC","POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN","SUNPHARMA",
"TATACONSUM","TATAMOTORS","TATASTEEL","TCS","TECHM","TITAN","TRENT",
"ULTRACEMCO","WIPRO"]

# ─── HELPERS ────────────────────────────────────────────────
def get_kite_auth():
    """Get the best available Kite auth — user's own or admin's."""
    uid=session.get("user_id")
    if uid and uid in user_sessions and user_sessions[uid].get("token"):
        us=user_sessions[uid]
        return {"X-Kite-Version":"3","Authorization":f"token {us['api_key']}:{us['token']}"}
    if admin_state["token"]:
        return {"X-Kite-Version":"3","Authorization":f"token {ADMIN_API_KEY}:{admin_state['token']}"}
    return None

def kite_get(ep,params=None,auth_override=None):
    h=auth_override or get_kite_auth()
    if not h:return {"error":"Not connected to Kite"}
    try:
        r=rq.get(f"{KITE}{ep}",headers=h,params=params,timeout=15)
        return r.json() if r.status_code==200 else {"error":f"Kite error {r.status_code}"}
    except Exception as e:return {"error":str(e)}

def load_instruments():
    today=datetime.now().strftime("%Y-%m-%d")
    if admin_state["instruments"] and admin_state["inst_date"]==today:
        return admin_state["instruments"]
    h=get_kite_auth()
    if not h:return []
    try:
        r=rq.get(f"{KITE}/instruments/NFO",headers=h,timeout=30)
        if r.status_code!=200:return []
        admin_state["instruments"]=list(csv.DictReader(io.StringIO(r.text)))
        admin_state["inst_date"]=today
        log.info(f"Loaded {len(admin_state['instruments'])} instruments")
        return admin_state["instruments"]
    except:return []

def require_auth(f):
    @wraps(f)
    def d(*a,**k):
        if not session.get("authenticated"):return jsonify({"error":"Login required"}),401
        return f(*a,**k)
    return d

def send_tg(msg):
    if not TG_ENABLED:return
    try:rq.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",json={"chat_id":TG_CHANNEL_ID,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True},timeout=5)
    except:pass

def format_signal_tg(d):
    dr="\U0001f7e2" if d.get("dir")=="BULLISH" else "\U0001f534"
    conf=d.get('conf',0)
    bars="\u2588"*min(conf,7)+"\u2591"*(7-min(conf,7))
    return f"""{dr} <b>NIFTY AGENT \u2014 NEW SIGNAL</b> {dr}

\U0001f4cb <b>{d.get('tradingsymbol','')}</b>
Direction: <b>{d.get('dir','')}</b>
Confidence: [{bars}] {conf}/7

\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

\U0001f4b0 <b>ENTRY:</b> \u20b9{d.get('entryPrice',0)}
\u26d4 <b>STOP LOSS:</b> \u20b9{d.get('slPrice',0)}
\U0001f3af <b>TARGET 1:</b> \u20b9{d.get('t1Price',0)} (book 50%)
\U0001f680 <b>TARGET 2:</b> \u20b9{d.get('t2Price',0)} (trail SL)

\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501

\U0001f4ca Lots: {d.get('lots',1)} \u00d7 {d.get('lotSize',65)}
\U0001f4b8 Max Risk: \u20b9{d.get('totalRisk',0)}
\U0001f48e Potential: \u20b9{d.get('totalReward',0)}
\u2696\ufe0f Risk:Reward = 1:{d.get('rr',0)}

\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501
\U0001f550 {d.get('time','')} | Spot: {d.get('spotAtSignal','')}
OI: {d.get('oi',0)} | Vol: {d.get('volume',0)}

\u26a0\ufe0f <i>Max 2% capital risk. Always use SL.</i>"""

# ─── OPTION FINDING ─────────────────────────────────────────
def find_options(idx,spot):
    insts=load_instruments()
    if not insts:return None,None,[]
    nf="NIFTY" if idx.upper()=="NIFTY" else "BANKNIFTY"
    step=50 if idx.upper()=="NIFTY" else 100
    atm=round(spot/step)*step
    today=datetime.now().strftime("%Y-%m-%d")
    sw=set(atm+i*step for i in range(-10,11))
    matching=[i for i in insts if i.get("name")==nf and i.get("instrument_type") in("CE","PE") and i.get("expiry","")>=today]
    matching=[i for i in matching if float(i.get("strike",0)) in sw]
    if not matching:return None,None,[]
    exp=min(set(i["expiry"] for i in matching))
    matching=[i for i in matching if i["expiry"]==exp]
    # Fetch quotes
    syms=[f"NFO:{i['tradingsymbol']}" for i in matching]
    cd={}
    for i in range(0,len(syms),500):
        br=kite_get("/quote",params=[("i",s) for s in syms[i:i+500]])
        if "data" in br:cd.update(br["data"])
    # Build chain
    sl={(float(i["strike"]),i["instrument_type"]):i for i in matching}
    chain=[]
    for strike in sorted(set(float(i["strike"]) for i in matching)):
        cei=sl.get((strike,"CE"));pei=sl.get((strike,"PE"))
        ce=cd.get(f"NFO:{cei['tradingsymbol']}",{}) if cei else {}
        pe=cd.get(f"NFO:{pei['tradingsymbol']}",{}) if pei else {}
        chain.append({"strike":int(strike),
            "ce":{"ltp":ce.get("last_price",0)or 0,"oi":ce.get("oi",0)or 0,"oiChange":(ce.get("oi",0)or 0)-(ce.get("oi_day_low",0)or 0),"volume":ce.get("volume",0)or 0},
            "pe":{"ltp":pe.get("last_price",0)or 0,"oi":pe.get("oi",0)or 0,"oiChange":(pe.get("oi",0)or 0)-(pe.get("oi_day_low",0)or 0),"volume":pe.get("volume",0)or 0}})
    # Best CE and PE
    def best_opt(direction):
        ot="CE" if direction=="BULLISH" else "PE"
        cands=[atm,atm+step] if direction=="BULLISH" else [atm,atm-step]
        opts=[i for i in matching if i["instrument_type"]==ot and float(i["strike"]) in cands]
        if not opts:return None
        best,bs=None,-1
        for inst in opts:
            q=cd.get(f"NFO:{inst['tradingsymbol']}",{})
            ltp=q.get("last_price",0)or 0;vol=q.get("volume",0)or 0;oi=q.get("oi",0)or 0
            dp=q.get("depth",{});bid=dp.get("buy",[{}])[0].get("price",0) if dp.get("buy") else 0
            ask=dp.get("sell",[{}])[0].get("price",0) if dp.get("sell") else 0
            sp=abs(ask-bid) if ask and bid else 999
            sc=vol*.4+oi*.4+(1000/(sp+1))*.2
            if 50<=ltp<=300:sc*=1.5
            if sc>bs:bs=sc;best={"tradingsymbol":inst["tradingsymbol"],"strike":float(inst["strike"]),"type":ot,"expiry":inst["expiry"],"lot_size":int(inst.get("lot_size",65)),"ltp":ltp,"volume":vol,"oi":oi,"spread":round(sp,2)}
        return best
    return best_opt("BULLISH"),best_opt("BEARISH"),chain

# ─── STOCK SCANNER ──────────────────────────────────────────
def ema_calc(p,n):
    if len(p)<n:return p[-1] if p else 0
    k=2/(n+1);e=sum(p[:n])/n
    for i in range(n,len(p)):e=p[i]*k+e*(1-k)
    return round(e,2)

def rsi_calc(p,n=14):
    if len(p)<n+1:return 50
    g=l=0
    for i in range(len(p)-n,len(p)):
        d=p[i]-p[i-1]
        if d>0:g+=d
        else:l-=d
    return 100 if l==0 else round(100-100/(1+g/n/(l/n)),1)

def scan_one(sym,auth=None):
    """Analyze a stock using proper candle-based technicals."""
    result = analyze_stock_proper(sym, auth)
    return result

def do_scan(auth=None):
    if scan_results["scanning"]:return
    # If no auth passed, try to get from admin state
    if not auth and admin_state["token"]:
        auth={"X-Kite-Version":"3","Authorization":f"token {ADMIN_API_KEY}:{admin_state['token']}"}
    if not auth:
        log.error("Scan aborted: no auth available");scan_results["scanning"]=False;return
    scan_results["scanning"]=True;log.info("Scanning Nifty 50...")
    picks=[]
    for i,sym in enumerate(NIFTY50):
        r=scan_one(sym,auth)
        if r:picks.append(r)
        if i%3==2:time.sleep(1.2)
    picks.sort(key=lambda x:abs(x["score"]),reverse=True)
    scan_results.update(picks=picks,last_scan=datetime.now().isoformat(),scanning=False)
    log.info(f"Scan done: {len(picks)} stocks")
    for p in picks:
        if abs(p["score"])>=3:
            sig={"type":"stock_pick","symbol":p["symbol"],"dir":"BULLISH" if p["score"]>0 else "BEARISH","verdict":p["verdict"],"score":p["score"],"tradingsymbol":p["symbol"],"entryPrice":p["entry"],"slPrice":p["sl"],"t1Price":p["t1"],"t2Price":p["t2"],"ltp":p["ltp"],"change":p["change"],"rsi":p["rsi"],"lotSize":1,"lots":1,"time":datetime.now().strftime("%H:%M"),"id":int(time.time()*1000)+hash(p["symbol"])%1000,"timestamp":datetime.now().isoformat(),"date":datetime.now().strftime("%Y-%m-%d"),"outcome":"open","pnl":None}
            if not any(s.get("symbol")==p["symbol"] and s.get("date")==sig["date"] and s.get("type")=="stock_pick" for s in signal_history):
                signal_history.insert(0,sig)
    if TG_ENABLED:
        buys=[p for p in picks if p["score"]>=3][:3];sells=[p for p in picks if p["score"]<=-3][:3]
        if buys or sells:
            msg="📊 <b>NIFTY 50 SCAN</b>\n\n"
            for b in buys:msg+=f"🟢 <b>{b['symbol']}</b> ₹{b['ltp']} ({b['change']:+.1f}%) Sc:{b['score']}\n   SL:₹{b['sl']} T1:₹{b['t1']} T2:₹{b['t2']}\n"
            for s in sells:msg+=f"\n🔴 <b>{s['symbol']}</b> ₹{s['ltp']} ({s['change']:+.1f}%) Sc:{s['score']}\n   SL:₹{s['sl']} T1:₹{s['t1']}\n"
            threading.Thread(target=send_tg,args=(msg,),daemon=True).start()

def scanner_loop():
    while True:
        try:
            if scanner_cfg["enabled"] and scanner_cfg["auto"] and admin_state["token"]:
                now=datetime.now()
                if 3<=now.hour<=10:
                    auth={"X-Kite-Version":"3","Authorization":f"token {ADMIN_API_KEY}:{admin_state['token']}"}
                    do_scan(auth)
        except Exception as e:log.error(f"Scanner: {e}");scan_results["scanning"]=False
        time.sleep(scanner_cfg.get("interval",10)*60)
threading.Thread(target=scanner_loop,daemon=True).start()

# Keep-alive
def keepalive():
    while True:
        try:
            if PUBLIC_URL!="http://localhost:5000":rq.get(f"{PUBLIC_URL}/api/ping",timeout=5)
        except:pass
        time.sleep(600)
threading.Thread(target=keepalive,daemon=True).start()

# ─── ROUTES: AUTH ───────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("authenticated"):return redirect("/access")
    return send_from_directory("static","index.html")

@app.route("/access",methods=["GET","POST"])
def access():
    err=""
    if request.method=="POST":
        pw=request.form.get("password","")
        api_key=request.form.get("api_key","").strip()
        api_secret=request.form.get("api_secret","").strip()
        if pw==ADMIN_PASSWORD:
            session.update(authenticated=True,is_admin=True,user_id="admin",use_own_api=False);session.permanent=True;return redirect("/")
        elif pw==ACCESS_PASSWORD:
            session.update(authenticated=True,is_admin=False,user_id=secrets.token_hex(8),use_own_api=False);session.permanent=True;return redirect("/")
        elif api_key and api_secret:
            uid=secrets.token_hex(8)
            user_sessions[uid]={"api_key":api_key,"api_secret":api_secret,"token":None}
            session.update(authenticated=True,is_admin=False,user_id=uid,use_own_api=True,own_api_key=api_key,own_api_secret=api_secret);session.permanent=True
            return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}")
        else:
            err='<div style="color:#ff1744;font-size:12px;margin-bottom:8px">Invalid credentials</div>'
    return f'''<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="theme-color" content="#060a10"><title>Nifty Agent</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,system-ui,sans-serif;background:#060a10;color:#e4eaf6;display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px}}
.box{{background:#111a2e;border:1px solid #1a2744;border-radius:20px;padding:32px;width:100%;max-width:400px}}.title{{text-align:center;margin-bottom:24px}}
input{{width:100%;padding:14px 16px;border-radius:10px;border:1px solid #1a2744;background:#0c1220;color:#e4eaf6;font-size:15px;margin-bottom:12px;outline:none;transition:border .2s}}
input:focus{{border-color:#00e5ff}}button{{width:100%;padding:14px;border-radius:10px;border:none;background:#00e5ff;color:#060a10;font-weight:700;font-size:15px;cursor:pointer;margin-bottom:8px}}
button:active{{opacity:.8}}.divider{{text-align:center;color:#5a6a8a;font-size:11px;margin:20px 0;position:relative}}.divider::before,.divider::after{{content:'';position:absolute;top:50%;width:35%;height:1px;background:#1a2744}}
.divider::before{{left:0}}.divider::after{{right:0}}.sub{{font-size:11px;color:#5a6a8a;text-align:center}}</style></head>
<body><div class="box"><div class="title"><div style="font-size:40px;margin-bottom:8px">📊</div><div style="font-size:20px;font-weight:700">Nifty Trading Agent</div><div style="font-size:12px;color:#5a6a8a;margin-top:4px">Smart Signals · Nifty 50 Scanner</div></div>
{err}
<form method="POST">
<input name="password" type="password" placeholder="Enter access password" autocomplete="off">
<button type="submit">Login</button>
<div class="divider">or use your own Kite API</div>
<input name="api_key" placeholder="Your Kite API Key" autocomplete="off">
<input name="api_secret" type="password" placeholder="Your Kite API Secret" autocomplete="off">
<button type="submit" style="background:transparent;border:1px solid #00e5ff;color:#00e5ff">Connect with your API</button>
</form>
<div class="sub" style="margin-top:16px">Add to Home Screen for app experience</div>
</div></body></html>'''

@app.route("/admin/login")
def admin_login():
    return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={ADMIN_API_KEY}")

@app.route("/callback")
def callback():
    rt=request.args.get("request_token")
    if not rt:return jsonify({"error":"No token"}),400
    uid=session.get("user_id","admin")
    own=session.get("use_own_api",False)
    ak=session.get("own_api_key",ADMIN_API_KEY) if own else ADMIN_API_KEY
    asc=session.get("own_api_secret",ADMIN_API_SECRET) if own else ADMIN_API_SECRET
    ck=hashlib.sha256(f"{ak}{rt}{asc}".encode()).hexdigest()
    try:
        r=rq.post(f"{KITE}/session/token",data={"api_key":ak,"request_token":rt,"checksum":ck},headers={"X-Kite-Version":"3"},timeout=10)
        d=r.json()
        if d.get("status")=="success":
            tok=d["data"]["access_token"]
            if own and uid in user_sessions:
                user_sessions[uid]["token"]=tok
                log.info(f"User {uid} authenticated with own API")
            else:
                admin_state["token"]=tok
                load_instruments()
                log.info("Admin Kite connected")
            return '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="2;url=/"></head><body style="background:#060a10;color:#00e5ff;font-family:system-ui;display:grid;place-items:center;height:100vh"><div style="text-align:center"><div style="font-size:48px;margin-bottom:16px">✅</div><h2>Connected!</h2><p style="color:#5a6a8a;margin-top:8px">Redirecting to dashboard...</p></div></body></html>'
        return jsonify({"error":"Failed","detail":d}),400
    except Exception as e:return jsonify({"error":str(e)}),500

# ─── ROUTES: API ────────────────────────────────────────────
@app.route("/api/ping")
def ping():return jsonify({"ok":True})

@app.route("/api/status")
@require_auth
def status():
    return jsonify({"authenticated":get_kite_auth() is not None,"is_admin":session.get("is_admin",False),"instruments_count":len(admin_state["instruments"]) if admin_state["instruments"] else 0,"use_own_api":session.get("use_own_api",False),"scanner":scanner_cfg})


# ─── PROPER TECHNICAL ANALYSIS ENGINE ───────────────────────
# Uses real OHLC candles from Kite, not ticks.

def calc_rsi(closes, period=14):
    """RSI on proper close prices."""
    if len(closes) < period + 1: return 50
    gains = losses = 0
    for i in range(len(closes)-period, len(closes)):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else: losses -= d
    ag, al = gains/period, losses/period
    if al == 0: return 100
    return round(100 - 100/(1 + ag/al), 1)

def calc_ema(prices, period):
    """EMA on close prices."""
    if len(prices) < period: return prices[-1] if prices else 0
    k = 2/(period+1)
    e = sum(prices[:period])/period
    for i in range(period, len(prices)):
        e = prices[i]*k + e*(1-k)
    return round(e, 2)

def calc_supertrend(highs, lows, closes, factor=2, period=10):
    """Supertrend using proper H/L/C data with ATR."""
    if len(closes) < period + 1: return {"trend": "NEUTRAL", "value": closes[-1] if closes else 0}
    # Calculate ATR
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    if len(trs) < period: return {"trend": "NEUTRAL", "value": closes[-1]}
    atr = sum(trs[-period:])/period
    
    # Supertrend calculation
    hl2 = (highs[-1] + lows[-1]) / 2
    upper = hl2 + factor * atr
    lower = hl2 - factor * atr
    
    if closes[-1] > lower:
        return {"trend": "BULLISH", "value": round(lower, 2)}
    else:
        return {"trend": "BEARISH", "value": round(upper, 2)}

def calc_adx(highs, lows, closes, period=14):
    """ADX - measures trend strength. >25 = trending, <20 = ranging."""
    if len(closes) < period + 2: return 20
    plus_dm = []
    minus_dm = []
    trs = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    
    if len(trs) < period: return 20
    atr = sum(trs[-period:])/period
    if atr == 0: return 20
    plus_di = 100 * (sum(plus_dm[-period:])/period) / atr
    minus_di = 100 * (sum(minus_dm[-period:])/period) / atr
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
    return round(dx, 1)

def calc_vwap_from_candles(candles):
    """Real VWAP from candle data with volume."""
    total_vol = 0
    total_vp = 0
    for c in candles:
        # candle: [timestamp, open, high, low, close, volume]
        typical = (c[2] + c[3] + c[4]) / 3
        vol = c[5] if len(c) > 5 else 0
        total_vp += typical * vol
        total_vol += vol
    if total_vol == 0: return candles[-1][4] if candles else 0
    return round(total_vp / total_vol, 2)

def calc_volume_ratio(candles, lookback=20):
    """Current volume vs average volume."""
    if len(candles) < 2: return 1
    vols = [c[5] for c in candles if len(c) > 5]
    if not vols or len(vols) < 2: return 1
    current = vols[-1]
    avg = sum(vols[-lookback-1:-1]) / min(lookback, len(vols)-1) if len(vols) > 1 else current
    return round(current / avg, 1) if avg > 0 else 1

def analyze_index_proper(idx, auth=None):
    """
    PROPER index analysis using real candle data.
    Multi-timeframe: 5-min for entry, 15-min for trend, daily for bias.
    Returns analysis dict with score, direction, and all indicators.
    """
    # 1. Get spot quote (includes real VWAP as average_price)
    spot_sym = "NSE:NIFTY 50" if idx.upper() == "NIFTY" else "NSE:NIFTY BANK"
    sq = kite_get("/quote", params=[("i", spot_sym)], auth_override=auth)
    if "error" in sq: return None
    qd = sq.get("data", {}).get(spot_sym, {})
    if not qd: return None
    
    spot = qd.get("last_price", 0)
    real_vwap = qd.get("average_price", 0) or spot  # This is the REAL VWAP from exchange
    ohlc = qd.get("ohlc", {})
    day_open = ohlc.get("open", spot)
    day_high = ohlc.get("high", spot)
    day_low = ohlc.get("low", spot)
    prev_close = ohlc.get("close", spot)
    
    # Get instrument token for historical data
    # For NIFTY 50 index, token is 256265. For NIFTY BANK, it's 260105.
    token = qd.get("instrument_token", 256265 if idx.upper() == "NIFTY" else 260105)
    
    today = datetime.now()
    
    # 2. Fetch 5-minute candles (today)
    candles_5m = []
    r5 = kite_get(f"/instruments/historical/{token}/5minute",
                  params={"from": today.strftime("%Y-%m-%d"), "to": today.strftime("%Y-%m-%d")},
                  auth_override=auth)
    if "data" in r5: candles_5m = r5["data"].get("candles", [])
    
    # 3. Fetch 15-minute candles (last 3 days for trend)
    candles_15m = []
    r15 = kite_get(f"/instruments/historical/{token}/15minute",
                   params={"from": (today - timedelta(days=3)).strftime("%Y-%m-%d"), "to": today.strftime("%Y-%m-%d")},
                   auth_override=auth)
    if "data" in r15: candles_15m = r15["data"].get("candles", [])
    
    # 4. Fetch daily candles (last 30 days for daily bias)
    candles_day = []
    rd = kite_get(f"/instruments/historical/{token}/day",
                  params={"from": (today - timedelta(days=30)).strftime("%Y-%m-%d"), "to": today.strftime("%Y-%m-%d")},
                  auth_override=auth)
    if "data" in rd: candles_day = rd["data"].get("candles", [])
    
    # ─── COMPUTE INDICATORS ────────────────────────────────
    score = 0
    reasons = []
    
    # === 5-MIN TIMEFRAME (entry timing) ===
    if len(candles_5m) >= 15:
        c5 = [c[4] for c in candles_5m]  # closes
        h5 = [c[2] for c in candles_5m]  # highs
        l5 = [c[3] for c in candles_5m]  # lows
        
        rsi_5m = calc_rsi(c5)
        ema9_5m = calc_ema(c5, 9)
        ema21_5m = calc_ema(c5, 21)
        st_5m = calc_supertrend(h5, l5, c5)
        vwap_dist = round(spot - real_vwap, 2)
        vol_ratio = calc_volume_ratio(candles_5m)
        
        # RSI (5-min)
        if rsi_5m < 25: score += 2; reasons.append({"t": f"5m RSI {rsi_5m} deeply oversold", "d": "bull", "w": 2})
        elif rsi_5m < 35: score += 1; reasons.append({"t": f"5m RSI {rsi_5m} oversold zone", "d": "bull", "w": 1})
        elif rsi_5m > 75: score -= 2; reasons.append({"t": f"5m RSI {rsi_5m} deeply overbought", "d": "bear", "w": 2})
        elif rsi_5m > 65: score -= 1; reasons.append({"t": f"5m RSI {rsi_5m} overbought zone", "d": "bear", "w": 1})
        else: reasons.append({"t": f"5m RSI {rsi_5m} neutral", "d": "neut", "w": 0})
        
        # EMA crossover (5-min)
        ema_diff = ema9_5m - ema21_5m
        if ema_diff > 5: score += 1; reasons.append({"t": f"5m EMA9 > EMA21 by {ema_diff:.0f}", "d": "bull", "w": 1})
        elif ema_diff < -5: score -= 1; reasons.append({"t": f"5m EMA9 < EMA21 by {abs(ema_diff):.0f}", "d": "bear", "w": 1})
        else: reasons.append({"t": f"5m EMAs flat (diff {ema_diff:.0f})", "d": "neut", "w": 0})
        
        # VWAP (real exchange VWAP)
        if vwap_dist > 20: score += 1; reasons.append({"t": f"Spot {vwap_dist:.0f} above VWAP — buyers", "d": "bull", "w": 1})
        elif vwap_dist < -20: score -= 1; reasons.append({"t": f"Spot {abs(vwap_dist):.0f} below VWAP — sellers", "d": "bear", "w": 1})
        else: reasons.append({"t": f"Near VWAP ({vwap_dist:+.0f})", "d": "neut", "w": 0})
        
        # Supertrend (5-min)
        if st_5m["trend"] == "BULLISH": score += 1; reasons.append({"t": f"5m Supertrend BULL at {st_5m['value']}", "d": "bull", "w": 1})
        else: score -= 1; reasons.append({"t": f"5m Supertrend BEAR at {st_5m['value']}", "d": "bear", "w": 1})
        
        # Volume confirmation
        if vol_ratio >= 1.5 and spot > real_vwap: score += 1; reasons.append({"t": f"Volume {vol_ratio}x + above VWAP", "d": "bull", "w": 1})
        elif vol_ratio >= 1.5 and spot < real_vwap: score -= 1; reasons.append({"t": f"Volume {vol_ratio}x + below VWAP", "d": "bear", "w": 1})
        else: reasons.append({"t": f"Volume {vol_ratio}x normal", "d": "neut", "w": 0})
    
    # === 15-MIN TIMEFRAME (trend direction) ===
    if len(candles_15m) >= 20:
        c15 = [c[4] for c in candles_15m]
        h15 = [c[2] for c in candles_15m]
        l15 = [c[3] for c in candles_15m]
        
        st_15m = calc_supertrend(h15, l15, c15)
        ema9_15m = calc_ema(c15, 9)
        ema21_15m = calc_ema(c15, 21)
        adx = calc_adx(h15, l15, c15)
        
        # 15-min trend (higher weight)
        if st_15m["trend"] == "BULLISH" and ema9_15m > ema21_15m:
            score += 2; reasons.append({"t": f"15m TREND BULLISH (ST+EMA confirm)", "d": "bull", "w": 2})
        elif st_15m["trend"] == "BEARISH" and ema9_15m < ema21_15m:
            score -= 2; reasons.append({"t": f"15m TREND BEARISH (ST+EMA confirm)", "d": "bear", "w": 2})
        elif st_15m["trend"] == "BULLISH":
            score += 1; reasons.append({"t": f"15m Supertrend bullish", "d": "bull", "w": 1})
        elif st_15m["trend"] == "BEARISH":
            score -= 1; reasons.append({"t": f"15m Supertrend bearish", "d": "bear", "w": 1})
        
        # ADX trend strength
        if adx > 25: reasons.append({"t": f"ADX {adx} — strong trend (good for trading)", "d": "neut", "w": 0})
        elif adx < 20:
            reasons.append({"t": f"ADX {adx} — weak/ranging (AVOID trading)", "d": "neut", "w": 0})
            score = int(score * 0.5)  # Halve the score in ranging markets
    
    # === DAILY TIMEFRAME (bias) ===
    if len(candles_day) >= 10:
        cd = [c[4] for c in candles_day]
        hd = [c[2] for c in candles_day]
        ld = [c[3] for c in candles_day]
        
        ema20_d = calc_ema(cd, 20)
        st_daily = calc_supertrend(hd, ld, cd, factor=3, period=10)
        
        # Daily bias
        if spot > ema20_d and st_daily["trend"] == "BULLISH":
            score += 1; reasons.append({"t": f"Daily BULLISH (above 20EMA + ST)", "d": "bull", "w": 1})
        elif spot < ema20_d and st_daily["trend"] == "BEARISH":
            score -= 1; reasons.append({"t": f"Daily BEARISH (below 20EMA + ST)", "d": "bear", "w": 1})
        else:
            reasons.append({"t": f"Daily mixed — no strong bias", "d": "neut", "w": 0})
    
    # === PCR (OI based) ===
    # We still use PCR but look at the actual values more carefully
    step = 50 if idx.upper() == "NIFTY" else 100
    atm = round(spot / step) * step
    
    return {
        "spot": spot, "vwap": real_vwap, "ohlc": ohlc,
        "day_high": day_high, "day_low": day_low,
        "score": score, "reasons": reasons,
        "atm": atm, "token": token,
    }

def analyze_stock_proper(symbol, auth=None):
    """
    Proper stock analysis using multi-timeframe candle data.
    Returns score and recommendation.
    """
    sym = f"NSE:{symbol}"
    q = kite_get("/quote", params=[("i", sym)], auth_override=auth)
    if "error" in q: return None
    qd = q.get("data", {}).get(sym)
    if not qd or not qd.get("last_price"): return None
    
    ltp = qd["last_price"]
    ohlc = qd.get("ohlc", {})
    volume = qd.get("volume", 0) or 0
    prev_close = ohlc.get("close", ltp)
    chg = round(((ltp - prev_close) / prev_close) * 100, 2) if prev_close else 0
    token = qd.get("instrument_token")
    if not token: return None
    
    today = datetime.now()
    
    # Fetch daily candles (90 days)
    h = kite_get(f"/instruments/historical/{token}/day",
                 params={"from": (today - timedelta(days=90)).strftime("%Y-%m-%d"),
                         "to": today.strftime("%Y-%m-%d"), "oi": "0"},
                 auth_override=auth)
    candles = h.get("data", {}).get("candles", []) if "data" in h else []
    if len(candles) < 20: return None
    
    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    volumes = [c[5] for c in candles if len(c) > 5]
    
    score = 0
    factors = []
    
    # 1. RSI (14) on daily
    rsi_val = calc_rsi(closes)
    if rsi_val < 30: score += 2; factors.append(("RSI " + str(rsi_val) + " oversold", "bull"))
    elif rsi_val < 40: score += 1; factors.append(("RSI " + str(rsi_val) + " low", "bull"))
    elif rsi_val > 70: score -= 2; factors.append(("RSI " + str(rsi_val) + " overbought", "bear"))
    elif rsi_val > 60: score -= 1; factors.append(("RSI " + str(rsi_val) + " high", "bear"))
    else: factors.append(("RSI " + str(rsi_val), "neut"))
    
    # 2. EMA 9/21 crossover
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    if ema9 > ema21 * 1.005: score += 1; factors.append(("EMA9 > EMA21", "bull"))
    elif ema9 < ema21 * 0.995: score -= 1; factors.append(("EMA9 < EMA21", "bear"))
    
    # 3. Price vs 50 EMA
    ema50 = calc_ema(closes, 50) if len(closes) >= 50 else calc_ema(closes, 20)
    if ltp > ema50 * 1.02: score += 1; factors.append(("Above 50EMA (uptrend)", "bull"))
    elif ltp < ema50 * 0.98: score -= 1; factors.append(("Below 50EMA (downtrend)", "bear"))
    
    # 4. Supertrend (daily)
    st = calc_supertrend(highs, lows, closes, factor=2, period=10)
    if st["trend"] == "BULLISH": score += 1; factors.append(("Supertrend bullish", "bull"))
    else: score -= 1; factors.append(("Supertrend bearish", "bear"))
    
    # 5. Volume surge
    if volumes and len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 1
        if vol_ratio > 1.5 and chg > 0: score += 1; factors.append((str(vol_ratio) + "x volume + up", "bull"))
        elif vol_ratio > 1.5 and chg < 0: score -= 1; factors.append((str(vol_ratio) + "x volume + down", "bear"))
    
    # 6. ADX (trend strength)
    adx = calc_adx(highs, lows, closes)
    if adx < 20:
        score = int(score * 0.5)  # Weak trend = reduce confidence
        factors.append(("ADX " + str(adx) + " weak trend", "neut"))
    elif adx > 30:
        factors.append(("ADX " + str(adx) + " strong trend", "neut"))
    
    # 7. Support/Resistance
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    if ((ltp - recent_low) / recent_low) * 100 < 2: score += 1; factors.append(("Near 20d support", "bull"))
    elif ((recent_high - ltp) / ltp) * 100 < 2: score -= 1; factors.append(("Near 20d resistance", "bear"))
    
    # 8. Momentum (5-day ROC)
    if len(closes) >= 5:
        roc5 = ((closes[-1] - closes[-5]) / closes[-5]) * 100
        if roc5 > 2: score += 1; factors.append(("+" + str(round(roc5, 1)) + "% 5d momentum", "bull"))
        elif roc5 < -2: score -= 1; factors.append((str(round(roc5, 1)) + "% 5d momentum", "bear"))
    
    # Calculate targets using ATR (proper way)
    atr20 = sum(highs[i] - lows[i] for i in range(-20, 0)) / 20 if len(highs) >= 20 else highs[-1] - lows[-1]
    
    verdict = "STRONG BUY" if score >= 4 else "BUY" if score >= 2 else "STRONG SELL" if score <= -4 else "SELL" if score <= -2 else "HOLD"
    
    # 1:2 minimum R:R for targets
    if score >= 2:
        sl = round(max(recent_low, ltp - 1.5 * atr20), 2)
        risk = ltp - sl
        t1 = round(ltp + risk * 2, 2)   # 1:2 R:R
        t2 = round(ltp + risk * 3, 2)   # 1:3 R:R
    elif score <= -2:
        sl = round(min(recent_high, ltp + 1.5 * atr20), 2)
        risk = sl - ltp
        t1 = round(ltp - risk * 2, 2)
        t2 = round(ltp - risk * 3, 2)
    else:
        sl = t1 = t2 = 0
    
    return {
        "symbol": symbol, "ltp": ltp, "change": chg, "volume": volume,
        "rsi": rsi_val, "ema9": ema9, "ema21": ema21, "ema50": ema50,
        "adx": adx, "supertrend": st["trend"],
        "score": score, "verdict": verdict, "factors": factors,
        "entry": ltp, "sl": sl, "t1": t1, "t2": t2,
        "atr": round(atr20, 2),
    }

# Update the main analysis endpoint
@app.route("/api/analysis/<idx>")
@require_auth
def proper_analysis(idx):
    """Multi-timeframe analysis using real candle data."""
    auth = None
    if admin_state["token"]:
        auth = {"X-Kite-Version": "3", "Authorization": f"token {ADMIN_API_KEY}:{admin_state['token']}"}
    result = analyze_index_proper(idx, auth)
    if not result:
        return jsonify({"error": "Analysis failed — Kite may not be connected"}), 503
    
    # Also get option chain data for PCR
    ce_opt, pe_opt, chain = find_options(idx, result["spot"])
    pcr_val = 0
    mp = result["atm"]
    if chain:
        tp = sum(r["pe"]["oi"] for r in chain)
        tc = sum(r["ce"]["oi"] for r in chain)
        pcr_val = round(tp/tc, 2) if tc > 0 else 0
        
        # PCR scoring
        if pcr_val > 1.3:
            result["score"] += 1
            result["reasons"].append({"t": f"PCR {pcr_val} — heavy put writing (bullish)", "d": "bull", "w": 1})
        elif pcr_val < 0.7:
            result["score"] -= 1
            result["reasons"].append({"t": f"PCR {pcr_val} — heavy call writing (bearish)", "d": "bear", "w": 1})
        else:
            result["reasons"].append({"t": f"PCR {pcr_val} — neutral", "d": "neut", "w": 0})
        
        # Max pain
        mv = float("inf")
        for r in chain:
            p = sum(max(0, o["ce"]["oi"]*(r["strike"]-o["strike"])) + max(0, o["pe"]["oi"]*(o["strike"]-r["strike"])) for o in chain)
            if p < mv: mv, mp = p, r["strike"]
    
    # Build signal if score is strong enough (need ≥5 for proper conviction)
    signal = None
    now = datetime.now()
    hr, mn = now.hour, now.minute
    # Trading window: 10:00 AM to 2:30 PM IST (4:30 to 9:00 UTC)
    time_ok = (hr > 4 or (hr == 4 and mn >= 30)) and (hr < 9 or (hr == 9 and mn == 0))
    
    if abs(result["score"]) >= 5 and time_ok:
        direction = "BULLISH" if result["score"] > 0 else "BEARISH"
        rec = ce_opt if direction == "BULLISH" else pe_opt
        
        if rec and rec["ltp"] > 0:
            opt_ltp = rec["ltp"]
            # 1:2 minimum R:R
            sl_pct = 0.25  # 25% SL on premium
            sl_points = max(opt_ltp * sl_pct, 10)
            sl_price = round(opt_ltp - sl_points, 2)
            t1_price = round(opt_ltp + sl_points * 2, 2)  # 1:2 R:R
            t2_price = round(opt_ltp + sl_points * 3, 2)  # 1:3 R:R
            
            lot = rec.get("lot_size", 65)
            max_risk = 200000 * 0.02  # Default capital, frontend can override
            risk_per_lot = sl_points * lot
            lots = max(1, int(max_risk / risk_per_lot)) if risk_per_lot > 0 else 1
            
            signal = {
                "dir": direction,
                "tradingsymbol": rec["tradingsymbol"],
                "strike": rec["strike"],
                "optionType": rec["type"],
                "expiry": rec["expiry"],
                "entryPrice": opt_ltp,
                "slPrice": sl_price,
                "slPoints": round(sl_points, 2),
                "t1Price": t1_price,
                "t2Price": t2_price,
                "lots": lots,
                "lotSize": lot,
                "totalRisk": round(risk_per_lot * lots),
                "totalReward": round((t1_price - opt_ltp) * lot * lots),
                "rr": round((t1_price - opt_ltp) / sl_points, 1),
                "conf": min(abs(result["score"]), 10),
                "volume": rec.get("volume", 0),
                "oi": rec.get("oi", 0),
                "spread": rec.get("spread", 0),
                "spotAtSignal": result["spot"],
            }
    
    return jsonify({
        "data": {
            "spot": result["spot"],
            "vwap": result.get("vwap", 0),
            "ohlc": result.get("ohlc", {}),
            "atm": result["atm"],
            "pcr": pcr_val,
            "max_pain": mp,
            "chain": chain,
            "score": result["score"],
            "reasons": result["reasons"],
            "signal": signal,
            "recommended_ce": ce_opt,
            "recommended_pe": pe_opt,
            "timestamp": datetime.now().isoformat(),
        }
    })


@app.route("/api/market-data-both")
@require_auth
def market_data_both():
    h=get_kite_auth()
    if not h:return jsonify({"error":"Kite not connected"}),503
    sr=kite_get("/quote",params=[("i","NSE:NIFTY 50"),("i","NSE:NIFTY BANK")])
    if "error" in sr:return jsonify(sr),500
    nq=sr.get("data",{}).get("NSE:NIFTY 50",{})
    bq=sr.get("data",{}).get("NSE:NIFTY BANK",{})
    return jsonify({"data":{"nifty":{"spot":nq.get("last_price",0),"ohlc":nq.get("ohlc",{})},"banknifty":{"spot":bq.get("last_price",0),"ohlc":bq.get("ohlc",{})}}})

@app.route("/api/market-data/<idx>")
@require_auth
def market_data(idx):
    h=get_kite_auth()
    if not h:return jsonify({"error":"Kite not connected"}),503
    ss="NSE:NIFTY 50" if idx.upper()=="NIFTY" else "NSE:NIFTY BANK"
    sr=kite_get("/quote",params=[("i",ss)])
    if "error" in sr:return jsonify(sr),500
    sf=sr.get("data",{}).get(ss,{});spot=sf.get("last_price",0)
    if not spot:return jsonify({"error":"No spot"}),500
    step=50 if idx.upper()=="NIFTY" else 100;atm=round(spot/step)*step
    ce_opt,pe_opt,chain=find_options(idx,spot)
    pcr_v=0;mp=atm
    if chain:
        tp=sum(r["pe"]["oi"] for r in chain);tc=sum(r["ce"]["oi"] for r in chain)
        pcr_v=round(tp/tc,2) if tc else 0
        mv=float("inf")
        for r in chain:
            p=sum(max(0,o["ce"]["oi"]*(r["strike"]-o["strike"]))+max(0,o["pe"]["oi"]*(o["strike"]-r["strike"])) for o in chain)
            if p<mv:mv,mp=p,r["strike"]
    return jsonify({"data":{"spot":spot,"spot_ohlc":sf.get("ohlc",{}),"atm":atm,"pcr":pcr_v,"max_pain":mp,"chain":chain,"recommended_ce":ce_opt,"recommended_pe":pe_opt,"timestamp":datetime.now().isoformat()}})

@app.route("/api/stock/<symbol>")
@require_auth
def stock_api(symbol):
    h=get_kite_auth()
    if not h:return jsonify({"error":"Kite not connected"}),503
    sym=f"NSE:{symbol.upper()}"
    q=kite_get("/quote",params=[("i",sym)])
    if "error" in q:return jsonify(q),500
    qd=q.get("data",{}).get(sym)
    if not qd:return jsonify({"error":f"{symbol} not found"}),404
    tok=qd.get("instrument_token")
    if not tok:return jsonify({"error":"No token"}),500
    today=datetime.now();candles={}
    for label,days,iv in[("daily",180,"day"),("weekly",365,"day"),("intraday",5,"15minute")]:
        r=kite_get(f"/instruments/historical/{tok}/{iv}",params={"from":(today-timedelta(days=days)).strftime("%Y-%m-%d"),"to":today.strftime("%Y-%m-%d"),"oi":"0"})
        if "data" in r:candles[label]=r["data"].get("candles",[])
    return jsonify({"data":{"symbol":symbol.upper(),"ltp":qd.get("last_price",0),"ohlc":qd.get("ohlc",{}),"volume":qd.get("volume",0),"candles":candles}})

@app.route("/api/signals",methods=["GET","POST"])
@require_auth
def signals():
    if request.method=="POST":
        d=request.get_json()
        if d:d.update(timestamp=datetime.now().isoformat(),date=datetime.now().strftime("%Y-%m-%d"));signal_history.insert(0,d)
        if len(signal_history)>100:signal_history.pop()
        save_signals()
        threading.Thread(target=send_tg,args=(format_signal_tg(d),),daemon=True).start()
        return jsonify({"ok":True})
    return jsonify({"signals":signal_history[:50]})

@app.route("/api/signals/update",methods=["POST"])
@require_auth
def update_sig():
    d=request.get_json();sid=str(d.get("id",""));oc=d.get("outcome","")
    for s in signal_history:
        if str(s.get("id",""))==sid:
            s["outcome"]=oc;s["exit_time"]=datetime.now().isoformat()
            e=s.get("entryPrice",0);lots=s.get("lots",1);ls=s.get("lotSize",65)
            if oc=="sl_hit":s["pnl"]=round((s.get("slPrice",e)-e)*lots*ls)
            elif oc=="t1_hit":s["pnl"]=round((s.get("t1Price",e)-e)*lots*ls)
            elif oc=="t2_hit":s["pnl"]=round((s.get("t2Price",e)-e)*lots*ls)
            em={"sl_hit":"⛔","t1_hit":"🎯","t2_hit":"🚀"}.get(oc,"📝")
            pnl_s=f"₹{s.get('pnl',0):+,.0f}" if s.get("pnl") is not None else ""
            save_signals()
            threading.Thread(target=send_tg,args=(f"{em} <b>{s.get('tradingsymbol','')}</b> — {oc.upper().replace('_',' ')}\n{pnl_s}",),daemon=True).start()
            break
    return jsonify({"ok":True})

@app.route("/api/scanner")
@require_auth
def scanner():
    picks=scan_results.get("picks",[])
    return jsonify({"data":{"top_buys":sorted([p for p in picks if p["score"]>=1],key=lambda x:-x["score"])[:5],"top_sells":sorted([p for p in picks if p["score"]<=-1],key=lambda x:x["score"])[:5],"all":picks,"last_scan":scan_results.get("last_scan"),"scanning":scan_results["scanning"],"total":len(picks),"config":scanner_cfg}})

@app.route("/api/scanner/config",methods=["GET","POST"])
@require_auth
def scan_cfg():
    if not session.get("is_admin"):return jsonify({"error":"Admin"}),403
    if request.method=="POST":
        d=request.get_json()
        if "enabled" in d:scanner_cfg["enabled"]=bool(d["enabled"])
        if "auto" in d:scanner_cfg["auto"]=bool(d["auto"])
        if "interval" in d:scanner_cfg["interval"]=max(5,min(60,int(d["interval"])))
    return jsonify({"config":scanner_cfg})

@app.route("/api/scanner/trigger",methods=["POST"])
@require_auth
def scan_trigger():
    if scan_results["scanning"]:return jsonify({"error":"Scan already in progress. Please wait ~60 seconds."}),400
    if not admin_state["token"]:return jsonify({"error":"Kite not connected. Admin needs to login first."}),503
    # Capture auth header NOW (in request context) and pass to thread
    auth={"X-Kite-Version":"3","Authorization":f"token {ADMIN_API_KEY}:{admin_state['token']}"}
    threading.Thread(target=do_scan,args=(auth,),daemon=True).start()
    return jsonify({"ok":True,"message":"Scanning 50 stocks... Results in ~60 seconds."})

@app.route("/api/admin/disconnect",methods=["POST"])
@require_auth
def disconnect():
    if not session.get("is_admin"):return jsonify({"error":"Admin"}),403
    admin_state.update(token=None,instruments=None,inst_date=None)
    send_tg("⚠️ <b>Kite disconnected</b> by admin")
    return jsonify({"ok":True})

@app.route("/api/admin/clear",methods=["POST"])
@require_auth
def clear():
    if not session.get("is_admin"):return jsonify({"error":"Admin"}),403
    signal_history.clear();return jsonify({"ok":True})

@app.route("/api/admin/tg-test",methods=["POST"])
@require_auth
def tg_test():
    if not TG_ENABLED:return jsonify({"error":"Set TG_BOT_TOKEN & TG_CHANNEL_ID"}),400
    send_tg("🔔 <b>Test</b> — Telegram working! ✅");return jsonify({"ok":True,"message":"Sent!"})

@app.route("/api/eod-check",methods=["POST"])
@require_auth
def eod_check():
    if not session.get("is_admin"):return jsonify({"error":"Admin"}),403
    open_trades=[s for s in signal_history if s.get("outcome")=="open"]
    if not open_trades:return jsonify({"message":"No open trades"})
    for t in open_trades:
        t["outcome"]="eod_exit";t["exit_time"]=datetime.now().isoformat()
    save_signals()
    if TG_ENABLED:
        msg="\U0001f514 <b>END OF DAY REVIEW</b>\n\n"
        for t in open_trades:
            msg+=f"\U0001f4cb <b>{t.get('tradingsymbol','')}</b>\nEntry: \u20b9{t.get('entryPrice',0)}\n\U0001f4a1 <b>EXIT</b> \u2014 Options lose value overnight (theta decay). Close before 3:15 PM.\n\n"
        msg+="\u26a0\ufe0f <i>Exit all F&O by 3:15 PM</i>"
        threading.Thread(target=send_tg,args=(msg,),daemon=True).start()
    return jsonify({"ok":True,"count":len(open_trades)})


@app.route("/api/scanner/debug")
@require_auth
def scanner_debug():
    """Debug endpoint - shows raw scan state."""
    picks=scan_results.get("picks",[])
    return jsonify({
        "scanning":scan_results["scanning"],
        "last_scan":scan_results.get("last_scan"),
        "total_picks":len(picks),
        "sample_picks":picks[:3] if picks else [],
        "scores":[{"sym":p["symbol"],"score":p["score"],"verdict":p["verdict"]} for p in picks[:10]],
        "kite_connected":get_kite_auth() is not None,
        "scanner_cfg":scanner_cfg,
    })

@app.route("/api/option-chain/<idx>")
@require_auth
def oc(idx):return market_data(idx)

@app.route("/<path:p>")
def static_files(p):
    if not session.get("authenticated"):return redirect("/access")
    return send_from_directory("static",p)

if __name__=="__main__":
    print(f"\n{'='*50}\n  NIFTY AGENT v5\n  {PUBLIC_URL}\n{'='*50}\n")
    app.run(host="0.0.0.0",port=PORT,debug=False)
