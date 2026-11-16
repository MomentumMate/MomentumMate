#!/usr/bin/env python3
"""
analysts.py – riktkurser och analytikerrevideringar (SPEC § 12: information som visas men inte styr; arbetslista 6, 2026-11-16).

Hämtar från Yahoo (fungerar från GitHub Actions men stryper vid hela universumet) för ett urval: egna innehav (holdings.txt),
bevakning (markets_extra.json), topp 20 i 2.0, daghandelskandidater – ca 60–100 bolag dagligen:
  konsensusriktkurs (snitt/hög/låg), antal analytiker, snittrekommendation, uppsida mot senaste kurs,
  upp-/nedgraderingar med datum och analyshus (senaste 60 dagarna).
Skriver analysts.json. Varje dags konsensus sparas i en historik per aktie så att revideringar (riktkurs upp/ner, fler/färre köp)
kan följas – och så att "revideringar som avgörare" kan testas om ett år (TESTPLAN). Nivån på riktkursen är svag information;
förändringen är det som har dokumenterat värde.
"""
import json, sys, time, datetime as dt
import yfinance as yf

OUT = "analysts.json"
TODAY = dt.date.today().isoformat()

def log(*a): print(*a, flush=True)
def jload(p, default):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return default

def targets():
    d = jload("data.json", {}); S = d.get("stocks") or {}
    sel = []
    try: sel += [l.split("#")[0].strip() for l in open("holdings.txt", encoding="utf-8") if l.split("#")[0].strip()]
    except Exception: pass
    try: sel += [t for m in jload("markets_extra.json", {}).get("markets", []) for t in m.get("tickers", [])]
    except Exception: pass
    sel += [t for t, _ in sorted(((t, x.get("sc2") or 0) for t, x in S.items() if (x.get("sc2") or 0) > 0 and x.get("liqok", True)), key=lambda z: -z[1])[:30]]
    rk = (d.get("rank") or {}).get("DT") or {}
    sel += list(rk.get("cand") or [])
    return [t for t in dict.fromkeys(sel) if t in S], S

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
    try:
        ud = tk.upgrades_downgrades
        ch = []
        if ud is not None and len(ud):
            ud = ud.reset_index()
            since = (dt.date.today() - dt.timedelta(days=60)).isoformat()
            for _, r in ud.iterrows():
                dte = str(r.get("GradeDate", ""))[:10]
                if dte < since: continue
                ch.append({"date": dte, "firm": str(r.get("Firm", "")), "to": str(r.get("ToGrade", "")), "from": str(r.get("FromGrade", "")),
                           "action": str(r.get("Action", "")).lower()})
        ch.sort(key=lambda x: x["date"], reverse=True)
        out["changes"] = ch[:30]
    except Exception as e:
        out["err2"] = f"grades: {str(e)[:50]}"
    return out

def main():
    sel, S = targets()
    prev = jload(OUT, {})
    hist = prev.get("history") or {}
    stocks = {}; ok = 0
    log(f"Analytiker: {len(sel)} bolag")
    for i, t in enumerate(sel):
        try:
            a = fetch(t)
            px = (S.get(t) or {}).get("px")
            if a.get("tgt") and px: a["upside"] = round((a["tgt"] / px - 1) * 100, 1)
            if a.get("tgt") or a.get("changes"): ok += 1
            stocks[t] = a
            if a.get("tgt"):
                h = hist.setdefault(t, [])
                if not h or h[-1][0] != TODAY: h.append([TODAY, a["tgt"], a.get("n"), a.get("rec")])
                else: h[-1] = [TODAY, a["tgt"], a.get("n"), a.get("rec")]
                if len(h) > 400: del h[:len(h) - 400]
        except Exception as e:
            stocks[t] = {"err": str(e)[:60]}
        if i % 10 == 9: time.sleep(1.5)       # varsamt mot Yahoo
    # revideringar ur historiken: riktkurs i dag mot 30 dagar sedan
    for t, a in stocks.items():
        h = hist.get(t) or []
        if len(h) >= 2 and a.get("tgt"):
            since = (dt.date.today() - dt.timedelta(days=30)).isoformat()
            old = next((x for x in h if x[0] >= since), h[0])
            if old and old[1]: a["tgt30"] = old[1]; a["tgtchg"] = round((a["tgt"] / old[1] - 1) * 100, 1); a["tgt30d"] = old[0]
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "n": len(sel), "ok": ok, "stocks": stocks, "history": hist}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"Skrev {OUT}: {ok} av {len(sel)} med riktkurs eller graderingar")

if __name__ == "__main__":
    main()
