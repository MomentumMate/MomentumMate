#!/usr/bin/env python3
"""
backtest.py – validerar den TEKNISKA kärnan i MomentumMate.

Vad som testas: momentumurval (12-1 volatilitetsjusterat), trendfilter, likviditetsfilter,
bandning, stopp och ombalansering – samt vad courtaget äter.

Vad som INTE testas: fundamenta (tillväxt, ROE, Graham, Magic Formula, Piotroski).
Yahoo levererar bara dagens nyckeltal, inte historiska, så de går inte att återskapa bakåt
utan att titta in i framtiden. Resultaten gäller alltså den tekniska delen av modellen.

Survivorship bias: tickerlistan är dagens indexmedlemmar. Bolag som gått under saknas,
vilket gör resultatet för optimistiskt. Ange en historisk lista med --universe för att minska det.

Körs i GitHub Actions:  python backtest.py --years 10 --out backtest.json
"""
import argparse, json, math, sys, datetime as dt
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- data
def load_prices(tickers, years, cache=None):
    if cache:
        try:
            px = pd.read_pickle(cache)
            print(f"Läste {px.shape[1]} aktier ur cache {cache}")
            return px
        except Exception:
            pass
    import yfinance as yf
    end = dt.date.today()
    start = end - dt.timedelta(days=int(years * 365.25) + 400)   # extra år för 12-1-fönstret
    print(f"Hämtar {len(tickers)} aktier {start} → {end} …")
    raw = yf.download(tickers, start=start, end=end, interval="1d",
                      auto_adjust=True, progress=False, threads=True, group_by="column")
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    vol = raw["Volume"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Volume"]]
    close = close.dropna(axis=1, how="all")
    vol = vol.reindex(columns=close.columns)
    if cache:
        try: close.to_pickle(cache)
        except Exception: pass
    print(f"  {close.shape[1]} aktier med data, {close.shape[0]} handelsdagar")
    return close, vol

# ----------------------------------------------------------------------------- modell
def score_panel(close, vol, cfg):
    """Poäng per aktie och dag, räknad BARA på data till och med dagen i fråga.

    Allt bygger på rullande fönster i pandas, som per definition inte ser framåt.
    """
    r = close.pct_change()
    MP = {"min_periods": None}
    # 12-1: avkastning från t-252 till t-21 (senaste månaden utelämnad)
    m121 = (close.shift(cfg["skip"]) / close.shift(cfg["look"]) - 1) * 100
    if cfg["voladj"]:
        ann = r.rolling(126, min_periods=90).std() * math.sqrt(252) * 100
        vam = m121 / ann.replace(0, np.nan)
        mom = ((vam + 0.30) / 2.30).clip(0, 1) * 100
    else:
        mom = (m121 / 60).clip(0, 1) * 100
    s50 = close.rolling(50, min_periods=40).mean()
    s200 = close.rolling(200, min_periods=160).mean()
    trend = ((close > s50).astype(float) * 30 + (close > s200).astype(float) * 30
             + (s50 > s200).astype(float) * 20 + (s200 > s200.shift(1)).astype(float) * 20)
    sc = mom * 0.65 + trend * 0.35
    if cfg["trendfilter"]:
        sc = sc.where(close > s200)                      # bara aktier över sitt långa medelvärde
    if cfg["minturnover"] > 0 and vol is not None:
        turn = (close * vol).rolling(20, min_periods=12).mean()
        sc = sc.where(turn >= cfg["minturnover"])
    return sc, m121

def period_ends(idx, freq="M"):
    """Sista handelsdagen i varje månad (M) eller kvartal (Q)."""
    s = pd.Series(idx, index=idx)
    key = [idx.year, idx.month] if freq == "M" else [idx.year, idx.quarter]
    return list(s.groupby(key).last())

def run(close, vol, cfg):
    """Simulerar strategin. Returnerar kapitalkurva och nyckeltal."""
    sc, _ = score_panel(close, vol, cfg)
    # Olika börser har olika helgdagar, så panelen har hål. Utan detta försvinner innehav
    # ur värderingen de dagarna och kapitalkurvan ser ut att kollapsa.
    alive = close.notna()
    pxf = close.ffill()
    last_day = {t: (alive[t][alive[t]].index[-1] if alive[t].any() else None) for t in close.columns}
    dates = close.index
    rebs = set(period_ends(dates, cfg.get("reb", "M")))
    start_i = max(cfg["look"] + 5, 210)
    if start_i >= len(dates) - 5:
        raise SystemExit("För kort historik för att backtesta.")

    cash, hold = 1.0, {}          # hold: ticker -> {"sh": andel av kapitalet, "px": inköpskurs, "peak": högsta}
    eq, eqdates, trades = [], [], []
    cost = cfg["cost_bps"] / 10000.0

    def value(i):
        p = pxf.iloc[i]
        v = cash
        for t, h in hold.items():
            q = p.get(t)
            v += h["sh"] * (q if q == q else h["px"])      # saknad kurs: använd senast kända
        return v

    for i in range(start_i, len(dates)):
        d, p = dates[i], pxf.iloc[i]
        # avnoterade bolag säljs till sista kända kurs i stället för att tyst försvinna
        for t in list(hold):
            ld = last_day.get(t)
            if ld is not None and d > ld:
                q = p.get(t)
                cash += hold[t]["sh"] * (q if q == q else hold[t]["px"]) * (1 - cost)
                trades.append(("delisted", t, d)); del hold[t]
        # --- dagliga stopp (gäller mellan ombalanseringarna) ---
        for t in list(hold):
            px = p.get(t)
            if px != px:   # NaN
                continue
            h = hold[t]
            h["peak"] = max(h["peak"], px)
            base = h["peak"] if cfg["trail"] else h["px"]
            if cfg["stop"] > 0 and px <= base * (1 - cfg["stop"] / 100):
                cash += h["sh"] * px * (1 - cost); trades.append(("stop", t, d)); del hold[t]

        # --- ombalansering ---
        if d in rebs:
            row = sc.iloc[i].dropna()
            if len(row) >= cfg["n"]:
                rank = row.rank(ascending=False, method="first")
                top = set(rank[rank <= cfg["n"]].index)
                keep = set(t for t in hold if rank.get(t, 1e9) <= cfg["band_out"])
                # sälj allt som ramlat ur bandet
                for t in list(hold):
                    if t not in keep:
                        px = p.get(t)
                        if px == px:
                            cash += hold[t]["sh"] * px * (1 - cost); trades.append(("band", t, d))
                        del hold[t]
                # återställ vikterna till lika stora (trimma vinnare, fyll på eftersläntrare)
                if cfg.get("reweight") and hold:
                    tot = value(i); target = tot / cfg["n"]
                    for t, h in list(hold.items()):
                        px = p.get(t)
                        if px != px or px <= 0: continue
                        cur = h["sh"] * px
                        if cur > target * (1 + cfg.get("rw_band", 0.25)):
                            sell = (cur - target) / px
                            h["sh"] -= sell; cash += sell * px * (1 - cost); trades.append(("trim", t, d))
                        elif cur < target * (1 - cfg.get("rw_band", 0.25)) and cash > 0:
                            add = min(target - cur, cash)
                            h["sh"] += add * (1 - cost) / px; cash -= add; trades.append(("add", t, d))
                # fyll på upp till n med de högst rankade
                room = cfg["n"] - len(hold)
                if room > 0:
                    picks = [t for t in rank.sort_values().index if t not in hold and t in top][:room]
                    tot = value(i)
                    for t in picks:
                        px = p.get(t)
                        if px != px or px <= 0: continue
                        target = tot / cfg["n"]
                        if cash < target * 0.2: break
                        amt = min(target, cash)
                        sh = amt * (1 - cost) / px
                        cash -= amt
                        hold[t] = {"sh": sh, "px": px, "peak": px}; trades.append(("buy", t, d))
        eq.append(value(i)); eqdates.append(d)

    e = pd.Series(eq, index=pd.DatetimeIndex(eqdates)).dropna()
    return e, trades

# ----------------------------------------------------------------------------- mått
def stats(e, trades, years):
    if len(e) < 30: return {}
    r = e.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if not len(r) or e.iloc[0] <= 0: return {}
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1
    vol = r.std() * math.sqrt(252)
    dd = (e / e.cummax() - 1).min()
    buys = sum(1 for t in trades if t[0] == "buy")
    return {"slutkapital": round(float(e.iloc[-1]), 3),
            "CAGR_%": round(cagr * 100, 2),
            "volatilitet_%": round(float(vol) * 100, 1),
            "Sharpe": round(float(cagr / vol), 2) if vol > 0 else None,
            "max_nedgång_%": round(float(dd) * 100, 1),
            "affärer": len(trades), "köp": buys,
            "köp_per_år": round(buys / years, 1)}

def benchmark(close, years, start_i=210):
    sub = close.iloc[start_i:]
    r = sub.pct_change().mean(axis=1).fillna(0)       # likaviktat index av universumet
    e = (1 + r).cumprod()
    return e, stats(e, [], years)

# ----------------------------------------------------------------------------- main
DEF = dict(n=10, band_out=20, stop=12, trail=True, look=252, skip=21, voladj=True,
           trendfilter=True, minturnover=3e6, cost_bps=15, reb="M", reweight=False, rw_band=0.25)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=10)
    ap.add_argument("--universe", default=None, help="fil med en ticker per rad")
    ap.add_argument("--out", default="backtest.json")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--quick", action="store_true", help="kör bara grundfallet")
    ap.add_argument("--combo", action="store_true", help="testa kombinationer i stället för en ändring i taget")
    ap.add_argument("--robust", action="store_true", help="håller vinnaren på data den inte valts på?")
    a = ap.parse_args()

    if a.universe:
        tick = [x.split("#")[0].strip() for x in open(a.universe) if x.split("#")[0].strip()]
    else:
        sys.path.insert(0, ".")
        from scanner import PRESETS
        tick = []
        for k in ("SP500", "NASDAQ", "OMXS", "DAX", "CAC", "FTSE", "OMXC", "OSE"):
            v = PRESETS.get(k, {}).get("tickers")
            if callable(v):
                try: v = v()
                except Exception: v = []
            if isinstance(v, list): tick += v
        tick = sorted(set(tick))
    print(f"Universum: {len(tick)} aktier")

    close, vol = load_prices(tick, a.years, a.cache)
    years = (close.index[-1] - close.index[210]).days / 365.25

    bench_e, bench = benchmark(close, years)
    print("\n=== JÄMFÖRELSE: likaviktat index av universumet ===")
    for k, v in bench.items(): print(f"   {k:<18}{v}")

    # ---------- robusthetstest: delar perioden och universumet ----------
    if a.robust:
        BEST = dict(voladj=False, band_out=30, n=20, stop=12)
        CAND = {
            "A. Nuvarande grundfall": {},
            "B. Bästa enskilda kombinerat": dict(BEST),
            "E. B+kvartal+likavikt (stopp 12)": dict(BEST, reb="Q", reweight=True),
            "F. E men stopp 15": dict(BEST, reb="Q", reweight=True, stop=15),
            "G. E men stopp 20": dict(BEST, reb="Q", reweight=True, stop=20),
            "H. E utan stopp": dict(BEST, reb="Q", reweight=True, stop=0),
            "I. E men 15 innehav": dict(BEST, reb="Q", reweight=True, n=15, band_out=25),
            "L. E utan trendfilter": dict(BEST, reb="Q", reweight=True, trendfilter=False),
        }
        def group(cols):
            us, nordic, eur = [], [], []
            for t in cols:
                if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): nordic.append(t)
                elif "." in t: eur.append(t)
                else: us.append(t)
            return {"USA": us, "Norden": nordic, "Europa utom Norden": eur}
        grp = group(list(close.columns))
        idx = close.index
        mid = idx[len(idx) // 2]
        WARM = pd.Timedelta(days=430)
        segments = {
            "Hela perioden": (idx[0], idx[-1]),
            "Första halvan": (idx[0], mid),
            "Andra halvan": (mid - WARM, idx[-1]),
        }
        tests = {}
        for segn, (s0, s1) in segments.items():
            for un, cols in [("Alla marknader", list(close.columns))] + list(grp.items()):
                cols = [c for c in cols if c in close.columns]
                if len(cols) < 40: 
                    print(f"  hoppar över {segn} / {un}: bara {len(cols)} aktier"); continue
                sub = close.loc[s0:s1, cols]; subv = vol.loc[s0:s1, cols]
                if len(sub) < 500: continue
                yrs = (sub.index[-1] - sub.index[min(210, len(sub)-1)]).days / 365.25
                if yrs < 1.5: continue
                be, bst = benchmark(sub, yrs)
                row = {"_index": bst.get("CAGR_%"), "_indexSharpe": bst.get("Sharpe"), "_aktier": len(cols)}
                for cn, over in CAND.items():
                    cfg = dict(DEF); cfg.update(over)
                    try:
                        e, tr = run(sub, subv, cfg); st = stats(e, tr, yrs)
                        row[cn] = {"CAGR_%": st.get("CAGR_%"), "Sharpe": st.get("Sharpe"),
                                   "max_nedgång_%": st.get("max_nedgång_%")}
                    except Exception as ex:
                        row[cn] = {"fel": str(ex)[:60]}
                key = f"{segn} | {un}"
                tests[key] = row
                print(f"\n=== {key}  ({len(cols)} aktier, {yrs:.1f} år) ===")
                print(f"   {'index':<34}{row['_index']:>8} %   Sharpe {row['_indexSharpe']}")
                for cn in CAND:
                    v = row[cn]
                    if "fel" in v: print(f"   {cn:<34}  FEL {v['fel']}"); continue
                    print(f"   {cn:<34}{v['CAGR_%']:>8} %   Sharpe {v['Sharpe']:<6} nedgång {v['max_nedgång_%']} %")
        # hur stabil är rangordningen?
        print("\n=== STABILITET: placering per delprov (1 = bäst Sharpe) ===")
        ranks = {cn: [] for cn in CAND}
        for key, row in tests.items():
            ok = {cn: row[cn]["Sharpe"] for cn in CAND if isinstance(row.get(cn), dict) and row[cn].get("Sharpe") is not None}
            order = sorted(ok, key=lambda c: -ok[c])
            for i, cn in enumerate(order): ranks[cn].append(i + 1)
        summary = {}
        for cn, rs in ranks.items():
            if rs:
                summary[cn] = {"medelplacering": round(sum(rs) / len(rs), 2), "bästa": min(rs),
                               "sämsta": max(rs), "antal_delprov": len(rs)}
        for cn, v in sorted(summary.items(), key=lambda x: x[1]["medelplacering"]):
            print(f"   {cn:<34} medel {v['medelplacering']:<6} spann {v['bästa']}–{v['sämsta']}  ({v['antal_delprov']} prov)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "robusthet", "delprov": tests, "stabilitet": summary,
               "varning": "Survivorship bias och ingen fundamenta. Delproven överlappar delvis i tid."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    BEST = dict(voladj=False, band_out=30, n=20, stop=12)      # bäst enligt enskildtesterna
    if a.combo:
        variants = {
            "A. Nuvarande grundfall": {},
            "B. Bästa enskilda kombinerat (ingen vol-just, band 30, 20 st, stopp 12)": dict(BEST),
            "C. B + kvartalsvis ombalansering": dict(BEST, reb="Q"),
            "D. B + likaviktning vid ombalansering": dict(BEST, reweight=True),
            "E. B + kvartal + likaviktning": dict(BEST, reb="Q", reweight=True),
            "F. E men stopp 15 %": dict(BEST, reb="Q", reweight=True, stop=15),
            "G. E men stopp 20 %": dict(BEST, reb="Q", reweight=True, stop=20),
            "H. E men utan stopp": dict(BEST, reb="Q", reweight=True, stop=0),
            "I. E men 15 innehav": dict(BEST, reb="Q", reweight=True, n=15, band_out=25),
            "J. E men 30 innehav": dict(BEST, reb="Q", reweight=True, n=30, band_out=45),
            "K. E men band 40": dict(BEST, reb="Q", reweight=True, band_out=40),
            "L. E utan trendfilter": dict(BEST, reb="Q", reweight=True, trendfilter=False),
            "M. E med 12-1 och vol-just ändå": dict(BEST, reb="Q", reweight=True, voladj=True),
            "N. E med högt courtage (50 bp)": dict(BEST, reb="Q", reweight=True, cost_bps=50),
            "O. E med mycket högt courtage (100 bp)": dict(BEST, reb="Q", reweight=True, cost_bps=100),
        }
        res = {}
        for name, over in variants.items():
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr = run(close, vol, cfg)
                res[name] = stats(e, tr, years)
                print(f"\n=== {name} ===")
                for k, v in res[name].items(): print(f"   {k:<18}{v}")
            except Exception as ex:
                print(f"\n=== {name} === FEL: {ex}"); res[name] = {"fel": str(ex)[:120]}
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "år": round(years, 1), "aktier": int(close.shape[1]),
               "jämförelseindex": bench, "varianter": res, "läge": "kombinationer",
               "varning": "Survivorship bias och ingen fundamenta – se föregående körning."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    variants = {"Grundfall (12-1, vol-just, band 10/20, stopp 12 %)": {}}
    if not a.quick:
        variants.update({
            "Utan volatilitetsjustering": dict(voladj=False),
            "6-1 i stället för 12-1": dict(look=126),
            "3 mån utan skip (gamla modellen)": dict(look=63, skip=0),
            "Inget band (sälj utanför topp 10)": dict(band_out=10),
            "Vitt band (sälj under plats 30)": dict(band_out=30),
            "Stopp 7 %": dict(stop=7),
            "Stopp 20 %": dict(stop=20),
            "Utan stopp": dict(stop=0),
            "Utan trendfilter (SMA200)": dict(trendfilter=False),
            "20 innehav": dict(n=20, band_out=40),
            "5 innehav": dict(n=5, band_out=10),
            "Högt courtage (50 bp)": dict(cost_bps=50),
        })

    res = {}
    for name, over in variants.items():
        cfg = dict(DEF); cfg.update(over)
        try:
            e, tr = run(close, vol, cfg)
            res[name] = stats(e, tr, years)
            print(f"\n=== {name} ===")
            for k, v in res[name].items(): print(f"   {k:<18}{v}")
        except Exception as ex:
            print(f"\n=== {name} === FEL: {ex}")
            res[name] = {"fel": str(ex)[:120]}

    out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "år": round(years, 1), "aktier": int(close.shape[1]),
           "jämförelseindex": bench, "varianter": res,
           "varning": "Survivorship bias: tickerlistan är dagens indexmedlemmar, så resultaten är för optimistiska. "
                      "Fundamenta ingår inte i testet."}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nSkrev {a.out}")

if __name__ == "__main__":
    main()
