"""Daglig aktiescanner. Kör: python scanner.py  (cron / GitHub Actions kl 14:00 svensk tid, före USA-öppning)
pip install yfinance pandas requests lxml
Valfritt: export BENZINGA_KEY=...  (Benzinga news API)"""
import json, os, datetime as dt
import pandas as pd, requests, yfinance as yf

OMXS = ["VOLV-B.ST","ERIC-B.ST","HM-B.ST","ABB.ST","ATCO-A.ST","SEB-A.ST","SWED-A.ST","INVE-B.ST","SAND.ST","ASSA-B.ST",
        "EVO.ST","SINCH.ST","NIBE-B.ST","HEXA-B.ST","ALFA.ST","ESSITY-B.ST","BOL.ST","SKA-B.ST","TELIA.ST","SHB-A.ST"]  # utöka
def sp500():
    h = {"User-Agent": "Mozilla/5.0"}
    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=h).text
    return [s.replace(".", "-") for s in pd.read_html(html)[0]["Symbol"]]

def rsi(c, n=14):
    d = c.diff(); u = d.clip(lower=0).rolling(n).mean(); l = (-d.clip(upper=0)).rolling(n).mean()
    return float(100 - 100 / (1 + u.iloc[-1] / l.iloc[-1]))

def news(t):
    pos = ("beat","upgrade","raises","surge","record","growth","strong","buy")
    neg = ("miss","downgrade","cuts","falls","probe","lawsuit","weak","sell")
    items = []
    try:
        if os.getenv("BENZINGA_KEY") and not t.endswith(".ST"):
            r = requests.get("https://api.benzinga.com/api/v2/news", params={"token": os.environ["BENZINGA_KEY"], "tickers": t, "pageSize": 3},
                             headers={"accept": "application/json"}, timeout=10).json()
            items = [(n["title"], "Benzinga") for n in r]
        if not items:
            items = [((n.get("content") or n).get("title", ""), "Yahoo Finance") for n in yf.Ticker(t).news[:3]]
    except Exception: pass
    s = sum(any(w in h.lower() for w in pos) - any(w in h.lower() for w in neg) for h, _ in items)
    return [{"h": h, "src": s_} for h, s_ in items], max(-1, min(1, s / 3))

def fib_levels(c, lookback=126):
    seg = c.iloc[-lookback:]
    hi, lo = float(seg.max()), float(seg.min())
    rng = hi - lo or 1
    return {r: hi - rng * r for r in (0.236, 0.382, 0.5, 0.618, 0.786)}

def graham(info):
    pe, pb = info.get("trailingPE"), info.get("priceToBook")
    de = info.get("debtToEquity")
    eg = (info.get("earningsGrowth") or 0) * 100
    if not pe or not pb or pe <= 0 or pb <= 0: return False
    return pe < 15 and pb < 1.5 and pe * pb < 22.5 and (de is None or de < 100) and eg > 0

def magic_formula(info):
    ev, ebitda = info.get("enterpriseValue"), info.get("ebitda")
    roa = info.get("returnOnAssets")
    if not ev or not ebitda or ev <= 0 or roa is None: return False, None, None
    ey = ebitda / ev * 100           # earnings yield-proxy
    roc = roa * 100                  # return on capital-proxy
    return (ey > 8 and roc > 12), round(ey, 1), round(roc, 1)

def analyse(t):
    d = yf.Ticker(t).history(period="1y", interval="1d")
    if len(d) < 200: return None
    c, v = d["Close"], d["Volume"]
    y, p = d.iloc[-2], d.iloc[-1]                       # gårdag / senaste
    pm = yf.Ticker(t).history(period="1d", interval="1m", prepost=True)
    pre = pm[pm.index.time < dt.time(9, 30)] if not t.endswith(".ST") and len(pm) else pm.iloc[0:0]
    pre_hi = float(pre["High"].max()) if len(pre) else float(p["Open"])
    pre_vol = int(pre["Volume"].sum()) if len(pre) else 0
    px = float(p["Close"]); chg = (px / float(y["Close"]) - 1) * 100
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    ema12, ema26 = c.ewm(span=12).mean(), c.ewm(span=26).mean()
    r1, r3 = (px / float(c.iloc[-21]) - 1) * 100, (px / float(c.iloc[-63]) - 1) * 100
    volx = float(v.iloc[-1] / v.rolling(20).mean().iloc[-1])
    info = yf.Ticker(t).info

    # --- Tekniska larm ---
    alerts = []
    sma50_y, sma50_t = float(sma50.iloc[-2]), float(sma50.iloc[-1])
    if y["Close"] < sma50_y <= px: alerts.append({"k": "sma50", "t": "Bröt upp genom SMA50"})
    elif y["Close"] > sma50_y >= px: alerts.append({"k": "sma50", "t": "Bröt ner genom SMA50"})
    rs = rsi(c)
    if rs < 30: alerts.append({"k": "rsi", "t": f"RSI översåld ({rs:.0f})"})
    elif rs > 70: alerts.append({"k": "rsi", "t": f"RSI överköpt ({rs:.0f})"})
    fib = fib_levels(c)
    for r, lvl in fib.items():
        if lvl and abs(px / lvl - 1) <= 0.012:
            alerts.append({"k": "fib", "t": f"Pris vid Fibonacci {int(r*100)}%"}); break
    if graham(info): alerts.append({"k": "graham", "t": "Graham Screener-kandidat"})
    mf, mf_ey, mf_roc = magic_formula(info)
    if mf: alerts.append({"k": "magic", "t": "Magic Formula-kandidat"})
    eg, rg, pm_ = (info.get("earningsGrowth") or 0) * 100, (info.get("revenueGrowth") or 0) * 100, (info.get("profitMargins") or 0) * 100
    nws, sent = news(t)
    # --- Poäng 0-100: teknik 60 %, fundamentals 25 %, nyheter 15 % ---
    tech = (min(max(r1, 0), 30) / 30 * 25 + min(max(r3, 0), 60) / 60 * 25 + (px > sma50.iloc[-1]) * 15 + (px > sma200.iloc[-1]) * 15
            + (ema12.iloc[-1] > ema26.iloc[-1]) * 10 + min(volx, 3) / 3 * 10)
    fund = min(max(eg, 0), 50) / 50 * 40 + min(max(rg, 0), 30) / 30 * 40 + min(max(pm_, 0), 25) / 25 * 20
    score = round(tech * .6 + fund * .25 + (sent + 1) / 2 * 15, 1)
    hit = chg >= 5 or (px - float(y["Close"])) >= 3 or pre_vol >= 50_000          # scannerns urval
    dtc = [px > float(y["High"]), float(y["Close"]) > float(sma200.iloc[-2]), px > pre_hi, px > float(p["Open"]), pre_vol >= 50_000]
    return dict(t=t.replace(".ST", ""), name=info.get("shortName", t), px=round(px, 2), chg=round(chg, 2), score=score, rsi=round(rsi(c), 0),
                r1m=round(r1, 1), r3m=round(r3, 1), volx=round(volx, 1), above200=bool(px > sma200.iloc[-1]), eg=round(eg, 0), rg=round(rg, 0),
                pe=info.get("trailingPE"), news=nws, sent=sent, hit=bool(hit), dt=[bool(x) for x in dtc], alerts=alerts)

def run(tickers):
    out = []
    for t in tickers:
        try:
            r = analyse(t); r and out.append(r)
        except Exception as e: print("skip", t, e)
    pri = [r for r in out if r["hit"]] or out
    return sorted(pri, key=lambda r: r["score"], reverse=True)[:25]

if __name__ == "__main__":
    data = {"updated": dt.datetime.now().isoformat(timespec="minutes"), "OMXS": run(OMXS), "SP500": run(sp500())}
    json.dump(data, open("data.json", "w"), ensure_ascii=False)
    print("Klart:", {k: len(v) for k, v in data.items() if isinstance(v, list)})
