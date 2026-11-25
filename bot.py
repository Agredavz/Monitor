import json, os, time, uuid, requests, pandas as pd

TOKEN = os.environ["BOT_TOKEN"].strip()
if "bot" in TOKEN:
    TOKEN = TOKEN.split("bot")[-1].strip("/")
CHAT = str(os.environ["CHAT_ID"]).strip()
ADMIN = str(os.environ["ADMIN_ID"]).strip()

TEMA_CONSULTAS = 0  
SIMBOLOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT"]
CONF_MIN, RIESGO_ATR, COOLDOWN = 70, 1.5, 14400
LIMITE_POR_USUARIO_HORA = 5

TG = "https://" + "api.telegram.org/bot" + TOKEN
est = json.load(open("estado.json"))
fin = time.time() + 200

def tg(metodo, **kw):
    try:
        r = requests.post(f"{TG}/{metodo}", data=kw, timeout=15).json()
        if not r.get("ok"):
            print("tg_err:", r)
        return r
    except Exception as e:
        print("tg_exc:", e)
        return {}

def enviar(txt, chat=CHAT, thread_id=None, reply_id=None):
    kw = {"chat_id": chat, "text": txt, "parse_mode": "HTML", "disable_web_page_preview": "true","allow_sending_without_reply": "true"}
    if thread_id:
        kw["message_thread_id"] = thread_id
    if reply_id:
        kw["reply_to_message_id"] = reply_id
    return tg("sendMessage", **kw)

def velas(sim, tf="15", lim=250):
    base = sim.replace("USDT", "")
    # CryptoCompare API pública (100% abierta en GitHub Actions / EE.UU.)
    endpoint = "histohour" if tf == "60" else "histominute"
    agg = 1 if tf == "60" else 15
    host = ".".join(["min-api", "cryptocompare", "com"])
    url = "/".join(["https:", "", host, "data", "v2", endpoint])

    try:

        r = requests.get(
            url,
            timeout=12,
            params={"fsym": base, "tsym": "USDT", "limit": lim, "aggregate": agg}
        )
        r.raise_for_status()
        data = r.json().get("Data", {}).get("Data") or []
        if not data:
            return None
        df = pd.DataFrame(data)
        df = df.rename(columns={
            "time": "t",
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volumefrom": "v"
        })
        for c in "ohlcv":
            df[c] = df[c].astype(float)
        return df[["t", "o", "h", "l", "c", "v"]]
    except Exception as e:
        print("cc", sim, e)
        return None

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    ru = up.ewm(alpha=1/n, adjust=False).mean()
    rd = dn.ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + ru / rd)

def atr(df, n=14):
    tr = pd.concat([df["h"] - df["l"], (df["h"] - df["c"].shift()).abs(),
                    (df["l"] - df["c"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

f = lambda x: f"{x:,.4f}".rstrip("0").rstrip(".")

def analizar_moneda(sim, usuario_nombre):
    sim = sim.upper().strip()
    if not sim.endswith("USDT"):
        sim += "USDT"
    df, d1 = velas(sim), velas(sim, "60")
    if df is None or d1 is None or len(df) < 150:
        return f"⚠️ {usuario_nombre}, no encontré datos suficientes para <b>{sim}</b>. Verifica que el símbolo exista."
    df["r"] = rsi(df["c"]); df["e20"] = ema(df["c"], 20)
    df["e50"] = ema(df["c"], 50); df["a"] = atr(df)
    df["vma"] = df["v"].rolling(20).mean(); e200 = ema(d1["c"], 200).iloc[-1]
    b = df.iloc[-1]; p, at = b["c"], b["a"]
    tend_15m = "Alcista (EMA20 > EMA50) 🟢" if b["e20"] > b["e50"] else "Bajista (EMA20 < EMA50) 🔴"
    tend_1h = "Alcista (Sobre EMA200) 🟢" if p > e200 else "Bajista (Bajo EMA200) 🔴"
    estado_rsi = "Sobrecompra 🔥" if b["r"] >= 70 else "Sobreventa ❄️" if b["r"] <= 30 else "Neutral ⚖️"
    vol_rel = b["v"] / b["vma"] if b["vma"] > 0 else 1.0
    r = at * RIESGO_ATR
    return (f"👤 <b>Pedido de: {usuario_nombre}</b>\n"
            f"🔎 <b>Análisis Rápido · {sim}</b> (15m)\n"
            f"Precio actual: <b>{f(p)}</b>\n"
            f"Tendencia 15m: {tend_15m}\n"
            f"Tendencia 1H: {tend_1h}\n"
            f"RSI (14): {b['r']:.1f} ({estado_rsi})\n"
            f"Volumen: {vol_rel:.1f}x vs promedio\n"
            f"Soporte dinámico (SL ref): {f(p - r)}\n"
            f"Resistencia dinámica (TP ref): {f(p + r * 2)}")

def score(sim):
    df, d1 = velas(sim), velas(sim, "60")
    if df is None or d1 is None or len(df) < 150: return None
    df["r"] = rsi(df["c"]); df["e20"] = ema(df["c"], 20)
    df["e50"] = ema(df["c"], 50); df["a"] = atr(df)
    df["vma"] = df["v"].rolling(20).mean(); e200 = ema(d1["c"], 200).iloc[-1]
    a, b = df.iloc[-2], df.iloc[-1]; p, at = b["c"], b["a"]
    if a["e20"] <= a["e50"] and b["e20"] > b["e50"]: d = "LONG"
    elif a["e20"] >= a["e50"] and b["e20"] < b["e50"]: d = "SHORT"
    else: return None
    conf, notas = 40, []
    if (d == "LONG") == (p > e200): conf += 20; notas.append("EMA200 1h a favor")
    else: notas.append("contra EMA200 1h")
    if (35 < b["r"] < 68) if d == "LONG" else (32 < b["r"] < 65):
        conf += 15; notas.append(f"RSI {b['r']:.0f}")
    if b["v"] > b["vma"] * 1.2: conf += 15; notas.append(f"vol {b['v']/b['vma']:.1f}x")
    if at / p > 0.004: conf += 10; notas.append("volatilidad sana")
    if conf < CONF_MIN: return None
    r = at * RIESGO_ATR
    sl, t1, t2 = (p-r, p+r*2, p+r*3) if d == "LONG" else (p+r, p-r*2, p-r*3)
    return dict(sim=sim, dir=d, entrada=p, sl=sl, tp1=t1, tp2=t2, conf=conf, notas=", ".join(notas))

def plantilla(s):
    i = "🟢" if s["dir"] == "LONG" else "🔴"; k = 1 if s["dir"] == "LONG" else -1
    pc = lambda v: (v - s["entrada"]) / s["entrada"] * 100 * k
    return (f"{i} <b>{s['dir']} {s['sim']}</b> · 15m\nEntrada: {f(s['entrada'])}\n"
            f"SL: {f(s['sl'])} ({pc(s['sl']):+.2f}%)\nTP1: {f(s['tp1'])} ({pc(s['tp1']):+.2f}%) · "
            f"TP2: {f(s['tp2'])} ({pc(s['tp2']):+.2f}%)\nR:R 1:2 · Confianza {s['conf']}/100\n"
            f"Contexto: {s['notas']}\nInvalidación: cierre 15m más allá del SL")

def revisar():
    for sid, o in list(est["abiertas"].items()):
        df = velas(o["sim"], "15", 3)
        if df is None: continue
        c = df.iloc[-1]; g = None
        if o["dir"] == "LONG":
            g = "SL" if c["l"] <= o["sl"] else "TP2" if c["h"] >= o["tp2"] else "TP1" if c["h"] >= o["tp1"] else None
        else:
            g = "SL" if c["h"] >= o["sl"] else "TP2" if c["l"] <= o["tp2"] else "TP1" if c["l"] <= o["tp1"] else None
        if g:
            enviar(f"#{sid} {o['sim']} cerrada en <b>{g}</b>")
            est.setdefault("hist", []).append({"id": sid, "res": g})
            est["abiertas"].pop(sid)

def comandos():
    r = tg("getUpdates", offset=est["offset"], timeout=0).get("result") or []
    ahora = time.time()
    if not isinstance(est.get("consultas_user"), dict):
        est["consultas_user"] = {}
        
    for u in r:
        est["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        txt = (m.get("text") or "").strip()
        cid = str(m.get("chat", {}).get("id", ""))
        remitente = m.get("from") or {}
        uid = str(remitente.get("id", ""))
        tid = m.get("message_thread_id")
        mid = m.get("message_id")
        
        uname = remitente.get("username")
        nombre = f"@{uname}" if uname else remitente.get("first_name", "Miembro")
        
        if cid != CHAT and uid != ADMIN:
            continue
        if not txt or not cid:
            continue
            
        if txt.startswith("/analizar"):
            if TEMA_CONSULTAS and tid != TEMA_CONSULTAS:
                continue
            partes = txt.split()
            if len(partes) < 2:
                enviar(f"⚠️ {nombre}, escribe la moneda así: <code>/analizar BTC</code> o <code>/analizar XRPUSDT</code>", cid, tid, mid)
                continue
                
            historial_u = [t for t in est["consultas_user"].get(uid, []) if ahora - t < 3600]
            if uid != ADMIN and len(historial_u) >= LIMITE_POR_USUARIO_HORA:
                enviar(f"⏳ {nombre}, ya usaste tus <b>{LIMITE_POR_USUARIO_HORA} análisis por hora</b>. Podrás pedir otro en unos minutos.", cid, tid, mid)
                est["consultas_user"][uid] = historial_u
                continue
                
            historial_u.append(ahora)
            est["consultas_user"][uid] = historial_u
            moneda = partes [1]
            enviar(analizar_moneda(moneda, nombre), cid, tid, mid)
            
        elif txt.startswith("/stats"):
            h = est.get("hist", [])
            if not h: enviar("Aún sin señales cerradas.", cid, tid, mid)
            else:
                t1 = sum(1 for x in h if x["res"] in ("TP1","TP2"))
                enviar(f"Cerradas: {len(h)}\nTP: {t1}\nAcierto: {t1/len(h)*100:.1f}%", cid, tid, mid)
        elif txt.startswith("/salud"):
            enviar(f"✅ vivo\nabiertas: {len(est['abiertas'])}", cid, tid, mid)
        elif txt.startswith("/estado"):
            enviar("Comandos: /analizar MONEDA · /stats · /salud", cid, tid, mid)

revisar()
comandos()

for s in SIMBOLOS:
    if time.time() > fin: break
    try:
        x = score(s)
        if not x: continue
        clave = f"{s}_{x['dir']}"
        if time.time() - est["ultimo"].get(clave, 0) < COOLDOWN: continue
        est["ultimo"][clave] = time.time()
        sid = uuid.uuid4().hex[:6]
        est["abiertas"][sid] = {k: x[k] for k in ("sim","dir","sl","tp1","tp2")}
        enviar(plantilla(x) + f"\nID: #{sid}")
    except Exception as e:
        print("score", s, e)

json.dump(est, open("estado.json", "w"))
