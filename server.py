"""
Nifty Trading Agent v4 — Production Server
Fixed lot sizes, signal history, stock analysis, admin roles.
"""
import os,json,hashlib,logging,csv,io,secrets,threading,time
from datetime import datetime,timedelta
from functools import wraps
from flask import Flask,jsonify,request,redirect,send_from_directory,session
from flask_cors import CORS
import requests as req

API_KEY=os.environ.get("KITE_API_KEY","YOUR_API_KEY_HERE")
API_SECRET=os.environ.get("KITE_API_SECRET","YOUR_API_SECRET_HERE")
PUBLIC_URL=os.environ.get("PUBLIC_URL","http://localhost:5000")
ACCESS_PASSWORD=os.environ.get("ACCESS_PASSWORD","nifty2026")
ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD","admin2026")
SECRET_KEY=os.environ.get("SECRET_KEY",secrets.token_hex(24))
PORT=int(os.environ.get("PORT",5000))
KITE_BASE="https://api.kite.trade"

# Telegram Bot Integration
TG_BOT_TOKEN=os.environ.get("TG_BOT_TOKEN","")
TG_CHANNEL_ID=os.environ.get("TG_CHANNEL_ID","")
TG_ENABLED=bool(TG_BOT_TOKEN and TG_CHANNEL_ID)

def send_telegram(message):
    """Send a message to the Telegram channel."""
    if not TG_ENABLED:return
    try:
        url=f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        req.post(url,json={"chat_id":TG_CHANNEL_ID,"text":message,"parse_mode":"HTML","disable_web_page_preview":True},timeout=5)
        logger.info("Telegram message sent")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def format_signal_msg(d):
    """Format a signal into a beautiful Telegram message."""
    dir_emoji="🟢" if d.get("dir")=="BULLISH" else "🔴"
    dir_text="BULLISH" if d.get("dir")=="BULLISH" else "BEARISH"
    conf=d.get("conf",0)
    bars="█"*conf+"░"*(7-conf)
    return f"""
{dir_emoji} <b>NIFTY AGENT — NEW SIGNAL</b> {dir_emoji}

📋 <b>{d.get('tradingsymbol','N/A')}</b>
Direction: <b>{dir_text}</b>
Confidence: [{bars}] {conf}/7

━━━━━━━━━━━━━━━━━━━━━━

💰 <b>ENTRY:</b> ₹{d.get('entryPrice',0)}
⛔ <b>STOP LOSS:</b> ₹{d.get('slPrice',0)}
🎯 <b>TARGET 1:</b> ₹{d.get('t1Price',0)} (book 50%)
🚀 <b>TARGET 2:</b> ₹{d.get('t2Price',0)} (trail SL)

━━━━━━━━━━━━━━━━━━━━━━

📊 Lots: {d.get('lots',1)} × {d.get('lotSize',65)}
💸 Max Risk: ₹{d.get('totalRisk',0)}
💎 Potential: ₹{d.get('totalReward',0)}
⚖️ Risk:Reward = 1:{d.get('rr',0)}

━━━━━━━━━━━━━━━━━━━━━━
🕐 {d.get('time','')} | Spot: {d.get('spotAtSignal','')}
OI: {d.get('oi',0)} | Vol: {d.get('volume',0)}

⚠️ <i>Max 2% capital risk. Always use SL.</i>
"""

def format_outcome_msg(d):
    """Format a signal outcome update for Telegram."""
    outcome=d.get("outcome","")
    sym=d.get("tradingsymbol","N/A")
    pnl=d.get("pnl")
    if outcome=="sl_hit":
        emoji="⛔"
        text="STOP LOSS HIT"
    elif outcome=="t1_hit":
        emoji="🎯"
        text="TARGET 1 HIT"
    elif outcome=="t2_hit":
        emoji="🚀"
        text="TARGET 2 HIT"
    else:
        emoji="📝"
        text="MANUAL EXIT"
    pnl_text=f"₹{pnl:+,.0f}" if pnl is not None else "N/A"
    pnl_emoji="✅" if pnl and pnl>0 else "❌"
    return f"""
{emoji} <b>SIGNAL UPDATE — {text}</b>

📋 <b>{sym}</b>
{pnl_emoji} P&L: <b>{pnl_text}</b>

Entry: ₹{d.get('entryPrice',0)} → {text}
🕐 {d.get('exit_time','')}
"""

app=Flask(__name__,static_folder="static")
app.secret_key=SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"]="Lax"
app.config["PERMANENT_SESSION_LIFETIME"]=timedelta(hours=12)
CORS(app,origins=["*"])
logging.basicConfig(level=logging.INFO)
logger=logging.getLogger(__name__)

state={"access_token":None,"instruments":None,"inst_date":None}
signal_history=[]

def keep_alive():
    while True:
        try:
            now=datetime.now()
            if 3<=now.hour<=11:req.get(f"{PUBLIC_URL}/api/ping",timeout=5)
        except:pass
        time.sleep(600)
if PUBLIC_URL!="http://localhost:5000":threading.Thread(target=keep_alive,daemon=True).start()

def require_login(f):
    @wraps(f)
    def d(*a,**k):
        if not session.get("authenticated"):return jsonify({"error":"Not logged in"}),401
        return f(*a,**k)
    return d

def auth_header():
    if not state["access_token"]:return None
    return{"X-Kite-Version":"3","Authorization":f"token {API_KEY}:{state['access_token']}"}

def kite_get(ep,params=None):
    h=auth_header()
    if not h:return{"error":"Kite not connected"}
    try:
        r=req.get(f"{KITE_BASE}{ep}",headers=h,params=params,timeout=15)
        return r.json() if r.status_code==200 else{"error":f"Kite {r.status_code}"}
    except Exception as e:return{"error":str(e)}

def load_instruments():
    today=datetime.now().strftime("%Y-%m-%d")
    if state["instruments"] and state["inst_date"]==today:return state["instruments"]
    h=auth_header()
    if not h:return[]
    try:
        r=req.get(f"{KITE_BASE}/instruments/NFO",headers=h,timeout=30)
        if r.status_code!=200:return[]
        state["instruments"]=list(csv.DictReader(io.StringIO(r.text)))
        state["inst_date"]=today
        logger.info(f"Loaded {len(state['instruments'])} instruments")
        return state["instruments"]
    except:return[]

def get_option_symbols(idx,spot,n=10):
    insts=load_instruments()
    if not insts:return[],None
    nf="NIFTY" if idx.upper()=="NIFTY" else "BANKNIFTY"
    step=50 if idx.upper()=="NIFTY" else 100
    atm=round(spot/step)*step
    sw=set(atm+i*step for i in range(-n,n+1))
    today=datetime.now().strftime("%Y-%m-%d")
    m,ed=[],set()
    for i in insts:
        if i.get("instrument_type") not in("CE","PE"):continue
        if i.get("name")!=nf or not i.get("expiry") or i["expiry"]<today:continue
        try:sv=float(i["strike"])
        except:continue
        if sv in sw:m.append(i);ed.add(i["expiry"])
    if not m:return[],None
    ne=min(ed)
    return[x for x in m if x["expiry"]==ne],ne

def find_option(idx,spot,direction):
    insts=load_instruments()
    if not insts:return None
    nf="NIFTY" if idx.upper()=="NIFTY" else "BANKNIFTY"
    step=50 if idx.upper()=="NIFTY" else 100
    atm=round(spot/step)*step
    today=datetime.now().strftime("%Y-%m-%d")
    ot="CE" if direction=="BULLISH" else "PE"
    cands=[atm,atm+step] if direction=="BULLISH" else[atm,atm-step]
    found=[i for i in insts if i.get("name")==nf and i.get("instrument_type")==ot and i.get("expiry","")>=today and float(i.get("strike",0)) in cands]
    if not found:return None
    ne=min(set(f["expiry"] for f in found))
    found=[f for f in found if f["expiry"]==ne]
    syms=[f"NFO:{f['tradingsymbol']}" for f in found]
    qs=kite_get("/quote",params=[("i",s) for s in syms])
    qd=qs.get("data",{})
    best,bs=None,-1
    for inst in found:
        sym=f"NFO:{inst['tradingsymbol']}"
        q=qd.get(sym,{})
        ltp,vol,oi=q.get("last_price",0)or 0,q.get("volume",0)or 0,q.get("oi",0)or 0
        dp=q.get("depth",{})
        bid=dp.get("buy",[{}])[0].get("price",0) if dp.get("buy") else 0
        ask=dp.get("sell",[{}])[0].get("price",0) if dp.get("sell") else 0
        sp=abs(ask-bid) if ask and bid else 999
        sc=vol*.4+oi*.4+(1000/(sp+1))*.2
        if 50<=ltp<=300:sc*=1.5
        elif 30<=ltp<=500:sc*=1.2
        if sc>bs:bs=sc;best={"tradingsymbol":inst["tradingsymbol"],"strike":float(inst["strike"]),"type":ot,"expiry":inst["expiry"],"lot_size":int(inst.get("lot_size",65)),"ltp":ltp,"volume":vol,"oi":oi,"bid":bid,"ask":ask,"spread":round(sp,2)}
    return best

@app.route("/")
def index():
    if not session.get("authenticated"):return redirect("/access")
    return send_from_directory("static","index.html")

@app.route("/access",methods=["GET","POST"])
def access_page():
    err=""
    if request.method=="POST":
        pw=request.form.get("password","")
        if pw==ADMIN_PASSWORD:session["authenticated"]=True;session["is_admin"]=True;session.permanent=True;return redirect("/")
        if pw==ACCESS_PASSWORD:session["authenticated"]=True;session["is_admin"]=False;session.permanent=True;return redirect("/")
        err='<div style="color:#ff1744;font-size:12px;margin-bottom:8px">Wrong password</div>'
    return f'''<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="NiftyAgent"><title>Nifty Agent</title><style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,'SF Mono',monospace;background:#060a10;color:#e4eaf6;display:grid;place-items:center;height:100vh;padding:20px}}.box{{background:#111a2e;border:1px solid #1a2744;border-radius:16px;padding:32px;width:100%;max-width:360px;text-align:center}}input{{width:100%;padding:14px;border-radius:10px;border:1px solid #1a2744;background:#0c1220;color:#e4eaf6;font-size:16px;margin:16px 0;font-family:inherit;-webkit-appearance:none}}button{{width:100%;padding:14px;border-radius:10px;border:none;background:#00e5ff;color:#060a10;font-weight:700;font-size:16px;cursor:pointer}}input:focus{{outline:none;border-color:#00e5ff}}</style></head><body><div class="box"><div style="font-size:36px;margin-bottom:8px">📊</div><div style="font-size:18px;font-weight:700;margin-bottom:4px">Nifty Trading Agent</div><div style="font-size:12px;color:#5a6a8a;margin-bottom:4px">Smart Signals · Max 2/Day</div>{err}<form method="POST"><input name="password" type="password" placeholder="Enter password" autofocus autocomplete="off"><button type="submit">Enter</button></form></div></body></html>'''

@app.route("/admin/login")
def admin_login():return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}")

@app.route("/callback")
def callback():
    rt=request.args.get("request_token")
    if not rt:return jsonify({"error":"No token"}),400
    ck=hashlib.sha256(f"{API_KEY}{rt}{API_SECRET}".encode()).hexdigest()
    try:
        r=req.post(f"{KITE_BASE}/session/token",data={"api_key":API_KEY,"request_token":rt,"checksum":ck},headers={"X-Kite-Version":"3"},timeout=10)
        d=r.json()
        if d.get("status")=="success":
            state["access_token"]=d["data"]["access_token"];load_instruments();c=len(state.get("instruments")or[])
            return f'<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="background:#060a10;color:#00e5ff;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;text-align:center;padding:20px"><div style="font-size:48px;margin-bottom:16px">✅</div><h2>Kite Connected!</h2><p style="color:#5a6a8a;margin-top:8px">{c} instruments</p><p style="color:#5a6a8a">Close this tab.</p></body></html>'
        return jsonify({"error":"Failed","detail":d}),400
    except Exception as e:return jsonify({"error":str(e)}),500

@app.route("/api/ping")
def ping():return jsonify({"ok":True,"kite":state["access_token"] is not None})

@app.route("/api/status")
@require_login
def api_status():return jsonify({"authenticated":state["access_token"] is not None,"instruments_count":len(state["instruments"]) if state["instruments"] else 0,"is_admin":session.get("is_admin",False)})

@app.route("/api/market-data/<idx>")
@require_login
def market_data(idx):
    h=auth_header()
    if not h:return jsonify({"error":"Kite not connected"}),503
    ss="NSE:NIFTY 50" if idx.upper()=="NIFTY" else "NSE:NIFTY BANK"
    sr=kite_get("/quote",params=[("i",ss)])
    if "error" in sr:return jsonify(sr),500
    sf=sr.get("data",{}).get(ss,{});spot=sf.get("last_price",0)
    if not spot:return jsonify({"error":"No spot"}),500
    step=50 if idx.upper()=="NIFTY" else 100;atm=round(spot/step)*step
    oil,exp=get_option_symbols(idx,spot,10)
    rows,pv,mp=[],0,atm
    if oil:
        cd={}
        for i in range(0,len(oil),500):
            br=kite_get("/quote",params=[("i",f"NFO:{x['tradingsymbol']}") for x in oil[i:i+500]])
            if "data" in br:cd.update(br["data"])
        sl={(float(i["strike"]),i["instrument_type"]):f"NFO:{i['tradingsymbol']}" for i in oil}
        for s in sorted(set(float(i["strike"]) for i in oil)):
            ce,pe=cd.get(sl.get((s,"CE"),""),{}),cd.get(sl.get((s,"PE"),""),{})
            rows.append({"strike":int(s),"ce":{"ltp":ce.get("last_price",0)or 0,"oi":ce.get("oi",0)or 0,"oiChange":(ce.get("oi",0)or 0)-(ce.get("oi_day_low",0)or 0),"volume":ce.get("volume",0)or 0,"iv":0},"pe":{"ltp":pe.get("last_price",0)or 0,"oi":pe.get("oi",0)or 0,"oiChange":(pe.get("oi",0)or 0)-(pe.get("oi_day_low",0)or 0),"volume":pe.get("volume",0)or 0,"iv":0}})
        tp,tc=sum(r["pe"]["oi"] for r in rows),sum(r["ce"]["oi"] for r in rows)
        pv=round(tp/tc,2) if tc>0 else 0
        mv=float("inf")
        for r in rows:
            p=sum(max(0,o["ce"]["oi"]*(r["strike"]-o["strike"]))+max(0,o["pe"]["oi"]*(o["strike"]-r["strike"])) for o in rows)
            if p<mv:mv,mp=p,r["strike"]
    return jsonify({"data":{"spot":spot,"spot_ohlc":sf.get("ohlc",{}),"atm":atm,"pcr":pv,"max_pain":mp,"chain":rows,"expiry":exp,"recommended_ce":find_option(idx,spot,"BULLISH"),"recommended_pe":find_option(idx,spot,"BEARISH"),"timestamp":datetime.now().isoformat()}})

@app.route("/api/option-chain/<idx>")
@require_login
def oc(idx):return market_data(idx)

@app.route("/api/signals",methods=["GET","POST"])
@require_login
def signals_api():
    if request.method=="POST":
        d=request.get_json()
        if d:
            d["timestamp"]=datetime.now().isoformat()
            d["date"]=datetime.now().strftime("%Y-%m-%d")
            signal_history.insert(0,d)
            # Send to Telegram
            threading.Thread(target=send_telegram,args=(format_signal_msg(d),),daemon=True).start()
        if len(signal_history)>50:signal_history.pop()
        return jsonify({"ok":True})
    return jsonify({"signals":signal_history})

@app.route("/api/signals/update",methods=["POST"])
@require_login
def update_signal():
    d=request.get_json();sid=str(d.get("id",""));outcome=d.get("outcome","");ep=d.get("exit_price",0)
    for s in signal_history:
        if str(s.get("id",""))==sid:
            s["outcome"]=outcome;s["exit_price"]=ep;s["exit_time"]=datetime.now().isoformat()
            entry=s.get("entryPrice",0);lots=s.get("lots",1);ls=s.get("lotSize",65)
            if outcome=="sl_hit":s["pnl"]=round((s.get("slPrice",entry)-entry)*lots*ls,0)
            elif outcome=="t1_hit":s["pnl"]=round((s.get("t1Price",entry)-entry)*lots*ls,0)
            elif outcome=="t2_hit":s["pnl"]=round((s.get("t2Price",entry)-entry)*lots*ls,0)
            elif ep:s["pnl"]=round((ep-entry)*lots*ls,0)
            # Send outcome to Telegram
            threading.Thread(target=send_telegram,args=(format_outcome_msg(s),),daemon=True).start()
            break
    return jsonify({"ok":True,"signals":signal_history})

@app.route("/api/stock/<symbol>")
@require_login
def stock_analysis(symbol):
    h=auth_header()
    if not h:return jsonify({"error":"Kite not connected"}),503
    sym=f"NSE:{symbol.upper()}"
    q=kite_get("/quote",params=[("i",sym)])
    if "error" in q:return jsonify(q),500
    qd=q.get("data",{}).get(sym)
    if not qd:sym=f"BSE:{symbol.upper()}";q=kite_get("/quote",params=[("i",sym)]);qd=q.get("data",{}).get(sym)
    if not qd:return jsonify({"error":f"{symbol} not found"}),404
    token=qd.get("instrument_token")
    if not token:return jsonify({"error":"No token"}),500
    today=datetime.now();candles={}
    for label,days,interval in[("daily",180,"day"),("weekly",365,"day"),("intraday",5,"15minute")]:
        r=kite_get(f"/instruments/historical/{token}/{interval}",params={"from":(today-timedelta(days=days)).strftime("%Y-%m-%d"),"to":today.strftime("%Y-%m-%d"),"oi":"1"})
        if "data" in r:candles[label]=r["data"].get("candles",[])
    return jsonify({"data":{"symbol":symbol.upper(),"exchange":sym.split(":")[0],"ltp":qd.get("last_price",0),"ohlc":qd.get("ohlc",{}),"volume":qd.get("volume",0),"oi":qd.get("oi",0),"candles":candles,"timestamp":datetime.now().isoformat()}})

@app.route("/<path:path>")
def serve(path):
    if not session.get("authenticated"):return redirect("/access")
    return send_from_directory("static",path)

if __name__=="__main__":
    print(f"\n{'='*55}\n  NIFTY AGENT v4\n  {PUBLIC_URL}\n  User: {ACCESS_PASSWORD} | Admin: {ADMIN_PASSWORD}\n{'='*55}\n")
    app.run(host="0.0.0.0",port=PORT,debug=False)

# ─── ADMIN ENDPOINTS ────────────────────────────────────────
@app.route("/api/admin/disconnect", methods=["POST"])
@require_login
def admin_disconnect():
    if not session.get("is_admin"):return jsonify({"error":"Admin only"}),403
    state["access_token"]=None;state["instruments"]=None;state["inst_date"]=None
    logger.info("ADMIN: Kite disconnected!")
    if TG_ENABLED:threading.Thread(target=send_telegram,args=("⚠️ <b>ADMIN:</b> Kite session disconnected. Live data stopped.",),daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/admin/clear-signals", methods=["POST"])
@require_login
def admin_clear():
    if not session.get("is_admin"):return jsonify({"error":"Admin only"}),403
    signal_history.clear()
    return jsonify({"ok":True})

@app.route("/api/admin/telegram-test", methods=["POST"])
@require_login
def telegram_test():
    if not session.get("is_admin"):return jsonify({"error":"Admin only"}),403
    if not TG_ENABLED:return jsonify({"error":"Set TG_BOT_TOKEN and TG_CHANNEL_ID in Render env vars"}),400
    send_telegram("🔔 <b>Nifty Agent — Test</b>\n\nTelegram is working! ✅\nSignals will appear here automatically.")
    return jsonify({"ok":True,"message":"Test sent!"})

@app.route("/api/admin/telegram-status")
@require_login
def telegram_status():
    return jsonify({"enabled":TG_ENABLED,"bot_set":bool(TG_BOT_TOKEN),"channel_set":bool(TG_CHANNEL_ID)})

# ─── NIFTY 50 STOCK SCANNER ────────────────────────────────
NIFTY50=[
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
    "BAJAJ-AUTO","BAJFINANCE","BAJAJFINSV","BEL","BPCL",
    "BHARTIARTL","BRITANNIA","CIPLA","COALINDIA","DRREDDY",
    "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
    "INFY","ITC","JSWSTEEL","KOTAKBANK","LT",
    "M&M","MARUTI","NESTLEIND","NTPC","ONGC",
    "POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATAMOTORS","TATASTEEL","TCS",
    "TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO"
]

scan_results={"picks":[],"last_scan":None,"scanning":False}

def compute_rsi(prices,n=14):
    if len(prices)<n+1:return 50
    gains,losses=0,0
    for i in range(len(prices)-n,len(prices)):
        d=prices[i]-prices[i-1]
        if d>0:gains+=d
        else:losses-=d
    ag,al=gains/n,losses/n
    return 100 if al==0 else round(100-100/(1+ag/al),1)

def compute_ema(prices,n):
    if len(prices)<n:return prices[-1] if prices else 0
    k=2/(n+1);e=sum(prices[:n])/n
    for i in range(n,len(prices)):e=prices[i]*k+e*(1-k)
    return round(e,2)

def scan_stock(symbol):
    """Analyze a single stock and return a score + recommendation."""
    try:
        sym=f"NSE:{symbol}"
        # Get quote
        q=kite_get("/quote",params=[("i",sym)])
        if "error" in q or "data" not in q:return None
        qd=q.get("data",{}).get(sym)
        if not qd:return None

        ltp=qd.get("last_price",0)
        if not ltp:return None
        ohlc=qd.get("ohlc",{})
        volume=qd.get("volume",0) or 0
        prev_close=ohlc.get("close",ltp)
        day_change=round(((ltp-prev_close)/prev_close)*100,2) if prev_close else 0
        token=qd.get("instrument_token")
        if not token:return None

        # Get daily historical (60 days)
        today=datetime.now()
        hist=kite_get(f"/instruments/historical/{token}/day",
                      params={"from":(today-timedelta(days=90)).strftime("%Y-%m-%d"),
                              "to":today.strftime("%Y-%m-%d"),"oi":"0"})
        candles=hist.get("data",{}).get("candles",[]) if "data" in hist else []
        if len(candles)<20:return None

        closes=[c[4] for c in candles]
        highs=[c[2] for c in candles]
        lows=[c[3] for c in candles]
        volumes=[c[5] for c in candles]

        # ─── 8-FACTOR SCORING ──────────────────────────
        score=0
        factors=[]

        # 1. RSI (14)
        rsi_val=compute_rsi(closes)
        if rsi_val<30:score+=2;factors.append(("RSI "+str(rsi_val)+" oversold","bull"))
        elif rsi_val<40:score+=1;factors.append(("RSI "+str(rsi_val)+" nearing oversold","bull"))
        elif rsi_val>70:score-=2;factors.append(("RSI "+str(rsi_val)+" overbought","bear"))
        elif rsi_val>60:score-=1;factors.append(("RSI "+str(rsi_val)+" nearing overbought","bear"))
        else:factors.append(("RSI "+str(rsi_val)+" neutral","neutral"))

        # 2. EMA 9/21 crossover
        ema9=compute_ema(closes,9)
        ema21=compute_ema(closes,21)
        if ema9>ema21*1.005:score+=1;factors.append(("9EMA > 21EMA","bull"))
        elif ema9<ema21*0.995:score-=1;factors.append(("9EMA < 21EMA","bear"))
        else:factors.append(("EMAs flat","neutral"))

        # 3. Price vs 50 EMA (trend)
        ema50=compute_ema(closes,50) if len(closes)>=50 else compute_ema(closes,20)
        if ltp>ema50*1.02:score+=1;factors.append(("Above 50EMA — uptrend","bull"))
        elif ltp<ema50*0.98:score-=1;factors.append(("Below 50EMA — downtrend","bear"))
        else:factors.append(("Near 50EMA","neutral"))

        # 4. Volume surge (today vs 20-day avg)
        avg_vol=sum(volumes[-20:])/20 if len(volumes)>=20 else sum(volumes)/len(volumes)
        vol_ratio=round(volume/avg_vol,1) if avg_vol>0 else 1
        if vol_ratio>2 and day_change>0:score+=1;factors.append(("Volume surge "+str(vol_ratio)+"x + up","bull"))
        elif vol_ratio>2 and day_change<0:score-=1;factors.append(("Volume surge "+str(vol_ratio)+"x + down","bear"))
        else:factors.append(("Volume normal "+str(vol_ratio)+"x","neutral"))

        # 5. Supertrend
        if len(closes)>=10:
            atr=sum(abs(closes[i]-closes[i-1]) for i in range(len(closes)-10,len(closes)))/10
            st_val=closes[-1]-2*atr
            if closes[-1]>st_val:score+=1;factors.append(("Supertrend bullish","bull"))
            else:score-=1;factors.append(("Supertrend bearish","bear"))

        # 6. Price momentum (5-day vs 20-day return)
        if len(closes)>=20:
            ret5=((closes[-1]-closes[-5])/closes[-5])*100 if closes[-5] else 0
            ret20=((closes[-1]-closes[-20])/closes[-20])*100 if closes[-20] else 0
            if ret5>1 and ret20>3:score+=1;factors.append(("Strong momentum +"+str(round(ret5,1))+"%/5d","bull"))
            elif ret5<-1 and ret20<-3:score-=1;factors.append(("Weak momentum "+str(round(ret5,1))+"%/5d","bear"))
            else:factors.append(("Moderate momentum","neutral"))

        # 7. Support/Resistance proximity
        recent_high=max(highs[-20:])
        recent_low=min(lows[-20:])
        range_pct=((recent_high-recent_low)/recent_low)*100 if recent_low else 0
        dist_from_low=((ltp-recent_low)/recent_low)*100 if recent_low else 0
        dist_from_high=((recent_high-ltp)/ltp)*100 if ltp else 0
        if dist_from_low<1.5:score+=1;factors.append(("Near 20-day support — bounce zone","bull"))
        elif dist_from_high<1.5:score-=1;factors.append(("Near 20-day resistance","bear"))
        else:factors.append(("Mid-range","neutral"))

        # 8. Candle pattern (last 3 days)
        if len(closes)>=3:
            last3_bullish=all(closes[-i]>closes[-i-1] for i in range(1,3))
            last3_bearish=all(closes[-i]<closes[-i-1] for i in range(1,3))
            if last3_bullish:score+=1;factors.append(("3 consecutive green candles","bull"))
            elif last3_bearish:score-=1;factors.append(("3 consecutive red candles","bear"))
            else:factors.append(("Mixed candles","neutral"))

        # ─── CALCULATE TARGETS ──────────────────────────
        atr20=sum(highs[i]-lows[i] for i in range(-20,0))/20 if len(highs)>=20 else (highs[-1]-lows[-1])
        pp=(recent_high+recent_low+ltp)/3

        if score>=3:
            verdict="STRONG BUY"
            entry=ltp
            sl=round(max(recent_low,ltp-1.5*atr20),2)
            t1=round(ltp+2*atr20,2)
            t2=round(ltp+3*atr20,2)
        elif score>=1:
            verdict="BUY"
            entry=ltp
            sl=round(max(recent_low,ltp-1.5*atr20),2)
            t1=round(ltp+1.5*atr20,2)
            t2=round(ltp+2.5*atr20,2)
        elif score<=-3:
            verdict="STRONG SELL"
            entry=ltp
            sl=round(min(recent_high,ltp+1.5*atr20),2)
            t1=round(ltp-2*atr20,2)
            t2=round(ltp-3*atr20,2)
        elif score<=-1:
            verdict="SELL"
            entry=ltp
            sl=round(min(recent_high,ltp+1.5*atr20),2)
            t1=round(ltp-1.5*atr20,2)
            t2=round(ltp-2.5*atr20,2)
        else:
            verdict="HOLD"
            entry=sl=t1=t2=0

        return {
            "symbol":symbol,"ltp":ltp,"change":day_change,
            "volume":volume,"vol_ratio":vol_ratio,
            "rsi":rsi_val,"ema9":ema9,"ema21":ema21,"ema50":ema50,
            "score":score,"verdict":verdict,
            "entry":entry,"sl":sl,"t1":t1,"t2":t2,
            "atr":round(atr20,2),
            "factors":factors,
            "high_20d":recent_high,"low_20d":recent_low,
        }
    except Exception as e:
        logger.error(f"Scan error {symbol}: {e}")
        return None

def run_scanner():
    """Background scanner — runs every 5 minutes during market hours."""
    while True:
        try:
            now=datetime.now()
            # Only scan during market hours (approx IST in UTC)
            if auth_header() and 3<=now.hour<=10:
                if not scan_results["scanning"]:
                    scan_results["scanning"]=True
                    logger.info("Starting Nifty 50 scan...")
                    picks=[]
                    for i,sym in enumerate(NIFTY50):
                        result=scan_stock(sym)
                        if result:picks.append(result)
                        # Rate limiting — 3 req/sec, we make ~2 per stock
                        if i%3==2:time.sleep(1.2)

                    # Sort by absolute score (strongest signals first)
                    picks.sort(key=lambda x:abs(x["score"]),reverse=True)
                    scan_results["picks"]=picks
                    scan_results["last_scan"]=datetime.now().isoformat()
                    scan_results["scanning"]=False
                    logger.info(f"Scan complete: {len(picks)} stocks analyzed")

                    # Send top picks to Telegram
                    if TG_ENABLED and picks:
                        buys=[p for p in picks if p["score"]>=3][:3]
                        sells=[p for p in picks if p["score"]<=-3][:3]
                        if buys or sells:
                            msg="📊 <b>NIFTY 50 SCAN — TOP PICKS</b>\n\n"
                            if buys:
                                msg+="🟢 <b>TOP BUYS:</b>\n"
                                for b in buys:
                                    msg+=f"  • <b>{b['symbol']}</b> ₹{b['ltp']} ({b['change']:+.1f}%) Score:{b['score']}\n"
                                    msg+=f"    Entry:₹{b['entry']} SL:₹{b['sl']} T1:₹{b['t1']}\n"
                            if sells:
                                msg+="\n🔴 <b>TOP SELLS:</b>\n"
                                for s in sells:
                                    msg+=f"  • <b>{s['symbol']}</b> ₹{s['ltp']} ({s['change']:+.1f}%) Score:{s['score']}\n"
                                    msg+=f"    Entry:₹{s['entry']} SL:₹{s['sl']} T1:₹{s['t1']}\n"
                            msg+=f"\n🕐 Scanned at {datetime.now().strftime('%H:%M IST')}"
                            threading.Thread(target=send_telegram,args=(msg,),daemon=True).start()
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            scan_results["scanning"]=False
        time.sleep(300) # Every 5 minutes

# Start scanner thread
threading.Thread(target=run_scanner,daemon=True).start()

@app.route("/api/scanner")
@require_login
def scanner_api():
    """Return latest scan results."""
    picks=scan_results.get("picks",[])
    buys=[p for p in picks if p["score"]>=1]
    sells=[p for p in picks if p["score"]<=-1]
    buys.sort(key=lambda x:x["score"],reverse=True)
    sells.sort(key=lambda x:x["score"])
    return jsonify({
        "data":{
            "top_buys":buys[:5],
            "top_sells":sells[:5],
            "all":picks,
            "last_scan":scan_results.get("last_scan"),
            "scanning":scan_results.get("scanning",False),
            "total_scanned":len(picks),
        }
    })

@app.route("/api/scanner/trigger",methods=["POST"])
@require_login
def trigger_scan():
    """Admin can manually trigger a scan."""
    if not session.get("is_admin"):return jsonify({"error":"Admin only"}),403
    if scan_results.get("scanning"):return jsonify({"error":"Scan already in progress"}),400
    def do_scan():
        scan_results["scanning"]=True
        picks=[]
        for i,sym in enumerate(NIFTY50):
            result=scan_stock(sym)
            if result:picks.append(result)
            if i%3==2:time.sleep(1.2)
        picks.sort(key=lambda x:abs(x["score"]),reverse=True)
        scan_results["picks"]=picks
        scan_results["last_scan"]=datetime.now().isoformat()
        scan_results["scanning"]=False
    threading.Thread(target=do_scan,daemon=True).start()
    return jsonify({"ok":True,"message":"Scan started"})
