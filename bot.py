import html, json, os, time, uuid, threading, requests, pandas as pd
from flask import Flask

TOKEN = os.environ["BOT_TOKEN"].strip().split("bot")[-1].strip("/")
CHAT = str(os.environ["CHAT_ID"]).strip()
ADMIN = str(os.environ["ADMIN_ID"]).strip()

TEMA_CONSULTAS = 0
SIMBOLOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "NEARUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT"]
CONF_MIN, RIESGO_ATR, COOLDOWN, LIMITE_HORA = 60, 1.5, 14400, 5
TG = f"https://telegram.org{TOKEN}"

est = json.load(open("estado.json", encoding="utf-8")) if os.path.exists("estado.json") else {"offset": 0, "abiertas": {}, "hist": [], "ultimas": {}, "consultas_user": {}}

def guardar():
    try:
        with open("estado.json", "w", encoding="utf-8") as fp:
            json.dump(est, fp)
    except Exception as e:
        print("save_err:", e)

app = Flask(__name__)

@app.route("/")
def ping():
    return "OK", 200

def web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def tg(metodo, **kw):
    try:
        return requests.post(f"{TG}/{metodo}", data=kw, timeout=15).json()
    except Exception as e:
        print("tg_err:", e)
        return {}

def enviar(txt, chat=CHAT, tid=None, rid=None):
    kw = {"chat_id": chat, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": "true", "allow_sending_without_reply": "true"}
    if tid: kw["message_thread_id"] = tid
    if rid: kw["reply_to_message_id"] = rid
    return tg("sendMessage", **kw)

def velas(sim, tf="15", lim=250):
    for dom in ["://binance.com", "api.binance.us"]:
        try:
            r = requests.get(f"https://{dom}/api/v3/klines", timeout=10, params={"symbol": sim, "interval": "1h" if tf == "60" else "15m", "limit": lim})
            r.raise_for_status()
            lista = r.json() or []
            if lista and isinstance(lista, list):
                df = pd.DataFrame(lista).iloc[:, :6]
                df.columns = ["t", "o", "h", "l", "c", "v"]
                for c in "ohlcv": df[c] = df[c].astype(float)
                return df
        except Exception:
            pass
    try:
        base = "XBT" if sim == "BTCUSDT" else sim.replace("USDT", "")
        r = requests.get("https://kraken.com", timeout=12, params={"pair": f"{base}USD", "interval": 60 if tf == "60" else 15})
        r.raise_for_status()
        res = r.json().get("result") or {}
        clave = next((k for k in res.keys() if k != "last"), None)
        if clave:
            df = pd.DataFrame(res[clave][-lim:]).iloc[:, :7]
            df.columns = ["t", "o", "h", "l", "c", "vwap", "v"]
            for c in "ohlcv": df[c] = df[c].astype(float)
            return df[["t", "o", "h", "l", "c", "v"]]
    except Exception as e:
        print("kraken_err:", sim, e)
    return None

ema = lambda s, n: s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    ru = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    rd = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + ru / rd)

def atr(df, n=14):
    tr = pd.concat([df["h"] - df["l"], (df["h"] - df["c"].shift()).abs(), (df["l"] - df["c"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

f = lambda x: f"{x:,.4f}".rstrip("0").rstrip(".")

def analizar_moneda(sim, usuario):
    sim = html.escape(sim.upper().strip())
    if not sim.endswith("USDT"): sim += "USDT"
    df, d1 = velas(sim), velas(sim, "60")
    if df is None or d1 is None or len(df) < 150:
        return f"⚠️ {usuario}, sin datos para <b>{sim}</b>."
    df["r"], df["e20"], df["e50"], df["a"], df["vma"] = rsi(df["c"]), ema(df["c"], 20), ema(df["c"], 50), atr(df), df["v"].rolling(20).mean()
    e200 = ema(d1["c"], 200).iloc[-1]
    b = df.iloc[-1]; p, at = b["c"], b["a"]; r = at * RIESGO_ATR
    t15 = "Alcista 🟢" if b["e20"] > b["e50"] else "Bajista 🔴"
    t1h = "Alcista 🟢" if p > e200 else "Bajista 🔴"
    er = "Sobrecompra 🔥" if b["r"] >= 70 else "Sobreventa ❄️" if b["r"] <= 30 else "Neutral ⚖️"
    vr = b["v"] / b["vma"] if b["vma"] > 0 else 1.0
    return (f"👤 <b>Pedido de: {usuario}</b>\n🔎 <b>Análisis · {sim}</b> (15m)\nPrecio: <b>{f(p)}</b>\n"
            f"15m: {t15} · 1H: {t1h}\nRSI (14): {b['r']:.1f} ({er}) · Vol: {vr:.1f}x\n"
            f"SL ref: {f(p - r)} · TP ref: {f(p + r * 2)}")

def score(sim):
    df, d1 = velas(sim), velas(sim, "60")
    if df is None or d1 is None or len(df) < 150: return None
    df["r"], df["e20"], df["e50"], df["a"], df["vma"] = rsi(df["c"]), ema(df["c"], 20), ema(df["c"], 50), atr(df), df["v"].rolling(20).mean()
    e200 = ema(d1["c"], 200).iloc[-1]
    p2, a, b = df.iloc[-3], df.iloc[-2], df.iloc[-1]; p, at = b["c"], b["a"]
    alc = (a["e20"] <= a["e50"] and b["e20"] > b["e50"]) or (p2["e20"] <= p2["e50"] and a["e20"] > a["e50"] and b["e20"] > b["e50"])
    baj = (a["e20"] >= a["e50"] and b["e20"] < b["e50"]) or (p2["e20"] >= p2["e50"] and a["e20"] < a["e50"] and b["e20"] < b["e50"])
    if alc: d = "LONG"
    elif baj: d = "SHORT"
    else: return None
    conf, notas = 40, []
    if (d == "LONG") == (p > e200): conf += 20; notas.append("EMA200 1h a favor")
    else: notas.append("contra EMA200 1h")
    if (35 < b["r"] < 68) if d == "LONG" else (32 < b["r"] < 65): conf += 15; notas.append(f"RSI {b['r']:.0f}")
    if b["v"] > b["vma"] * 1.1: conf += 15; notas.append(f"vol {b['v']/b['vma']:.1f}x")
    if at / p > 0.003: conf += 10; notas.append("volatilidad sana")
    if conf < CONF_MIN: return None
    r = at * RIESGO_ATR
    sl, t1, t2 = (p - r, p + r * 2, p + r * 3) if d == "LONG" else (p + r, p - r * 2, p - r * 3)
    return dict(sim=sim, dir=d, entrada=p, sl=sl, tp1=t1, tp2=t2, conf=conf, notas=", ".join(notas))

def plantilla(s):
    i, k = ("🟢", 1) if s["dir"] == "LONG" else ("🔴", -1)
    pc = lambda v: (v - s["entrada"]) / s["entrada"] * 100 * k
    return (f"{i} <b>{s['dir']} {s['sim']}</b> · 15m\nEntrada: {f(s['entrada'])}\n"
            f"SL: {f(s['sl'])} ({pc(s['sl']):+.2f}%)\nTP1: {f(s['tp1'])} ({pc(s['tp1']):+.2f}%) · "
            f"TP2: {f(s['tp2'])} ({pc(s['tp2']):+.2f}%)\nR:R 1:2 · Confianza {s['conf']}/100\nContexto: {s['notas']}")

def revisar():
    for sid, o in list(est.get("abiertas", {}).items()):
        df = velas(o["sim"], "15", 3)
        if df is None: continue
        c = df.iloc[-1]
        g = ("SL" if c["l"] <= o["sl"] else "TP2" if c["h"] >= o["tp2"] else "TP1" if c["h"] >= o["tp1"] else None) if o["dir"] == "LONG" else ("SL" if c["h"] >= o["sl"] else "TP2" if c["l"] <= o["tp2"] else "TP1" if c["l"] <= o["tp1"] else None)
        if g:
            enviar(f"#{sid} {o['sim']} cerrada en <b>{g}</b>")
            est.setdefault("hist", []).append({"id": sid, "res": g})
            est["abiertas"].pop(sid)
            guardar()

def comandos():
    r = tg("getUpdates", offset=est.get("offset", 0), timeout=10).get("result") or []
    ahora = time.time()
    if not isinstance(est.get("consultas_user"), dict): est["consultas_user"] = {}
    for u in r:
        est["offset"] = u["update_id"] + 1
        m = u.get("message") or {}; txt = (m.get("text") or "").strip()
        cid = str(m.get("chat", {}).get("id", "")); rem = m.get("from") or {}
        uid = str(rem.get("id", "")); tid = m.get("message_thread_id"); mid = m.get("message_id")
        nombre = html.escape(f"@{rem['username']}" if rem.get("username") else rem.get("first_name", "Miembro"))
        if (cid != CHAT and uid != ADMIN) or not txt or not cid: continue
        if tid is None and uid != ADMIN and not txt.startswith(("/stats", "/salud", "/estado")):
            tg("deleteMessage", chat_id=cid, message_id=mid); continue
        if txt.startswith(("/estado", "/salud")):
            enviar(f"✅ <b>Bot Activo 24/7</b> · {len(SIMBOLOS)} pares · Abiertas: {len(est.get('abiertas', {}))}", cid, tid, mid)
        elif txt.startswith("/stats"):
            h = est.get("hist", [])
            tp = sum(1 for x in h if x.get("res") in ("TP1", "TP2"))
            sl = sum(1 for x in h if x.get("res") == "SL")
            ef = (tp / (tp + sl) * 100) if (tp + sl) > 0 else 0
            enviar(f"📈 <b>Stats:</b> Total {tp+sl} | TP: {tp} | SL: {sl} | Efectividad: <b>{ef:.1f}%</b>", cid, tid, mid)
        elif txt.startswith("/analizar"):
            if TEMA_CONSULTAS and tid != TEMA_CONSULTAS: continue
            partes = txt.split()
            if len(partes) < 2:
                enviar(f"⚠️ {nombre}, usa: <code>/analizar BTC</code>", cid, tid, mid); continue
            hu = [t for t in est["consultas_user"].get(uid, []) if ahora - t < 3600]
            if len(hu) >= LIMITE_HORA and uid != ADMIN:
                enviar(f"⏳ {nombre}, límite de {LIMITE_HORA}/hora alcanzado.", cid, tid, mid); continue
            hu.append(ahora); est["consultas_user"][uid] = hu
            enviar(analizar_moneda(partes[1], nombre), cid, tid, mid)
        guardar()

def escaner():
    est.setdefault("ultimas", {}); est.setdefault("abiertas", {})
    while True:
        try:
            revisar()
            ahora = time.time()
            for sim in SIMBOLOS:
                if ahora - est["ultimas"].get(sim, 0) < COOLDOWN: continue
                s = score(sim)
                if s:
                    sid = str(uuid.uuid4())[:4].upper()
                    enviar(f"🚨 <b>SEÑAL #{sid}</b>\n\n" + plantilla(s))
                    est["abiertas"][sid] = s; est["ultimas"][sim] = ahora; guardar(); time.sleep(2)
        except Exception as e:
            print("escaner_err:", e)
        time.sleep(360)

if __name__ == "__main__":
    threading.Thread(target=web, daemon=True).start()
    threading.Thread(target=escaner, daemon=True).start()
    print("Bot 24/7 iniciado...")
    while True:
        try: comandos()
        except Exception as e: print("loop_err:", e); time.sleep(3)
