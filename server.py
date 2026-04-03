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
        if d:d["timestamp"]=datetime.now().isoformat();d["date"]=datetime.now().strftime("%Y-%m-%d");signal_history.insert(0,d)
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

# ─── ADMIN KILL SWITCH ──────────────────────────────────────
@app.route("/api/admin/disconnect", methods=["POST"])
@require_login
def admin_disconnect():
    """Admin can kill the Kite session — stops all live data for everyone."""
    if not session.get("is_admin"):
        return jsonify({"error": "Admin only"}), 403
    state["access_token"] = None
    state["instruments"] = None
    state["inst_date"] = None
    logger.info("ADMIN: Kite session disconnected!")
    return jsonify({"ok": True, "message": "Kite disconnected. Dashboard will show mock data."})

@app.route("/api/admin/clear-signals", methods=["POST"])
@require_login  
def admin_clear():
    """Admin can clear all signal history."""
    if not session.get("is_admin"):
        return jsonify({"error": "Admin only"}), 403
    signal_history.clear()
    return jsonify({"ok": True})
