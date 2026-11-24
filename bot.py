import json, os, time, uuid, requests, pandas as pd

TOKEN = os.environ["BOT_TOKEN"]
CHAT = str(os.environ["CHAT_ID"])
ADMIN = str(os.environ["ADMIN_ID"])
SIMBOLOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CONF_MIN, RIESGO_ATR, COOLDOWN = 70, 1.5, 14400
TG = f"https://api.telegram.org/bot{TOKEN}"
est = json.load(open("estado.json"))
fin = time.time() + 200

def tg(metodo, **kw):
    try: return requests.post(f"{TG}/{metodo}", data=kw, timeout=15).json()
    except Exception as e: print("tg", e); return {}

def enviar(txt, chat=CHAT):
    return tg("sendMessage", chat_id=chat, text=txt, parse_mode="HTML",
              disable_web_page_preview="true")

def velas(sim, tf="15", lim=250):
    for f in ("bybit", "okx"):
        try:
            if f == "bybit":
                r = requests.get("https://api.bybit.com/v5/market/kline", timeout=12,
                    params={"category":"spot","symbol":sim,"interval":tf,"limit":lim})
                r.raise_for_status(); d = list(reversed(r.json()["result"]["list"]))
            else:
                r = requests.get("https://www.okx.com/api/v5/market/candles", timeout=12,
                    params={"instId":sim.replace("USDT","-USDT"),
                            "bar":"1H" if tf=="60" else tf,"limit":lim})
                r.raise_for_status(); d = [[x[0],x[1],x[2],x[3],x[4],x[5],0] for x in reversed(r.json()["data"])]
            df = pd.DataFrame(d, columns=["t","o","h","l","c","v","tv"])
            for c in "ohlcv": df[c] = df[c].astype(float)
            return df
        except Exception as e: print(f, e)
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

def score(sim):
    df, d1 = velas(sim), velas(sim, "60")
    if df is None or d1 is None or len(df) < 220: return None
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

f = lambda x: f"{x:,.4f}".rstrip("0").rstrip(".")

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
    for u in r:
        est["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        txt = (m.get("text") or "").strip()
        cid = str(m.get("chat", {}).get("id", ""))
        uid = str(m.get("from", {}).get("id", ""))
        # Blindaje: solo responde en tu grupo VIP o a ti como Admin
        if cid != CHAT and uid != ADMIN:
            continue
        if not txt or not cid: continue
        if txt.startswith("/stats"):
            h = est.get("hist", [])
            if not h: enviar("Aún sin señales cerradas.", cid)
            else:
                t1 = sum(1 for x in h if x["res"] in ("TP1","TP2"))
                enviar(f"Cerradas: {len(h)}\nTP: {t1}\nAcierto: {t1/len(h)*100:.1f}%", cid)
        elif txt.startswith("/salud"):
            enviar(f"✅ vivo\nabiertas: {len(est['abiertas'])}", cid)
        elif txt.startswith("/estado"):
            enviar("Usa /stats o /salud. Señales automáticas cada 5 min.", cid)

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
