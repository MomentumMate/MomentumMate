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
def load_vix(index, start, end):
    """Volatilitetsindex. ^VIX (S&P 500), ^VXN (Nasdaq-100), ^V2TX (VSTOXX, Europa)."""
    import yfinance as yf
    try:
        d = yf.download(index, start=start, end=end, interval="1d", progress=False, auto_adjust=False)
        if d is None or d.empty: return None
        c = d["Close"]
        if hasattr(c, "columns"): c = c.iloc[:, 0]
        c = c.dropna()
        print(f"  {index}: {len(c)} dagar, median {float(c.median()):.1f}")
        return c
    except Exception as ex:
        print(f"  {index} kunde inte hämtas: {str(ex)[:70]}")
        return None

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
    cl, vl = [], []
    CH = 250
    for i in range(0, len(tickers), CH):
        part = tickers[i:i + CH]
        print(f"  {i + 1}–{min(i + CH, len(tickers))} av {len(tickers)} …", flush=True)
        try:
            raw = yf.download(part, start=start, end=end, interval="1d", auto_adjust=True,
                              progress=False, threads=True, group_by="column")
            if raw is None or raw.empty: continue
            c = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
            v = raw["Volume"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Volume"]]
            cl.append(c); vl.append(v)
        except Exception as ex:
            print(f"    hoppades över: {str(ex)[:70]}")
    if not cl: raise SystemExit("Ingen kursdata kunde hämtas.")
    close = pd.concat(cl, axis=1); vol = pd.concat(vl, axis=1)
    close = close.loc[:, ~close.columns.duplicated()]
    vol = vol.loc[:, ~vol.columns.duplicated()]
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

def score_to_return(close, sc, horizon=63):
    """Hur mycket extra avkastning är en poäng värd?

    För varje ombalanseringsdag jämförs aktiernas poäng med deras faktiska avkastning
    de följande ~3 månaderna. Lutningen i den regressionen är växelkursen: hur många
    procents avkastning en poäng motsvarar. Utan den går kostnad och poäng inte att väga.
    """
    rows = []
    dates = close.index
    for i in range(260, len(dates) - horizon, 21):
        s_i = sc.iloc[i].dropna()
        if len(s_i) < 50: continue
        fwd = (close.iloc[i + horizon] / close.iloc[i] - 1) * 100
        both = pd.concat([s_i.rename("sc"), fwd.rename("r")], axis=1).dropna()
        both = both[np.isfinite(both["r"]) & (both["r"].abs() < 300)]
        if len(both) < 50: continue
        # endast den övre halvan är relevant – det är där kandidaterna hämtas
        both = both[both["sc"] >= both["sc"].quantile(0.5)]
        if len(both) < 25: continue
        x, y = both["sc"].values, both["r"].values
        if x.std() < 1: continue
        rows.append(np.polyfit(x, y, 1)[0])          # lutning: procent per poäng
    if len(rows) < 12: return None, 0
    return float(np.median(rows)), len(rows)

def period_ends(idx, freq="M"):
    """Sista handelsdagen i varje månad (M), kvartal (Q), halvår (H) eller år (Y)."""
    s = pd.Series(idx, index=idx)
    if freq == "M":   key = [idx.year, idx.month]
    elif freq == "Q": key = [idx.year, idx.quarter]
    elif freq == "H": key = [idx.year, (idx.month - 1) // 6]
    else:             key = [idx.year]
    return list(s.groupby(key).last())

def exit_panels(close, cfg):
    """Nivåer för de utgångar som inte är ett fast procentstopp."""
    out = {}
    if cfg.get("exit_sma50"):
        out["sma50"] = close.rolling(50, min_periods=40).mean()
    if cfg.get("exit_sma200"):
        out["sma200"] = close.rolling(200, min_periods=160).mean()
    if cfg.get("exit_atr"):
        # ATR saknar high/low i panelen, så vi använder dagsrörelsens standardavvikelse som motsvarighet
        out["atr"] = (close.pct_change().abs().rolling(14, min_periods=10).mean() * close) * 1.6
    return out

def vix_exposure(vix, idx, cfg):
    """Andel av kapitalet som får vara investerad, 0–1, utifrån volatilitetsnivån.

    Daniel & Moskowitz (2016) visar att momentumkrascher nästan uteslutande inträffar när
    marknaden fallit OCH volatiliteten är hög. Regeln skalar ned exponeringen i de lägena.
    Nivån jämförs med ett rullande historiskt fönster, inte med ett fast tal, så den
    anpassar sig till att volatilitetens normalläge skiftar över tid.
    """
    if vix is None or not cfg.get("vix_cut"):
        return pd.Series(1.0, index=idx)
    v = vix.reindex(idx).ffill()
    hi = v.rolling(cfg.get("vix_win", 504), min_periods=250).quantile(cfg["vix_q"])
    e = pd.Series(1.0, index=idx)
    e[v > hi] = cfg["vix_cut"]                       # över tröskeln: minskad exponering
    return e.shift(1).fillna(1.0)                    # gårdagens signal styr idag – ingen framåtblick

# Nordeas faktiska priser, verifierade mot 13 avräkningsnotor 2026-10-08 till 10-13.
# Courtage 0,09 % med 29 kr som lägsta avgift utländskt, 1 kr nordiskt.
# Växling 0,266 % per affär (uppmätt median på åtta köp/säljpar inom 12 minuter).
# Transaktionsskatt vid KÖP: Storbritannien 0,50 %, Frankrike 0,40 %, Spanien 0,20 %, Italien 0,10 %.
NORDIC = (".ST", ".CO", ".OL", ".HE")
STAMP_BT = {".L": 0.0050, ".PA": 0.0040, ".MC": 0.0020, ".MI": 0.0010}

def real_cost(amount_sek, ticker, side, cfg):
    """Kostnad i kronor för en affär. amount_sek är affärens belopp."""
    if cfg.get("cost_bps") is not None and cfg.get("simple_cost"):
        return amount_sek * cfg["cost_bps"] / 10000.0
    nordic = any(ticker.endswith(x) for x in NORDIC)
    sek = nordic and ticker.endswith(".ST")
    cour = max(amount_sek * cfg.get("cour_pct", 0.0009), 1.0 if sek else 29.0)
    fx = 0.0 if sek else amount_sek * cfg.get("fx_pct", 0.00266)
    stamp = 0.0
    if side == "buy":
        for k, v in STAMP_BT.items():
            if ticker.endswith(k): stamp = amount_sek * v; break
    return cour + fx + stamp

def run(close, vol, cfg, vix=None):
    """Simulerar strategin. Returnerar kapitalkurva och nyckeltal."""
    sc, _ = score_panel(close, vol, cfg)
    EX = exit_panels(close, cfg)
    # Rekylkrav: en kandidat får köpas först när kursen fallit en viss andel från sin
    # högsta nivå de senaste N dagarna. 23,6 / 38,2 / 50 / 61,8 % av rekylen motsvarar
    # Fibonaccinivåerna; de testas som vilka trösklar som helst.
    PB = None
    if cfg.get("pullback"):
        win = cfg.get("pb_win", 60)
        hi = close.rolling(win, min_periods=max(20, win // 3)).max()
        lo = close.rolling(win, min_periods=max(20, win // 3)).min()
        if cfg.get("pb_mode") == "fib":
            # andel av hela spannet högsta–lägsta, alltså klassisk Fibonacciretracement
            PB = (hi - close) / (hi - lo).replace(0, np.nan)
        else:
            # enkel andel under toppen
            PB = (hi - close) / hi.replace(0, np.nan)
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

    expo = vix_exposure(vix, close.index, cfg)
    n_eff_now = lambda: n_eff
    cash, hold, hold_days = 1.0, {}, []
    cut_days = 0          # hold: ticker -> {"sh": andel av kapitalet, "px": inköpskurs, "peak": högsta}
    eq, eqdates, trades = [], [], []
    PORT = cfg.get("port_sek", 10_000_000.0)      # portföljens storlek i kronor, styr poststorleken
    def fee(val_units, t, side):
        """val_units är positionens andel av kapitalet (1.0 = hela). Returnerar samma enhet."""
        amt = max(0.0, val_units) * PORT
        return real_cost(amt, t, side, cfg) / PORT if amt > 0 else 0.0

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
                cash += hold[t]["sh"] * (q if q == q else hold[t]["px"]) - fee(hold[t]["sh"] * (q if q == q else hold[t]["px"]), t, "sell")
                trades.append(("delisted", t, d)); del hold[t]
        # --- dagliga stopp (gäller mellan ombalanseringarna) ---
        for t in list(hold):
            px = p.get(t)
            if px != px:   # NaN
                continue
            h = hold[t]
            h["peak"] = max(h["peak"], px)
            base = h["peak"] if cfg["trail"] else h["px"]
            why = None
            if cfg["stop"] > 0 and px <= base * (1 - cfg["stop"] / 100): why = "stop"
            if why is None and "sma50" in EX:
                lv = EX["sma50"].iat[i, close.columns.get_loc(t)]
                if lv == lv and px < lv: why = "sma50"
            if why is None and "sma200" in EX:
                lv = EX["sma200"].iat[i, close.columns.get_loc(t)]
                if lv == lv and px < lv: why = "sma200"
            if why is None and "atr" in EX:
                a = EX["atr"].iat[i, close.columns.get_loc(t)]
                if a == a and px <= h["peak"] - cfg["exit_atr"] * a: why = "atr"
            if why is None and cfg.get("exit_score", 0) > 0:
                s_ = sc.iat[i, close.columns.get_loc(t)]
                if s_ == s_ and s_ < cfg["exit_score"]: why = "svag poäng"
            if why:
                cash += h["sh"] * px - fee(h["sh"] * px, t, "sell")
                hold_days.append((i - h["i"]))
                trades.append((why, t, d)); del hold[t]

        # --- volatilitetsfilter: kontrolleras VARJE dag, inte bara vid ombalansering.
        #     En krasch väntar inte på kvartalsskiftet.
        ex = float(expo.iloc[i]); cut_days += (ex < 1)
        n_eff = max(1, int(round(cfg["n"] * ex)))
        if len(hold) > n_eff:
            sc_row = sc.iloc[i]
            for t in sorted(hold, key=lambda x: sc_row.get(x, -1e9))[:len(hold) - n_eff]:
                px = p.get(t)
                if px == px:
                    cash += hold[t]["sh"] * px - fee(hold[t]["sh"] * px, t, "sell"); hold_days.append(i - hold[t]["i"])
                    trades.append(("vix", t, d))
                del hold[t]

        # --- påfyllning: sker löpande, inte bara vid ombalansering ---
        #     (stopp och bandutgångar sker dagligen, så kapitalet ska inte ligga i kassa
        #      till nästa kvartalsskifte – appen föreslår ersättare direkt)
        if cfg.get("refill") and d not in rebs and len(hold) < n_eff:
            row_r = sc.iloc[i].dropna()
            mk_r = cfg.get("mkt", "all")
            if mk_r == "nordic":
                row_r = row_r[[t for t in row_r.index if any(t.endswith(x) for x in NORDIC)]]
            elif mk_r == "us":
                row_r = row_r[[t for t in row_r.index if "." not in t]]
            if len(row_r) >= cfg["n"]:
                rk = row_r.rank(ascending=False, method="first")
                free = [t for t in rk[rk <= cfg["n"]].sort_values().index if t not in hold]
                tot_r = value(i)
                for t in free[:n_eff - len(hold)]:
                    px = p.get(t)
                    if px != px or px <= 0: continue
                    target = tot_r / max(1, n_eff)
                    if cash < target * 0.2: break
                    amt = min(target, cash)
                    sh = (amt - fee(amt, t, "buy")) / px
                    cash -= amt
                    hold[t] = {"sh": sh, "px": px, "peak": px, "i": i}; trades.append(("buy", t, d))

        # --- ombalansering ---
        if d in rebs:
            row = sc.iloc[i].dropna()
            mk_sel = cfg.get("mkt", "all")
            if mk_sel == "nordic":
                row = row[[t for t in row.index if any(t.endswith(x) for x in NORDIC)]]
            elif mk_sel == "us":
                row = row[[t for t in row.index if "." not in t]]
            if len(row) >= cfg["n"]:
                rank = row.rank(ascending=False, method="first")
                top = set(rank[rank <= cfg["n"]].index)
                keep = set(t for t in hold if rank.get(t, 1e9) <= cfg["band_out"])
                # sälj allt som ramlat ur bandet
                for t in list(hold):
                    if t not in keep:
                        px = p.get(t)
                        if px == px:
                            cash += hold[t]["sh"] * px - fee(hold[t]["sh"] * px, t, "sell"); hold_days.append(i - hold[t]["i"]); trades.append(("band", t, d))
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
                            h["sh"] -= sell; cash += sell * px - fee(sell * px, t, "sell"); trades.append(("trim", t, d))
                        elif cur < target * (1 - cfg.get("rw_band", 0.25)) and cash > 0:
                            add = min(target - cur, cash)
                            h["sh"] += (add - fee(add, t, "buy")) / px; cash -= add; trades.append(("add", t, d))
                # fyll på upp till n med de högst rankade
                room = n_eff_now() - len(hold)
                if room > 0:
                    cand_all = [t for t in rank.sort_values().index if t not in hold and t in top]
                    if cfg.get("cost_adj"):
                        # Väg poäng mot kostnad i stället för att sortera bort dyra aktier.
                        # slope = procents avkastning per poäng, mätt på historiken.
                        post_sek = value(i) / max(1, n_eff_now()) * PORT
                        sl = cfg.get("slope") or 0.0
                        row_sc = sc.iloc[i]
                        def net(t):
                            s_ = row_sc.get(t)
                            if s_ != s_: return -9e9
                            rt = (real_cost(post_sek, t, "buy", cfg)
                                  + real_cost(post_sek, t, "sell", cfg)) / max(1.0, post_sek) * 100
                            return s_ * sl - rt          # förväntad avkastning minus kostnad
                        cand_all = sorted(cand_all, key=net, reverse=True)
                    if cfg.get("cost_filter"):
                        # Posten måste bära sin marknads minimicourtage: 1 kr nordiskt, 29 kr utländskt.
                        # Under den gränsen betalar man i praktiken en fast avgift per affär.
                        post = value(i) / max(1, n_eff_now()) * PORT
                        cand_all = [t for t in cand_all
                                    if post >= (1.0 if t.endswith(".ST") else 29.0) / cfg.get("cour_pct", 0.0009)]
                    if PB is not None:
                        row_pb = PB.iloc[i]
                        lo_t, hi_t = cfg["pullback"], cfg.get("pb_max", 1.0)
                        cand_all = [t for t in cand_all
                                    if (row_pb.get(t) == row_pb.get(t)) and lo_t <= row_pb.get(t) <= hi_t]
                    picks = cand_all[:room]
                    tot = value(i)
                    for t in picks:
                        px = p.get(t)
                        if px != px or px <= 0: continue
                        target = tot / max(1, n_eff)
                        if cash < target * 0.2: break
                        amt = min(target, cash)
                        sh = (amt - fee(amt, t, "buy")) / px
                        cash -= amt
                        hold[t] = {"sh": sh, "px": px, "peak": px, "i": i}; trades.append(("buy", t, d))
        eq.append(value(i)); eqdates.append(d)

    e = pd.Series(eq, index=pd.DatetimeIndex(eqdates)).dropna()
    if cfg.get("vix_cut"): print(f"      nedskalad exponering {cut_days} av {len(expo)} dagar")
    return e, trades, hold_days

# ----------------------------------------------------------------------------- mått
def stats(e, trades, years, hold_days=None):
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
            "köp_per_år": round(buys / years, 1),
            "innehavstid_dagar": (round(float(np.median(hold_days)) * 1.4) if hold_days else None),
            "längsta_innehav_dagar": (round(max(hold_days) * 1.4) if hold_days else None),
            "utgångar": {k: sum(1 for t in trades if t[0] == k)
                         for k in ("stop", "sma50", "sma200", "atr", "svag poäng", "band") 
                         if any(t[0] == k for t in trades)}}

def benchmark(close, years, start_i=210):
    sub = close.iloc[start_i:]
    r = sub.pct_change().mean(axis=1).fillna(0)       # likaviktat index av universumet
    e = (1 + r).cumprod()
    return e, stats(e, [], years)

# ----------------------------------------------------------------------------- main
DEF = dict(n=10, band_out=20, stop=12, trail=True, look=252, skip=21, voladj=True,
           trendfilter=True, minturnover=3e6, cost_bps=15, reb="M", reweight=False, rw_band=0.25,
           exit_sma50=False, exit_sma200=False, exit_atr=0, exit_score=0,
           vix_cut=0, vix_q=0.80, vix_win=504,
           port_sek=10_000_000.0, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False,
           pullback=0, pb_win=60, pb_mode='pct', pb_max=1.0,
           mkt='all', cost_filter=False, cost_adj=False, slope=0.0, refill=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=10)
    ap.add_argument("--universe", default=None, help="fil med en ticker per rad")
    ap.add_argument("--out", default="backtest.json")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--quick", action="store_true", help="kör bara grundfallet")
    ap.add_argument("--combo", action="store_true", help="testa kombinationer i stället för en ändring i taget")
    ap.add_argument("--robust", action="store_true", help="håller vinnaren på data den inte valts på?")
    ap.add_argument("--exits", action="store_true", help="jämför utgångsmekanismer för swinghandel")
    ap.add_argument("--vix", action="store_true", help="testar om volatilitetsindex skyddar mot momentumkrascher")
    ap.add_argument("--vixindex", default="^VIX", help="^VIX, ^VXN eller ^V2TX")
    ap.add_argument("--entry", action="store_true", help="kostnader + poststorlek + rekyltajming")
    ap.add_argument("--mix", action="store_true", help="bästa kombination av antal, marknad och hålltid per portföljstorlek")
    ap.add_argument("--port", type=float, default=10_000_000, help="portföljstorlek i kronor")
    a = ap.parse_args()

    if a.universe:
        tick = [x.split("#")[0].strip() for x in open(a.universe) if x.split("#")[0].strip()]
    else:
        sys.path.insert(0, ".")
        import scanner as SC
        tick = []
        # Samma marknader som skannern kör skarpt, så att backtestet validerar rätt universum
        for m in SC.MARKETS:
            k = m["k"]
            v = m.get("source") or m.get("tickers")
            if callable(v):
                try: v = v()
                except Exception as ex:
                    print(f"  VARNING: {k} kunde inte hämtas ({str(ex)[:60]})"); v = []
            if isinstance(v, list) and v:
                tick += v; print(f"  {k}: {len(v)} aktier")
            else: print(f"  VARNING: {k} gav 0 aktier")
        # Reservlistor om nätet mot Wikipedia fallerar – annars tappar vi hela USA
        if not any("." not in t for t in tick):
            for nm in ("SP500_FALLBACK", "NASDAQ_FALLBACK", "DOW_FALLBACK"):
                fb = getattr(SC, nm, None)
                if isinstance(fb, (list, tuple)) and fb:
                    tick += list(fb); print(f"  använder {nm} ({len(fb)} aktier)")
        tick = sorted(set(tick))
    def _grp(t):
        if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): return "Norden"
        return "Europa/övrigt" if "." in t else "USA"
    from collections import Counter
    comp = Counter(_grp(t) for t in tick)
    print(f"Universum: {len(tick)} aktier – " + ", ".join(f"{k} {v}" for k, v in sorted(comp.items())))
    if comp.get("USA", 0) < 50:
        print("  VARNING: få eller inga amerikanska aktier. Resultaten gäller då inte USA-tunga portföljer.")

    close, vol = load_prices(tick, a.years, a.cache)
    years = (close.index[-1] - close.index[210]).days / 365.25

    comp2 = Counter(_grp(t) for t in close.columns)
    print("Nedladdat: " + ", ".join(f"{k} {v}" for k, v in sorted(comp2.items())))
    bench_e, bench = benchmark(close, years)
    print("\n=== JÄMFÖRELSE: likaviktat index av universumet ===")
    for k, v in bench.items(): print(f"   {k:<18}{v}")

    # ---------- Mix-test: vilken kombination ger högst avkastning för just denna portföljstorlek? ----------
    if a.mix:
        BASE = dict(voladj=False, stop=15, reweight=True, port_sek=a.port,
                    cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "A. Alla marknader, 20 innehav, kvartal (referens)": dict(BASE, n=20, band_out=30, reb="Q"),
            "B. Alla + kostnadsfilter, 20 innehav, kvartal":     dict(BASE, n=20, band_out=30, reb="Q", cost_filter=True),
            "C. Alla + kostnadsfilter, 10 innehav, kvartal":     dict(BASE, n=10, band_out=20, reb="Q", cost_filter=True),
            "D. Endast Norden, 10 innehav, kvartal":             dict(BASE, n=10, band_out=20, reb="Q", mkt="nordic"),
            "E. Endast Norden, 20 innehav, kvartal":             dict(BASE, n=20, band_out=30, reb="Q", mkt="nordic"),
            "F. Endast Norden, 30 innehav, kvartal":             dict(BASE, n=30, band_out=45, reb="Q", mkt="nordic"),
            "G. Endast USA, 10 innehav, kvartal":                dict(BASE, n=10, band_out=20, reb="Q", mkt="us"),
            "H. Endast USA, 20 innehav, kvartal":                dict(BASE, n=20, band_out=30, reb="Q", mkt="us"),
            "I. Alla, 20 innehav, HALVÅR":                       dict(BASE, n=20, band_out=30, reb="H"),
            "J. Alla, 20 innehav, HELÅR":                        dict(BASE, n=20, band_out=30, reb="Y"),
            "K. Endast Norden, 20 innehav, halvår":              dict(BASE, n=20, band_out=30, reb="H", mkt="nordic"),
            "L. Kostnadsfilter, 20 innehav, halvår":             dict(BASE, n=20, band_out=30, reb="H", cost_filter=True),
            "M. Alla, 20 innehav, kvartal, brett band 50":       dict(BASE, n=20, band_out=50, reb="Q"),
            "N. Alla, 10 innehav, helår (minsta omsättning)":    dict(BASE, n=10, band_out=25, reb="Y"),
            "O. KOSTNADSVÄGD: poäng minus kostnad, 20 innehav":  dict(BASE, n=20, band_out=30, reb="Q", cost_adj=True),
            "P. KOSTNADSVÄGD, 10 innehav":                       dict(BASE, n=10, band_out=20, reb="Q", cost_adj=True),
            "Q. KOSTNADSVÄGD, 20 innehav, halvår":               dict(BASE, n=20, band_out=30, reb="H", cost_adj=True),
        }
        # Växelkursen poäng → avkastning mäts en gång och används av de kostnadsvägda varianterna.
        sc_panel, _ = score_panel(close, vol, dict(DEF, voladj=False))
        slope, nobs = score_to_return(close, sc_panel, 63)
        print(f"\n=== VÄXELKURS POÄNG → AVKASTNING ===")
        if slope is None:
            print("   Sambandet kunde inte mätas – för lite data. Kostnadsvägda varianter hoppas över.")
            slope = 0.0
        else:
            print(f"   1 poäng ≈ {slope:.4f} % avkastning per kvartal  (median av {nobs} mätpunkter)")
            print(f"   10 poängs skillnad ≈ {slope*10:.2f} % per kvartal, alltså {slope*10*4:.2f} % per år")
            print(f"   En USA-aktie kostar ca 0,71 % mer tur och retur än en svensk.")
            print(f"   Den skillnaden motsvarar {0.71/max(slope,1e-6):.1f} poäng – en USA-aktie måste ligga")
            print(f"   så många poäng högre för att vara värd merkostnaden.")
        for k in list(VAR):
            if VAR[k].get("cost_adj"): VAR[k]["slope"] = slope
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   {'index (köp och behåll)':<52}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}  nedgång {bst['max_nedgång_%']} %")
        res = {"_index": bst}
        for nm, over in VAR.items():
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                print(f"   {nm:<52}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<52} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        best = max((k for k in res if k != "_index" and "CAGR_%" in res[k]),
                   key=lambda k: res[k]["CAGR_%"], default=None)
        if best: print(f"\n   BÄST FÖR {a.port:,.0f} kr: {best}  ({res[best]['CAGR_%']} %)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "mix", "portfölj_sek": a.port, "år": round(yrs, 1), "bäst": best, "varianter": res,
               "poäng_per_procent": round(slope, 5), "mätpunkter": nobs,
               "varning": "Survivorship bias och ingen fundamenta. Kostnader enligt Nordeas pris verifierat mot avräkningsnotor."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Entry-test: verkliga kostnader, poststorlek och rekyltajming ----------
    if a.entry:
        CORE = dict(voladj=False, band_out=30, n=20, reb="Q", reweight=True, stop=15,
                    port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "A. Referens: 20 innehav, verkliga kostnader": dict(CORE),
            "B. Samma men gammal schablon 15 bp": dict(CORE, simple_cost=True, cost_bps=15),
            "C. 10 innehav (större poster)": dict(CORE, n=10, band_out=20),
            "D. 15 innehav": dict(CORE, n=15, band_out=25),
            "E. 30 innehav (mindre poster)": dict(CORE, n=30, band_out=45),
            "F. 20 innehav, halvårsvis ombalansering": dict(CORE, reb="Q", band_out=40),
            "G. Rekyl: köp först 5 % under 60-dagarstoppen": dict(CORE, pullback=0.05),
            "H. Rekyl: köp först 10 % under toppen": dict(CORE, pullback=0.10),
            "I. Rekyl: köp först 15 % under toppen": dict(CORE, pullback=0.15),
            "J. Fibonacci 23,6 % av spannet": dict(CORE, pullback=0.236, pb_mode="fib", pb_max=0.45),
            "K. Fibonacci 38,2 % av spannet": dict(CORE, pullback=0.382, pb_mode="fib", pb_max=0.60),
            "L. Fibonacci 50–61,8 % av spannet": dict(CORE, pullback=0.50, pb_mode="fib", pb_max=0.70),
            "M. Omvänt: köp bara nära toppen (max 3 % under)": dict(CORE, pullback=0.0, pb_max=0.03),
        }
        def grp4(t):
            if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): return "Norden"
            return "Europa" if "." in t else "USA"
        groups = {"Alla marknader": list(close.columns)}
        for g in ("USA", "Norden", "Europa"):
            cc = [t for t in close.columns if grp4(t) == g]
            if len(cc) >= 40: groups[g] = cc
        res = {}
        for gn, cols in groups.items():
            sub, subv = close[cols], vol[cols]
            yrs = (sub.index[-1] - sub.index[210]).days / 365.25
            be, bst = benchmark(sub, yrs)
            print(f"\n########## {gn}  ({len(cols)} aktier, portfölj {a.port:,.0f} kr) ##########")
            print(f"   {'index (köp och behåll)':<46}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}  nedgång {bst['max_nedgång_%']} %")
            res[gn] = {"_index": bst}
            for nm, over in VAR.items():
                cfg = dict(DEF); cfg.update(over)
                try:
                    e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, yrs, hd)
                    res[gn][nm] = st
                    print(f"   {nm:<46}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                          f"  köp/år {st['köp_per_år']}  innehav {st['innehavstid_dagar']} d")
                except Exception as ex:
                    print(f"   {nm:<46} FEL {str(ex)[:50]}"); res[gn][nm] = {"fel": str(ex)[:120]}
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "entry", "portfölj_sek": a.port, "år": round(years, 1), "grupper": res,
               "varning": "Survivorship bias och ingen fundamenta. Kostnader enligt Nordeas pris verifierat mot avräkningsnotor."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- VIX-test: skyddar volatilitetsfiltret mot momentumkrascher? ----------
    if a.vix:
        v = load_vix(a.vixindex, close.index[0], close.index[-1])
        if v is None: raise SystemExit("Volatilitetsindex kunde inte hämtas.")
        CORE = dict(voladj=False, band_out=30, n=20, reb="Q", reweight=True, stop=15)   # validerad kärna (F)
        VAR = {
            "0. Utan volatilitetsfilter (referens)": dict(CORE),
            "1. Halvera över 80:e percentilen": dict(CORE, vix_cut=0.5, vix_q=0.80),
            "2. Halvera över 90:e percentilen": dict(CORE, vix_cut=0.5, vix_q=0.90),
            "3. Ned till 25 % över 80:e percentilen": dict(CORE, vix_cut=0.25, vix_q=0.80),
            "4. Ned till 25 % över 90:e percentilen": dict(CORE, vix_cut=0.25, vix_q=0.90),
            "5. Helt ur marknaden över 90:e percentilen": dict(CORE, vix_cut=0.01, vix_q=0.90),
            "6. Ned till 75 % över 70:e percentilen": dict(CORE, vix_cut=0.75, vix_q=0.70),
            "7. Halvera över 80:e, kort fönster (1 år)": dict(CORE, vix_cut=0.5, vix_q=0.80, vix_win=252),
            "8. Halvera över 80:e, långt fönster (4 år)": dict(CORE, vix_cut=0.5, vix_q=0.80, vix_win=1008),
        }
        def grp3(t):
            if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): return "Norden"
            return "Europa" if "." in t else "USA"
        groups = {"Alla marknader": list(close.columns)}
        for g in ("USA", "Norden", "Europa"):
            cc = [t for t in close.columns if grp3(t) == g]
            if len(cc) >= 40: groups[g] = cc
        res = {}
        for gn, cols in groups.items():
            sub, subv = close[cols], vol[cols]
            yrs = (sub.index[-1] - sub.index[210]).days / 365.25
            be, bst = benchmark(sub, yrs)
            print(f"\n########## {gn}  ({len(cols)} aktier, index {a.vixindex}) ##########")
            print(f"   {'index (köp och behåll)':<44}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}  nedgång {bst['max_nedgång_%']} %")
            res[gn] = {"_index": bst}
            for nm, over in VAR.items():
                cfg = dict(DEF); cfg.update(over)
                try:
                    e, tr, hd = run(sub, subv, cfg, vix=v); st = stats(e, tr, yrs, hd)
                    res[gn][nm] = st
                    print(f"   {nm:<44}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %  köp/år {st['köp_per_år']}")
                except Exception as ex:
                    print(f"   {nm:<44} FEL {str(ex)[:50]}"); res[gn][nm] = {"fel": str(ex)[:120]}
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "vix", "index": a.vixindex, "år": round(years, 1), "grupper": res,
               "varning": "Survivorship bias och ingen fundamenta. Volatilitetssignalen är fördröjd en dag."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- utgångstest: vilken utgång låter vinnarna löpa längst? ----------
    if a.exits:
        CORE = dict(voladj=False, band_out=30, n=20, reb="Q", reweight=True)   # validerad kärna
        EX = {
            "1. Enbart band 10/30 (ingen annan utgång)": dict(CORE, stop=0),
            "2. Fast stopp 15 % från toppkurs": dict(CORE, stop=15),
            "3. Fast stopp 20 % från toppkurs": dict(CORE, stop=20),
            "4. Trendbrott: stängning under SMA50": dict(CORE, stop=0, exit_sma50=True),
            "5. Långsam trend: stängning under SMA200": dict(CORE, stop=0, exit_sma200=True),
            "6. ATR-stopp: topp minus 5 ATR": dict(CORE, stop=0, exit_atr=5),
            "7. ATR-stopp: topp minus 8 ATR": dict(CORE, stop=0, exit_atr=8),
            "8. ATR-stopp: topp minus 12 ATR": dict(CORE, stop=0, exit_atr=12),
            "9. Stagnation: poäng under 30": dict(CORE, stop=0, exit_score=30),
            "10. Stagnation: poäng under 45": dict(CORE, stop=0, exit_score=45),
            "11. SMA200 + ATR 8 (båda)": dict(CORE, stop=0, exit_sma200=True, exit_atr=8),
            "12. ATR 8 + stagnation 30": dict(CORE, stop=0, exit_atr=8, exit_score=30),
            "13. Stopp 20 % + stagnation 30": dict(CORE, stop=20, exit_score=30),
        }
        def grp2(t):
            if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): return "Norden"
            return "Europa" if "." in t else "USA"
        groups = {"Alla marknader": list(close.columns)}
        for g in ("USA", "Norden", "Europa"):
            cc = [t for t in close.columns if grp2(t) == g]
            if len(cc) >= 40: groups[g] = cc
        res = {}
        for gn, cols in groups.items():
            sub, subv = close[cols], vol[cols]
            yrs = (sub.index[-1] - sub.index[210]).days / 365.25
            be, bst = benchmark(sub, yrs)
            print(f"\n########## {gn}  ({len(cols)} aktier) ##########")
            print(f"   {'index (köp och behåll)':<44}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}  nedgång {bst['max_nedgång_%']} %")
            res[gn] = {"_index": bst}
            for nm, over in EX.items():
                cfg = dict(DEF); cfg.update(over)
                try:
                    e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, yrs, hd)
                    res[gn][nm] = st
                    print(f"   {nm:<44}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                          f"  innehav {st['innehavstid_dagar']} d  köp/år {st['köp_per_år']}")
                except Exception as ex:
                    print(f"   {nm:<44} FEL {str(ex)[:50]}"); res[gn][nm] = {"fel": str(ex)[:120]}
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "utgångar", "år": round(years, 1), "grupper": res,
               "varning": "Survivorship bias och ingen fundamenta. Innehavstiden är median i kalenderdagar."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

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
                        e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, yrs, hd)
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
                e, tr, hd = run(close, vol, cfg)
                res[name] = stats(e, tr, years, hd)
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
            e, tr, hd = run(close, vol, cfg)
            res[name] = stats(e, tr, years, hd)
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
