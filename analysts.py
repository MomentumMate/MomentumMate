#!/usr/bin/env python3
"""
analysts.py – riktkurser och analytikerrevideringar (SPEC § 12: information som visas men inte styr).

2026-11-16: urval ca 60–120 bolag dagligen (innehav, bevakning, topp 30, daghandelskandidater).
2026-11-23 (3.0): HELA universumet täcks. Yahoo stryper om alla ~2 300 hämtas samma dag, så urvalet delas i två:
  1. Prioriterade – innehav (holdings.txt, live_watch.json), bevakning (markets_extra.json), topp 30 i 2.0, daghandelskandidater:
     hämtas varje dag som förut.
  2. Resten av universumet – roterar: de bolag vars uppgifter är äldst hämtas först, ROT_N per körning (tidsgräns ROT_MIN minuter).
     Med 600 per dag täcks hela universumet ungefär var fjärde handelsdag (första körningen 2026-11-23: 461 bolag på 4 min, inga fel). Varje rad har "asof" = hämtdatum.
  Bolag som inte hämtas i dag behåller sina senaste uppgifter.

Per bolag: rapportdatum (nästa + åtta tidigare, 2026-11-23), konsensusriktkurs (snitt/hög/låg/median), antal analytiker, snittrekommendation, uppsida mot senaste kurs,
upp-/nedgraderingar med datum och analyshus senaste 45 dagarna – nu även varje analyshus tidigare och nya riktkurs ("ppt" → "pt").
Ur dem räknas "pt30": hur många analyshus som höjt respektive sänkt sin riktkurs senaste 30 dagarna och medianändringen i procent.
Det ger en riktig 30-dagarsrevidering från första dagen, för alla bolag, utan att vänta på egen historik.

Utfil: analysts.json (appen läser den). Konsensushistoriken per bolag (för "riktkurs mot 30 dagar sedan" och test 2027)
ligger i analysts_hist.json så att appens fil hålls liten.
"""
import json, os, sys, time, statistics, datetime as dt
import yfinance as yf

OUT = "analysts.json"
HIST = "analysts_hist.json"
TODAY = dt.date.today().isoformat()
ROT_N = int(os.environ.get("ANALYSTS_ROT_N", "600"))       # roterande bolag per körning
ROT_MIN = float(os.environ.get("ANALYSTS_ROT_MIN", "30"))  # tidsgräns för hela körningen, minuter (rotationen stannar där)
KEEP_DAYS = 45                                             # graderingar som sparas
KEEP_CH = 12                                               # högst så många graderingar per bolag

def log(*a): print(*a, flush=True)
def jload(p, default):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return default

def priority(d, S):
    sel = []
    try: sel += [l.split("#")[0].strip() for l in open("holdings.txt", encoding="utf-8") if l.split("#")[0].strip()]
    except Exception: pass
    try: sel += [t for m in jload("markets_extra.json", {}).get("markets", []) for t in m.get("tickers", [])]
    except Exception: pass
    try: sel += [t for t in jload("live_watch.json", {}).get("tickers", []) if isinstance(t, str)]
    except Exception: pass
    sel += [t for t, _ in sorted(((t, x.get("sc2") or 0) for t, x in S.items() if (x.get("sc2") or 0) > 0 and x.get("liqok", True)), key=lambda z: -z[1])[:30]]
    rk = (d.get("rank") or {}).get("DT") or {}
    sel += list(rk.get("cand") or [])
    return [t for t in dict.fromkeys(sel) if t in S]

def num(v):
    try:
        v = float(v)
        return round(v, 2) if v == v and v > 0 else None
    except Exception:
        return None

def fetch(t):
    tk = yf.Ticker(t)
    out = {}
    try:
        inf = tk.info or {}
        for k, kk in (("targetMeanPrice", "tgt"), ("targetHighPrice", "hi"), ("targetLowPrice", "lo"), ("targetMedianPrice", "med"),
                      ("numberOfAnalystOpinions", "n"), ("recommendationMean", "rec")):
            v = inf.get(k)
            if isinstance(v, (int, float)): out[kk] = round(float(v), 2)
        if inf.get("recommendationKey"): out["key"] = inf["recommendationKey"]
    except Exception as e:
        out["err"] = f"info: {str(e)[:50]}"
    # rapportdatum (2026-11-23): nästa och de senaste åtta – skannern räknar kursreaktionen ur sin kurshistorik
    try:
        ed = tk.get_earnings_dates(limit=12)
        if ed is not None and len(ed):
            ds = sorted({str(x)[:10] for x in ed.index})
            fut = [x for x in ds if x >= TODAY]; past = [x for x in ds if x < TODAY]
            if fut: out["ed"] = fut[0]
            if past: out["eds"] = past[-8:]
    except Exception as e:
        out["err3"] = f"earnings: {str(e)[:40]}"
    try:
        ud = tk.upgrades_downgrades
        ch = []
        if ud is not None and len(ud):
            ud = ud.reset_index()
            since = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
            for _, r in ud.iterrows():
                dte = str(r.get("GradeDate", ""))[:10]
                if dte < since: continue
                c = {"date": dte, "firm": str(r.get("Firm", "")), "to": str(r.get("ToGrade", "")), "from": str(r.get("FromGrade", "")),
                     "action": str(r.get("Action", "")).lower()}
                pt, ppt = num(r.get("currentPriceTarget")), num(r.get("priorPriceTarget"))
                if pt: c["pt"] = pt
                if ppt: c["ppt"] = ppt
                pta = str(r.get("priceTargetAction") or "").strip()
                if pta and pta.lower() != "nan": c["pta"] = pta
                ch.append(c)
        ch.sort(key=lambda x: x["date"], reverse=True)
        out["changes"] = ch[:KEEP_CH]
        # riktkursrevideringar per analyshus senaste 30 dagarna (ur hela listan, inte bara de sparade)
        since30 = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        rev = [c["pt"] / c["ppt"] - 1 for c in ch if c["date"] >= since30 and c.get("pt") and c.get("ppt")]
        if rev:
            out["pt30"] = {"up": sum(1 for x in rev if x > 0.001), "dn": sum(1 for x in rev if x < -0.001), "n": len(rev),
                           "med": round(statistics.median(rev) * 100, 1)}
    except Exception as e:
        out["err2"] = f"grades: {str(e)[:50]}"
    return out

def main():
    d = jload("data.json", {}); S = d.get("stocks") or {}
    prio = priority(d, S)
    prev = jload(OUT, {})
    pstocks = prev.get("stocks") or {}
    hist = jload(HIST, None)
    if hist is None: hist = prev.get("history") or {}          # flytt av historiken från analysts.json (2026-11-23)
    # roterande del: hela universumet utom de prioriterade, äldst hämtade först (aldrig hämtade allra först)
    rest = [t for t, x in S.items() if t not in set(prio) and x.get("liqok", True)]
    rest.sort(key=lambda t: ((pstocks.get(t) or {}).get("asof") or "0000", t))
    rot = rest[:ROT_N]
    log(f"Analytiker: {len(prio)} prioriterade + {len(rot)} roterande av {len(rest)} (universum {len(S)})")
    stocks = {t: a for t, a in pstocks.items() if t in S}       # behåll senaste uppgifter för dem som inte hämtas i dag
    for t, a in stocks.items(): a.setdefault("asof", (prev.get("updated") or TODAY)[:10])
    ok = 0; n_done = 0; t0 = time.time()
    for k, t in enumerate(prio + rot):
        if k >= len(prio) and time.time() - t0 > ROT_MIN * 60:
            log(f"Tidsgräns: stannar efter {n_done} bolag"); break
        try:
            a = fetch(t)
            px = (S.get(t) or {}).get("px")
            if a.get("tgt") and px: a["upside"] = round((a["tgt"] / px - 1) * 100, 1)
            a["asof"] = TODAY
            if a.get("tgt") or a.get("changes"): ok += 1
            if a.get("err") and not a.get("tgt") and t in pstocks and pstocks[t].get("tgt"):
                a = dict(pstocks[t]); a["stale"] = True             # Yahoo-fel: behåll gårdagens uppgifter, märkta
            stocks[t] = a
            if a.get("tgt") and not a.get("stale"):
                h = hist.setdefault(t, [])
                if not h or h[-1][0] != TODAY: h.append([TODAY, a["tgt"], a.get("n"), a.get("rec")])
                else: h[-1] = [TODAY, a["tgt"], a.get("n"), a.get("rec")]
                if len(h) > 400: del h[:len(h) - 400]
        except Exception as e:
            stocks[t] = {"err": str(e)[:60], "asof": TODAY}
        n_done += 1
        if k % 10 == 9: time.sleep(1.5)       # varsamt mot Yahoo
    # revidering ur egen historik: riktkurs i dag mot äldsta mätning inom 30 dagar
    since = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    for t, a in stocks.items():
        h = hist.get(t) or []
        if len(h) >= 2 and a.get("tgt"):
            old = next((x for x in h if x[0] >= since), h[0])
            if old and old[1]: a["tgt30"] = old[1]; a["tgtchg"] = round((a["tgt"] / old[1] - 1) * 100, 1); a["tgt30d"] = old[0]
    cover = sum(1 for a in stocks.values() if a.get("tgt"))
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "n": len(stocks), "ok": ok, "today": n_done,
           "prio": len(prio), "universe": len(S), "covered": cover, "stocks": stocks}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(hist, open(HIST, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"Skrev {OUT}: {n_done} hämtade i dag ({ok} med data), {len(stocks)} bolag totalt, {cover} med riktkurs; historik {len(hist)} bolag")

if __name__ == "__main__":
    main()
