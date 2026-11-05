#!/usr/bin/env python3
"""
datacheck.py – daglig kontroll av att alla aktier "svarar" (SPEC.md § 16, 2026-11-05).

Körs i GitHub Actions varje morgon före skanningen (datacheck.yml). Hämtar de senaste veckorna för hela
universumet med samma funktioner som skannern och kontrollerar varje aktie:

  tyst        senaste stängning äldre än 2 handelsdagar jämfört med aktiens egen marknad
  kurs        dagsrörelse över +100 % / under −50 %, nollvolym 5 dagar i rad, stillastående 10 dagar, dubblettdatum
  källa       EODHD mot Yahoo avviker > 20 % (stickprov)
  borta       fanns i universumet i går men inte i dag (listkällan tappade den)
  listkälla   en marknad gav färre än 90 % av gårdagens antal aktier

Resultat: datacheck.json (läses av appen – spärren "FÖRSLAG EJ VALIDERAT") och datacheck.md (läsbar sammanfattning).
Avslutar med kod 1 (rött jobb → GitHub mejlar ägaren) när något av detta gäller:
  mer än 2 % av universumet är tyst · en hel marknad saknar färska kurser · ett eget innehav (holdings.txt, WATCH) är flaggat.
"""
import json, os, sys, re, random, datetime as dt
import numpy as np, pandas as pd

sys.path.insert(0, ".")
import scanner as SC

OUT_JSON, OUT_MD = "datacheck.json", "datacheck.md"
NORDIC = (".ST", ".CO", ".OL", ".HE")

def market_of(t):
    if any(t.endswith(x) for x in NORDIC): return "Norden"
    return "Europa" if "." in t else "USA"

def load_prev():
    try: return json.load(open(OUT_JSON, encoding="utf-8"))
    except Exception: return None

def universe():
    """Samma listor som skannern och backtestet. Returnerar (tickers, {marknadsnyckel: antal})."""
    tick, per_src = [], {}
    for m in SC.MARKETS:
        k = m["k"]
        try: v = SC.get_tickers(m)          # samma normalisering som skannern (ASSA.ST → ASSA-B.ST osv.)
        except Exception as ex: print(f"  VARNING: {k} kunde inte hämtas ({str(ex)[:60]})"); v = []
        per_src[k] = len(v); tick += v
    m = SC.collapse_share_classes(sorted(set(tick)))
    tick = [m.get(t, t) for t in tick]
    return sorted(set(tick)), per_src

def own_holdings():
    own = set()
    try:
        for line in open("holdings.txt", encoding="utf-8"):
            t = line.split("#")[0].strip()
            if t: own.add(t)
    except Exception: pass
    try:
        j = json.load(open("markets_extra.json", encoding="utf-8"))
        for m in j.get("markets", []): own |= set(m.get("tickers", []))
    except Exception: pass
    return own

def bdays_between(a, b):
    """Antal vardagar från a (exkl.) till b (inkl.)."""
    if b <= a: return 0
    return int(np.busday_count(a.date(), b.date()))

def main():
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    prev = load_prev()
    tick, per_src = universe()
    own = own_holdings()
    print(f"Universum: {len(tick)} aktier, {len(per_src)} listor. Egna innehav/bevakning: {len(own)}")

    # --- listkällor mot i går ---
    src_warn = []
    if prev and prev.get("sources"):
        for k, n in per_src.items():
            p = prev["sources"].get(k)
            if p and n < 0.9 * p: src_warn.append({"k": k, "idag": n, "igar": p})
    gone = []
    if prev and prev.get("universe"):
        gone = sorted(set(prev["universe"]) - set(tick))

    # --- kurser: senaste ~6 veckor ---
    print("Hämtar kurser (2 månader)…", flush=True)
    data = SC.dl(tick, period="2mo", interval="1d")
    issues = {}      # ticker -> [problem]
    last = {}
    def flag(t, msg): issues.setdefault(t, []).append(msg)
    for t in tick:
        d = data.get(t)
        if d is None or len(d) == 0:
            flag(t, "ingen kursdata"); continue
        d = d.dropna(subset=["Close"])
        if d.empty: flag(t, "ingen kursdata"); continue
        idx = pd.DatetimeIndex(d.index).tz_localize(None) if getattr(d.index, "tz", None) is not None else pd.DatetimeIndex(d.index)
        if idx.has_duplicates: flag(t, "dubblettdatum")
        last[t] = idx.max().normalize()
        c = d["Close"].astype(float).values; v = d["Volume"].astype(float).values if "Volume" in d else None
        r = c[1:] / c[:-1] - 1 if len(c) > 1 else np.array([])
        if len(r) and (r.max() > 1.0 or r.min() < -0.5):
            i = int(r.argmax() if r.max() > 1.0 else r.argmin())
            flag(t, f"dagsrörelse {r[i]*100:+.0f} % den {idx[i+1].date()}")
        if v is not None and len(v) >= 5 and (v[-5:] == 0).all(): flag(t, "nollvolym 5 dagar")
        if len(c) >= 10 and np.all(c[-10:] == c[-1]): flag(t, "stillastående 10 dagar")
    # tyst: jämfört med den egna marknadens färskaste dag (90:e percentilen så att en enstaka felaktig framtida rad inte styr)
    mkt_last = {}
    for mk in ("Norden", "USA", "Europa"):
        ds = [last[t] for t in last if market_of(t) == mk]
        if ds: mkt_last[mk] = pd.Timestamp(np.quantile(np.array(sorted(ds), dtype="datetime64[ns]").astype("int64"), 0.9).astype("datetime64[ns]")).normalize()
    silent = []
    for t in tick:
        ml = mkt_last.get(market_of(t))
        if t in last and ml is not None and bdays_between(last[t], ml) > 2:
            flag(t, f"tyst sedan {last[t].date()}"); silent.append(t)
    mkt_stale = {mk: d.strftime("%Y-%m-%d") for mk, d in mkt_last.items() if bdays_between(d, today) > 3}

    # --- stickprov EODHD mot Yahoo ---
    xcheck = []
    if SC.EODHD_KEY:
        rng = random.Random(today.strftime("%Y%m%d"))
        sample = rng.sample([t for t in tick if t in last], min(60, len(last)))
        try:
            import yfinance as yf
            y = yf.download(sample, period="5d", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="ticker")
            for t in sample:
                try:
                    yc = y[t]["Close"].dropna() if isinstance(y.columns, pd.MultiIndex) else y["Close"].dropna()
                    ec = data[t]["Close"].dropna()
                    if yc.empty or ec.empty: continue
                    ratio = float(ec.iloc[-1]) / float(yc.iloc[-1])
                    if ratio > 1.2 or ratio < 0.8:
                        xcheck.append({"t": t, "eodhd": round(float(ec.iloc[-1]), 2), "yahoo": round(float(yc.iloc[-1]), 2)})
                        flag(t, f"EODHD {ec.iloc[-1]:.2f} mot Yahoo {yc.iloc[-1]:.2f}")
                except Exception: pass
        except Exception as ex:
            print(f"  Yahoo-stickprov misslyckades: {str(ex)[:60]}")

    # --- bedömning ---
    n = len(tick); n_silent = len(silent)
    own_flagged = sorted(t for t in own if t in issues or t in gone)
    red = []
    if n and n_silent / n > 0.02: red.append(f"{n_silent} av {n} aktier ({n_silent/n*100:.1f} %) är tysta")
    if mkt_stale: red.append("hel marknad utan färska kurser: " + ", ".join(f"{k} (senast {v})" for k, v in mkt_stale.items()))
    if own_flagged: red.append("egna innehav flaggade: " + ", ".join(own_flagged))
    if src_warn: red.append("listkälla gav färre aktier än i går: " + ", ".join(f"{w['k']} {w['idag']}/{w['igar']}" for w in src_warn))
    status = "red" if red else ("warn" if issues or gone else "ok")

    per_mkt = {}
    for mk in ("Norden", "USA", "Europa"):
        ts = [t for t in tick if market_of(t) == mk]
        per_mkt[mk] = {"antal": len(ts), "senaste": mkt_last.get(mk).strftime("%Y-%m-%d") if mkt_last.get(mk) else None,
                       "tysta": sum(1 for t in ts if t in silent), "flaggade": sum(1 for t in ts if t in issues)}
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status, "red": red,
           "markets": per_mkt, "n": n, "silent": n_silent, "flagged": len(issues), "gone": gone,
           "own_flagged": own_flagged, "issues": {t: v for t, v in sorted(issues.items())}, "xcheck": xcheck,
           "sources": per_src, "universe": tick}
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    L = [f"# Datakontroll {out['updated']} – {status.upper()}", ""]
    for r in red: L.append(f"- **RÖTT:** {r}")
    L.append(""); L.append("| Marknad | Aktier | Senaste stängning | Tysta | Flaggade |"); L.append("|---|---|---|---|---|")
    for mk, m in per_mkt.items(): L.append(f"| {mk} | {m['antal']} | {m['senaste']} | {m['tysta']} | {m['flaggade']} |")
    if gone: L += ["", f"**Borta sedan i går ({len(gone)}):** " + ", ".join(gone[:60]) + (" …" if len(gone) > 60 else "")]
    if issues:
        L += ["", f"**Flaggade aktier ({len(issues)}):**"]
        for t, v in sorted(issues.items())[:150]: L.append(f"- {t}: " + "; ".join(v))
        if len(issues) > 150: L.append(f"- … och {len(issues)-150} till (se datacheck.json)")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:40]))
    print(f"\nSkrev {OUT_JSON} och {OUT_MD}. Status: {status}")
    sys.exit(1 if status == "red" else 0)

if __name__ == "__main__":
    main()
