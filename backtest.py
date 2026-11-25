#!/usr/bin/env python3
"""
backtest.py – validerar den TEKNISKA kärnan i MomentumMate.

Vad som testas: momentumurval (12-1, tak 150 %, lika poäng → högre 12-1), trendfilter, likviditetsfilter,
månadsvis hållregel, kvartalslikaviktning – samt vad courtaget äter. Modell 1.0 = SPEC.md 2026-11-04.

Fyllnad (2026-11-05): utan --t1 fylls alla affärer på SIGNALDAGENS stängning, vilket är hur alla resultat
t.o.m. 2026-11-04 mätts. Med --t1 fylls de på nästa handelsdags stängning, som appen handlar. --slip lägger
till marknadspåverkan (kvadratrotsmodell mot dagsomsättning). --minadv X sätter ett likviditetsgolv i kronor;
det gamla golvet 3 M (minturnover) är i lokal valuta och betyder 3 M USD för USA men 3 M pence för London.
--mm2 kör 2.0-kandidaten enligt TESTPLAN_2.0.md.

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
    # EODHD först om nyckel finns – samma aktier varje körning, vilket tar bort den
    # slumpvariation på 2–3 procentenheter som Yahoos varierande leverans orsakade.
    try:
        import scanner as SC
        if SC.EODHD_KEY:
            print("  Datakälla: EODHD (nyckel hittad), Yahoo som reserv")
            got = SC.dl_eodhd(list(tickers), period=f"{int(years) + 2}y")
            st = SC.EODHD_STAT
            if st.get("err") or not got:
                from collections import Counter
                top = Counter(st.get("errs", [])).most_common(3)
                print(f"  EODHD: {st.get('ok',0)} ok, {st.get('miss',0)} tomma, {st.get('err',0)} fel"
                      + (": " + "; ".join(f"{n}× {m}" for m, n in top) if top else ""))
            if got:
                cl.append(pd.DataFrame({t: d["Close"] for t, d in got.items()}))
                vl.append(pd.DataFrame({t: d["Volume"] for t, d in got.items()}))
                rest = [t for t in tickers if t not in got]
                print(f"  EODHD gav {len(got)} av {len(tickers)} – {len(rest)} hämtas från Yahoo")
                tickers = rest
    except Exception as ex:
        print(f"  EODHD ej tillgängligt ({str(ex)[:60]}) – använder Yahoo")
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
    # Felkursfilter (2026-10-23): en enda korrupt kurs kan ge likaviktat universum +70 % på ett år.
    # En aktie som på EN dag rör sig mer än +300 % eller −80 % utan att komma tillbaka inom fem dagar
    # är nästan alltid ett datafel (fel valuta, saknad split, decimalfel). Sådana aktier rapporteras
    # och tas bort helt – hellre 10 aktier färre än en fiktiv vinnare.
    # Laga i stället för kasta (SPEC § 16, 2026-11-05): korrupta dagar som är tillbaka inom fem handelsdagar ersätts med
    # föregående stängning; aktien behålls. Bara aktier där felet består (kursen kommer inte tillbaka) tas bort.
    # Tidigare kastades hela aktien, vilket tog bort 12 nordiska bolag (Bufab, Instalco, Troax, Hexatronic m.fl.) ur alla
    # tio åren på grund av korrupta EODHD-rader 2019-06-10 och 2019-08-05, medan skannern behöll dem.
    close, fixed, bad = repair_glitches(close)
    if fixed:
        print(f"  FELKURSER LAGADE: {len(fixed)} aktier (dagar ersatta med föregående stängning):")
        for t, why in list(fixed.items())[:25]: print(f"    {t:<14} {why}")
    if bad:
        print(f"  FELKURSFILTER: {len(bad)} aktier bortfiltrerade (felet består):")
        for t, why in list(bad.items())[:25]: print(f"    {t:<14} {why}")
        close = close.drop(columns=list(bad))
    vol = vol.reindex(columns=close.columns)
    if cache:
        try: close.to_pickle(cache)
        except Exception: pass
    print(f"  {close.shape[1]} aktier med data, {close.shape[0]} handelsdagar")
    return close, vol

def sp500_entry_dates():
    """Inträdesdatum för dagens S&P 500-medlemmar ur Wikipedias tabell (kolumnen "Date added"). {ticker: Timestamp}."""
    import requests, io
    try:
        r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                         headers={"User-Agent": "Mozilla/5.0 (MomentumMate backtest)"}, timeout=30); r.raise_for_status()
        tabs = pd.read_html(io.StringIO(r.text))
        out = {}
        for tb in tabs:
            cols = {str(c).strip().lower(): c for c in tb.columns}
            sym = next((cols[c] for c in cols if c in ("symbol", "ticker")), None)
            add = next((cols[c] for c in cols if c.startswith("date added") or c.startswith("date first added")), None)
            if sym is None or add is None: continue
            for _, row in tb.iterrows():
                t = str(row[sym]).strip().replace(".", "-"); d = pd.to_datetime(str(row[add])[:10], errors="coerce")
                if t and d == d: out[t] = d
            break
        print(f"  S&P 500: inträdesdatum för {len(out)} bolag ({sum(1 for d in out.values() if d >= pd.Timestamp('2015-01-01'))} tillagda sedan 2015)")
        return out
    except Exception as ex:
        print(f"  S&P 500-inträdesdatum kunde inte hämtas: {str(ex)[:70]}"); return {}

def repair_glitches(close, up=3.0, dn=-0.8, back_days=5):
    """Hittar dagsrörelser över +up / under dn. Är kursen tillbaka inom ±50 % av nivån före inom back_days handelsdagar
    ersätts de felaktiga dagarna med föregående stängning. Returnerar (close, lagade, kvarstående)."""
    close = close.copy(); fixed, bad = {}, {}
    r = close.pct_change()
    for t in close.columns:
        rt = r[t]
        hits = rt.index[(rt > up) | (rt < dn)]
        if len(hits) == 0: continue
        col = close[t].values.copy(); idx = close.index
        ok = True; n_fix = 0; first = None
        for d0 in hits:
            i0 = idx.get_loc(d0)
            if i0 < 1: continue
            before = col[i0 - 1]
            if not (before == before and before > 0): continue
            if 0.5 <= col[i0] / before <= 2.0: continue            # redan lagad av en tidigare träff
            j = None
            for k in range(i0, min(len(col), i0 + back_days + 1)):
                v = col[k]
                if v == v and 0.5 <= v / before <= 2.0: j = k; break
            if j is None:
                ok = False; bad[t] = f"{d0.date()} {rt.loc[d0]*100:+.0f} %, kommer inte tillbaka inom {back_days} dagar"; break
            col[i0:j] = before; n_fix += j - i0; first = first or d0
        if ok and n_fix:
            close[t] = col; fixed[t] = f"{first.date()} ({n_fix} dag{'ar' if n_fix > 1 else ''})"
    return close, fixed, bad

# ----------------------------------------------------------------------------- modell
def cal_shift(close, months):
    """Kurs för varje aktie 'months' kalendermånader tillbaka: senaste kända stängning på eller före det datumet."""
    pf = close.ffill()
    back = close.index - pd.DateOffset(months=months)
    out = pf.reindex(back, method="ffill")
    out.index = close.index
    # ingen kurs får användas innan aktien faktiskt hade data
    first = close.apply(lambda c: c.first_valid_index())
    for t in close.columns:
        f = first[t]
        if f is not None: out.loc[back < f, t] = np.nan
    return out

def clenow_panel(close, n=90):
    """Clenow (Stocks on the Move): årstaktad lutning i en regression av log-kurs mot tid över n dagar, gånger R².
    Belönar jämn, stadig uppgång; en aktie som gjort hela sin uppgång i ett hopp får lågt R². Returneras i procent."""
    y = np.log(close.ffill()).values
    T, N = y.shape
    k = np.arange(n, dtype=float); kbar = k.mean(); sxx = ((k - kbar) ** 2).sum()
    out = np.full((T, N), np.nan)
    w1 = np.ones(n); wk = (k - kbar)[::-1]          # convolve vänder kärnan
    for j in range(N):
        c = y[:, j]
        if np.isnan(c).all(): continue
        cc = np.where(np.isnan(c), 0.0, c); ok = (~np.isnan(c)).astype(float)
        cnt = np.convolve(ok, w1, "valid")
        sy = np.convolve(cc, w1, "valid"); syy = np.convolve(cc * cc, w1, "valid"); sxy = np.convolve(cc, wk, "valid")
        ybar = sy / n
        syy_c = syy - n * ybar ** 2
        slope = sxy / sxx
        with np.errstate(divide="ignore", invalid="ignore"):
            r2 = np.where(syy_c > 0, (sxy ** 2) / (sxx * syy_c), 0.0)
        val = (np.exp(slope * 252) - 1) * 100 * r2
        val[cnt < n] = np.nan
        out[n - 1:, j] = val
    return pd.DataFrame(out, index=close.index, columns=close.columns)

def r2_panel(close, n=120):
    """R² i regressionen av ln(kurs) mot tid över n dagar – hur jämn trenden är (0–1). Test T10 2026-11-23."""
    y = np.log(close.ffill()).values; T, N = y.shape
    k = np.arange(n, dtype=float); kbar = k.mean(); sxx = ((k - kbar) ** 2).sum()
    out = np.full((T, N), np.nan); w1 = np.ones(n); wk = (k - kbar)[::-1]
    for j in range(N):
        c = y[:, j]
        if np.isnan(c).all(): continue
        cc = np.where(np.isnan(c), 0.0, c); ok = (~np.isnan(c)).astype(float)
        cnt = np.convolve(ok, w1, "valid"); sy = np.convolve(cc, w1, "valid"); syy = np.convolve(cc * cc, w1, "valid"); sxy = np.convolve(cc, wk, "valid")
        syy_c = syy - n * (sy / n) ** 2
        with np.errstate(divide="ignore", invalid="ignore"):
            r2 = np.where(syy_c > 0, (sxy ** 2) / (sxx * syy_c), 0.0)
        r2[cnt < n] = np.nan; out[n - 1:, j] = r2
    return pd.DataFrame(out, index=close.index, columns=close.columns)

def score_panel(close, vol, cfg):
    """Poäng per aktie och dag, räknad BARA på data till och med dagen i fråga.

    Allt bygger på rullande fönster i pandas, som per definition inte ser framåt.
    """
    r = close.pct_change()
    MP = {"min_periods": None}
    # 12-1: avkastning från t-252 till t-21 (senaste månaden utelämnad)
    m121 = (close.shift(cfg["skip"]) / close.shift(cfg["look"]) - 1) * 100
    if cfg.get("m121_cal"):
        # 12-1 i KALENDERMÅNADER (paritetstest 2026-11-05): senaste stängning på eller före samma datum 1 resp. 12 månader
        # tillbaka. Oberoende av vilka handelsdagar panelen råkar innehålla – samma definition går att använda i appen.
        p1, p12 = cal_shift(close, 1), cal_shift(close, 12)
        m121 = (p1 / p12 - 1) * 100
    mw = cfg.get("mom_win", "12-1")
    if mw == "6-1":
        m121 = (close.shift(21) / close.shift(126) - 1) * 100
    elif mw == "3-1":
        m121 = (close.shift(21) / close.shift(63) - 1) * 100
    elif mw == "mix":                                   # hälften 12-1, hälften 6-1 (6-1 skalad till årstakt)
        m61 = (close.shift(21) / close.shift(126) - 1) * 100
        m121 = 0.5 * m121 + 0.5 * m61 * 2
    elif mw == "weighted":                              # nyare månader väger tyngre: 2 × senaste halvåret + 1 × äldre
        m_old = (close.shift(126) / close.shift(252) - 1) * 100    # mån 12→6
        m_new = (close.shift(21) / close.shift(126) - 1) * 100     # mån 6→1
        m121 = (m_old + 2 * m_new) / 3 * 2
    elif mw == "12-1 kräver 6-1>0":                     # 12-1, men bara om aktien också stigit senaste halvåret
        m61 = (close.shift(21) / close.shift(126) - 1) * 100
        m121 = m121.where(m61 > 0, 0)
    rank_m = m121                                   # måttet som ger momentumpoäng och avgör lika poäng
    if cfg.get("mom_measure") == "clenow":
        # Test 2026-11-05 (--smooth2): Clenow-lutning × R² ersätter 12-1 i poäng och avgörare. Hållregeln (§ 6) läser fortfarande 12-1.
        rank_m = clenow_panel(close, cfg.get("clenow_n", 90))
    if cfg["voladj"]:
        ann = r.rolling(126, min_periods=90).std() * math.sqrt(252) * 100
        vam = m121 / ann.replace(0, np.nan)
        mom = ((vam + 0.30) / 2.30).clip(0, 1) * 100
    elif cfg.get("mom_measure") == "clenow":
        mom = (rank_m / cfg.get("mom_cap", 60)).clip(0, 1) * 100
    else:
        # Taket (test 2026-11-04): allt över mom_cap % 12-1 får full poäng. Med 60 % (här) eller 80 % (skannern)
        # ligger 30–40 aktier på exakt samma poäng i toppen, och ordningen mellan dem avgörs av listordningen.
        mom = (m121 / cfg.get("mom_cap", 60)).clip(0, 1) * 100
        if cfg.get("mom_rank"):                           # percentilrankning i stället för tak: ingen tar i taket
            mom = m121.rank(axis=1, pct=True) * 100
    s50 = close.rolling(50, min_periods=40).mean()
    L = cfg.get("sma_long", 200)                       # SMA200; sma_long=180/220 i känslighetstestet 2026-11-05
    s200 = close.rolling(L, min_periods=int(L * 0.8)).mean()
    trend = ((close > s50).astype(float) * 30 + (close > s200).astype(float) * 30
             + (s50 > s200).astype(float) * 20 + (s200 > s200.shift(1)).astype(float) * 20)
    # Appens formel: mom 55 % + trend 30 % + volymbekräftelse 15 %, minus överhettning.
    # Backtestets ursprungliga: mom 65 % + trend 35 %. Vikterna styrs av cfg.
    wm, wt, wv = cfg.get("w_mom", 0.65), cfg.get("w_trend", 0.35), cfg.get("w_vol", 0.0)
    if wv > 0 and vol is not None:
        v20 = vol.rolling(20, min_periods=12).mean()
        volx = (vol / v20.replace(0, np.nan))
        volconf = (volx.clip(upper=2.5) / 2.5 * 100).fillna(50)
    else:
        volconf = 0
    sc = mom * wm + trend * wt + volconf * wv
    if cfg.get("tie_m121"):                               # lika poäng avgörs av högre 12-1 i stället för listordning
        sc = sc + rank_m.clip(lower=0) * 1e-6
    if cfg.get("tie_clenow"):                             # 2026-11-05: Clenow-måttet bara som avgörare, 12-1 ger poängen
        sc = sc + clenow_panel(close, cfg.get("clenow_n", 90)).clip(lower=0).fillna(0) * 1e-4
    if cfg.get("tie_volm"):                               # 2026-11-05: 12-1 / volatilitet som avgörare
        ann = r.rolling(126, min_periods=90).std() * math.sqrt(252) * 100
        sc = sc + (m121 / ann.replace(0, np.nan)).clip(lower=0).fillna(0) * 1e-4
    if cfg.get("heat"):
        # Överhettningsavdrag: straffa aktier som rusat senaste månaden eller har hög RSI.
        r1m = (close / close.shift(21) - 1) * 100
        heat = (r1m - 25).clip(lower=0) / 25 * 15
        up = r.clip(lower=0).rolling(14, min_periods=10).mean()
        dn = (-r.clip(upper=0)).rolling(14, min_periods=10).mean()
        rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        heat = heat + (rsi - 80).clip(lower=0) / 20 * 10
        if cfg.get("heat_app"):
            # Skannerns formel (paritetstest 2026-11-05): månadsavdraget tak 15 vid +50 %, RSI-avdraget fullt redan vid 95
            heat = ((r1m - 25) / 25).clip(0, 1) * 15 + ((rsi - 80) / 15).clip(0, 1) * 10
            sc = (sc - heat.fillna(0)).clip(lower=0)
        else:
            sc = sc - heat.clip(upper=25).fillna(0)
    # Jämnhet (test 2026-11-04, "frog in the pan", Da/Gurun/Warachka 2014): andel positiva dagar och största
    # nedgång INOM 12-1-fönstret (t-252 → t-21). En uppgång i små steg fortsätter oftare än en i två rapporthopp.
    if cfg.get("tie_smooth") or cfg.get("smooth_w") or cfg.get("dd_max"):
        smooth = (r > 0).rolling(231, min_periods=180).mean().shift(cfg["skip"])
        if cfg.get("tie_smooth"):
            sc = sc + smooth.fillna(0.5) * 1e-2                 # lika poäng → jämnast uppgång först
        if cfg.get("smooth_w"):
            sc = sc + cfg["smooth_w"] * ((smooth - 0.5) / 0.1).clip(-2, 2).fillna(0)   # ± smooth_w × 2 poäng
        if cfg.get("dd_max"):
            rm = close.rolling(231, min_periods=180).max()
            mdd = (1 - close / rm).rolling(231, min_periods=180).max().shift(cfg["skip"]) * 100
            sc = sc.where(~(mdd > cfg["dd_max"]))              # bort med dem som fallit mer än dd_max % inom fönstret
    if cfg.get("gap_max"):
        # Clenows gapfilter (test 2026-11-05): ingen köpkandidat som haft en dagsrörelse större än gap_max % inom fönstret
        big = (r.abs() > cfg["gap_max"] / 100.0).astype(float).rolling(cfg.get("clenow_n", 90), min_periods=1).max() > 0
        sc = sc.where(~big)
    if cfg.get("sector_cap"):
        pass   # sektortaket hanteras vid urvalet, inte i poängen
    if cfg["trendfilter"]:
        sc = sc.where(close > s200)                      # bara aktier över sitt långa medelvärde
    if cfg["minturnover"] > 0 and vol is not None:
        turn = (close * vol).rolling(20, min_periods=12).mean()
        sc = sc.where(turn >= cfg["minturnover"])       # OBS: i lokal valuta (3 M USD för USA, 3 M pence för London)
    if cfg.get("entry_dates"):
        # Överlevnadsbias (test 2026-11-05, --survive): en aktie får vara köpkandidat först från den dag den togs in i sitt index.
        # Dagens indexmedlemmar togs ofta in EFTER en stor uppgång; utan maskering får backtestet köpa den uppgången.
        for t, d0 in cfg["entry_dates"].items():
            if t in sc.columns: sc.loc[sc.index < d0, t] = np.nan
    if cfg.get("minadv_sek", 0) > 0 and vol is not None:
        # Likviditetsgolv i KRONOR (test 2026-11-05, --minadv): samma gräns för alla marknader.
        turn_sek = (close * vol).rolling(20, min_periods=12).mean() * pd.Series({t: fx_sek(t) for t in close.columns})
        sc = sc.where(turn_sek >= cfg["minadv_sek"])
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
    elif freq == "Q1":                                   # kvartal förskjutet en månad (jan/apr/jul/okt)
        sh = idx - pd.DateOffset(months=1); key = [sh.year, sh.quarter]
    elif freq in ("Mmid", "Qmid"):                       # första handelsdagen på/efter den 15:e, en per månad
        s2 = pd.Series(idx, index=idx); s2 = s2[s2.dt.day >= 15]
        out = list(s2.groupby([s2.dt.year, s2.dt.month]).first())
        return [d for d in out if freq == "Mmid" or d.month in (3, 6, 9, 12)]
    elif freq == "H": key = [idx.year, (idx.month - 1) // 6]
    else:             key = [idx.year]
    return list(s.groupby(key).last())

def decay_panels(close, vol, cfg):
    """Tre mått på att momentum håller på att ta slut, till skillnad från att ha tagit slut.

    obv   – on balance volume: volymen adderas när kursen stiger och dras bort när den
            faller. Faller OBV medan kursen står still köps aktien på tunn handel och
            säljs på tjock, alltså att köpstyrkan mattas av.
    ath   – avstånd till högsta notering, och hur det avståndet rört sig senaste månaden.
    lower – antal veckor i rad med lägre toppar ("ner ner upp ner ner upp").
    """
    out = {}
    if cfg.get("exit_obv") or cfg.get("buy_obv"):
        sgn = np.sign(close.diff()).fillna(0)
        obv = (sgn * vol.fillna(0)).cumsum()
        out["obv_slope"] = obv.diff(20) / obv.rolling(60, min_periods=30).std().replace(0, np.nan)
    if cfg.get("exit_ath") or cfg.get("buy_ath"):
        ath = close.cummax()
        gap = (ath - close) / ath.replace(0, np.nan) * 100      # procent under högsta notering
        out["ath_gap"] = gap
        out["ath_worse"] = gap - gap.shift(21)                   # hur mycket sämre på en månad
    if cfg.get("exit_lower") or cfg.get("buy_lower"):
        hi5 = close.rolling(5, min_periods=3).max()
        low = (hi5 < hi5.shift(5)).astype(float)
        out["lower_tops"] = low.rolling(30, min_periods=10).sum() / 6.0   # veckor med lägre topp
    return out

def exit_panels(close, cfg):
    if cfg.get("stop_atr", 0) > 0 and not cfg.get("exit_atr"): cfg = dict(cfg, exit_atr=99)   # bara för att få ATR-panelen
    if cfg.get("core") and not cfg.get("exit_sma200"): cfg = dict(cfg, exit_sma200=True, _core_sma=True)
    """Nivåer för de utgångar som inte är ett fast procentstopp."""
    out = {}
    if cfg.get("exit_sma50"):
        out["sma50"] = close.rolling(50, min_periods=40).mean()
    if cfg.get("exit_sma200"):
        L = cfg.get("sma_long", 200)
        out["sma200"] = close.rolling(L, min_periods=int(L * 0.8)).mean()
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

# Ungefärlig växelkurs till kronor per handelsvaluta (2026-11-05). Används BARA för likviditetsgolv och
# slippage, där ±10 % i kursen saknar betydelse. Avkastningen räknas som tidigare i lokal valuta.
# Tickersuffix → valuta följer Yahoo: .L noteras i pence.
FX_SEK = {"SEK": 1.0, "NOK": 0.96, "DKK": 1.47, "EUR": 11.0, "USD": 10.0, "GBp": 0.126, "CHF": 11.5, "CAD": 7.3}
_SFX_CCY = {".ST": "SEK", ".OL": "NOK", ".CO": "DKK", ".HE": "EUR", ".DE": "EUR", ".PA": "EUR", ".AS": "EUR", ".BR": "EUR",
            ".MC": "EUR", ".MI": "EUR", ".VI": "EUR", ".LS": "EUR", ".IR": "EUR", ".L": "GBp", ".SW": "CHF", ".TO": "CAD"}
def fx_sek(ticker):
    for s, c in _SFX_CCY.items():
        if ticker.endswith(s): return FX_SEK[c]
    return FX_SEK["USD"] if "." not in ticker else FX_SEK["EUR"]

def slippage(amount_sek, adv_sek, cfg):
    """Marknadspåverkan per sida som andel av beloppet (test 2026-11-05, --slip).

    Kvadratrotsmodell: bas + k × sqrt(post / dagsomsättning), tak slip_cap. Med k = 2 % kostar en post på
    1 % av dagsomsättningen ca 0,25 %, 10 % ca 0,7 %. Saknas omsättningsdata antas taket."""
    if not cfg.get("slip"): return 0.0
    base, k, cap = cfg.get("slip_base", 0.0005), cfg.get("slip_k", 0.02), cfg.get("slip_cap", 0.03)
    if adv_sek is None or adv_sek != adv_sek or adv_sek <= 0: return cap
    return min(cap, base + k * math.sqrt(max(0.0, amount_sek) / adv_sek))

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

LAST_DIAG = {"snap": [], "exits": []}   # diagnostik från senaste run() med cfg["diag"] (2026-11-04)

def run(close, vol, cfg, vix=None):
    LAST_DIAG["snap"] = []; LAST_DIAG["exits"] = []
    """Simulerar strategin. Returnerar kapitalkurva och nyckeltal."""
    sc, _ = score_panel(close, vol, cfg)
    # ---- Tester 2026-11-23 (TESTPLAN_3.0 T9/T10) ----
    SIG60 = close.pct_change().rolling(60, min_periods=40).std() * math.sqrt(252)
    if cfg.get("vol_cap"):          # T9: aktier med årsvolatilitet (60 d) över taket får inte köpas
        sc = sc.where(~(SIG60 > cfg["vol_cap"]))
    if cfg.get("tie_mode"):         # T10: avgörare vid (nästan) lika poäng – jämnaste eller lugnaste först i stället för brantaste
        if cfg["tie_mode"] == "r2":
            sc = sc + r2_panel(close, cfg.get("clenow_n", 120)).fillna(0) * 1e-3
        elif cfg["tie_mode"] == "lowvol":
            sc = sc + (1.0 / SIG60.replace(0, np.nan)).clip(upper=20).fillna(0) * 1e-4
    EX = exit_panels(close, cfg)
    DK = decay_panels(close, vol, cfg)
    RSI = None
    if cfg.get("buy_rsi", 0) > 0:
        rr_ = close.pct_change()
        up_ = rr_.clip(lower=0).rolling(14, min_periods=10).mean()
        dn_ = (-rr_.clip(upper=0)).rolling(14, min_periods=10).mean()
        RSI = 100 - 100 / (1 + up_ / dn_.replace(0, np.nan))
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
    CAL = (cal_shift(close, 1), cal_shift(close, 12)) if cfg.get("m121_cal") else None
    last_day = {t: (alive[t][alive[t]].index[-1] if alive[t].any() else None) for t in close.columns}
    dates = close.index
    rebs = set(period_ends(dates, cfg.get("reb", "M")))
    start_i = max(cfg["look"] + 5, 210)
    if start_i >= len(dates) - 5:
        raise SystemExit("För kort historik för att backtesta.")

    expo = vix_exposure(vix, close.index, cfg)
    # ---- Strategitester 2026-11-18 (TESTPLAN § 9) ----
    # T2 riskbudget: bredd = andel av universumet över SMA200 (gårdagens värde, så att ingen framtidsläckage sker)
    BUYGATE = None
    if cfg.get("breadth_cut"):
        s200_ = close.rolling(200, min_periods=160).mean()
        br = ((close > s200_).sum(axis=1) / close.notna().sum(axis=1).replace(0, np.nan)).shift(1).fillna(1.0)
        if cfg.get("breadth_expo") is None:
            BUYGATE = br >= cfg["breadth_cut"]                      # inga nya köp när bredden är under gränsen
        else:
            lo_, hi_ = cfg["breadth_cut"], cfg.get("breadth_full", cfg["breadth_cut"])
            f = []; st_ = 1.0
            for v in br.values:                                      # hysteres: halv exponering under lo_, full igen över hi_
                if v < lo_: st_ = cfg["breadth_expo"]
                elif v >= hi_: st_ = 1.0
                f.append(st_)
            expo = expo * pd.Series(f, index=close.index)
    # T3 sektortak: sektor per aktie från data.json (skannerns Yahoo-sektor); saknas sektor räknas aktien som egen sektor
    SECTOR = None
    if cfg.get("sector_cap"):
        try:
            _dj = json.load(open("data.json", encoding="utf-8")).get("stocks") or {}
            SECTOR = {t: (_dj.get(t) or {}).get("sec") or f"?{t}" for t in close.columns}
        except Exception:
            SECTOR = {t: f"?{t}" for t in close.columns}
    def sector_ok(t):
        if not SECTOR: return True
        k = SECTOR.get(t); return sum(1 for x in hold if SECTOR.get(x) == k) < cfg["sector_cap"]
    # T4 omvänd volatilitet: vikt ∝ 1/σ60 (årstakt), normerad mot medianen bland innehav + kandidat, tak 2× och golv 0,5×
    SIG = close.pct_change().rolling(60, min_periods=40).std() * math.sqrt(252) if cfg.get("inv_vol") else None
    def ivf(t, i_, among):
        if SIG is None: return 1.0
        try:
            sg = SIG.iat[i_, close.columns.get_loc(t)]
            ref = [SIG.iat[i_, close.columns.get_loc(x)] for x in among]
            ref = [x for x in ref if x == x and x > 0]
            if sg != sg or sg <= 0 or not ref: return 1.0
            return float(min(2.0, max(0.5, (sorted(ref)[len(ref) // 2]) / sg)))
        except Exception:
            return 1.0
    # ---- Tester 3.0 (2026-11-23, TESTPLAN_3.0 T7): förtur för rekyl i upptrend ----
    # En kandidat "rekylerar" när 1-månadsavkastningen ligger mellan pb_lo och pb_hi (t.ex. −25 till −10 %).
    # pb_pref="pool": rekylaktier inom topp pb_pool sorteras först; "bonus": rekylaktier får pb_bonus poäng.
    R1M = (close / close.shift(21) - 1) if cfg.get("pb_pref") else None
    def pb_adj(row, i_):
        if R1M is None or not len(row): return row
        r_ = R1M.iloc[i_].reindex(row.index)
        m_ = (r_ >= cfg.get("pb_lo", -0.25)) & (r_ <= cfg.get("pb_hi", -0.10))
        row = row.copy()
        if cfg["pb_pref"] == "pool":
            rk0 = row.rank(ascending=False, method="first")
            m_ = m_ & (rk0 <= cfg.get("pb_pool", 30))
            row[m_] = row[m_] + 1000.0
        else:
            row[m_] = row[m_] + cfg.get("pb_bonus", 5.0)
        return row
    n_eff_now = lambda: n_eff
    cash, hold, hold_days = 1.0, {}, []
    cut_days = 0          # hold: ticker -> {"sh": andel av kapitalet, "px": inköpskurs, "peak": högsta}
    eq, eqdates, trades = [], [], []
    PORT = cfg.get("port_sek", 10_000_000.0)      # portföljens storlek i kronor, styr poststorleken
    # Dagsomsättning (20 d) i kronor per aktie – för slippage och rapport. Räknas bara när den behövs.
    ADV = ((close * vol).rolling(20, min_periods=12).mean() * pd.Series({t: fx_sek(t) for t in close.columns})
           if (cfg.get("slip") or cfg.get("report_adv")) and vol is not None else None)
    X = LAST_DIAG["x"] = {"cost": 0.0, "slip": 0.0, "traded": 0.0, "adv_buy": [], "vol_buy": []}   # enheter: andel av startkapital
    cur_i = [0]
    def fee(val_units, t, side):
        """val_units är positionens andel av kapitalet (1.0 = hela). Returnerar samma enhet.
        Innehåller courtage, växling, stämpelskatt och (med --slip) marknadspåverkan."""
        amt = max(0.0, val_units) * PORT
        if amt <= 0: return 0.0
        c = real_cost(amt, t, side, cfg) / PORT
        s = 0.0
        if cfg.get("slip") and ADV is not None:
            adv = ADV.iat[cur_i[0], close.columns.get_loc(t)]
            s = val_units * slippage(amt, adv, cfg)
        X["cost"] += c; X["slip"] += s; X["traded"] += val_units
        return c + s
    def fpx(t, i, px_sig):
        """Fyllnadskurs. Standard: signaldagens stängning (så backtestet mätts t.o.m. 2026-11-04).
        --t1: nästa handelsdags stängning – signalen finns först efter stängning och kan handlas tidigast dagen efter."""
        if not cfg.get("t1"): return px_sig
        j = i + 1
        if j >= len(dates): return px_sig
        q = pxf.iat[j, close.columns.get_loc(t)]
        return q if q == q and q > 0 else px_sig

    def value(i):
        p = pxf.iloc[i]
        v = cash
        for t, h in hold.items():
            q = p.get(t)
            v += h["sh"] * (q if q == q else h["px"])      # saknad kurs: använd senast kända
        return v

    for i in range(start_i, len(dates)):
        d, p = dates[i], pxf.iloc[i]; cur_i[0] = i
        # avnoterade bolag säljs till sista kända kurs i stället för att tyst försvinna
        for t in list(hold):
            ld = last_day.get(t)
            if ld is not None and d > ld:
                q = p.get(t)
                cash += hold[t]["sh"] * (q if q == q else hold[t]["px"]) - fee(hold[t]["sh"] * (q if q == q else hold[t]["px"]), t, "sell")
                trades.append(("delisted", t, d)); del hold[t]
        # --- vinstmål: sälj en andel när aktien stigit X % över inköpskursen ---
        if cfg.get("tp", 0) > 0:
            for t in list(hold):
                h = hold[t]
                if h.get("tp_done"): continue
                px = p.get(t)
                if px != px or px <= 0: continue
                if px >= h["px"] * (1 + cfg["tp"] / 100.0):
                    frac = cfg.get("tp_sell", 50) / 100.0
                    sh = h["sh"] * frac; px = fpx(t, i, px)
                    cash += sh * px - fee(sh * px, t, "sell")
                    h["sh"] -= sh
                    h["tp_done"] = True          # utlöses bara en gång per position
                    trades.append(("tp", t, d))
                    if h["sh"] <= 1e-9:
                        hold_days.append(i - h["i"]); del hold[t]

        # --- stopp vid stöd (dokumentets förslag): räknas om varje dag för varje innehav ---
        if cfg.get("stop_mode") == "support" and i >= 130:
            for t in list(hold):
                h = hold[t]; px = p.get(t)
                if px != px or px <= 0: continue
                ci = close.columns.get_loc(t)
                win = close.iloc[max(0, i - cfg.get("sr_win", 130)):i + 1, ci].dropna()
                if len(win) < 40: continue
                v = win.values.tolist()
                # lokala bottnar (±5 dagar) under aktuell kurs, klustrade inom 2 %
                lows = [v[k] for k in range(5, len(v) - 5) if v[k] == min(v[k - 5:k + 6]) and v[k] < px * 0.995]
                zones = []
                for q in sorted(lows):
                    for z in zones:
                        if abs(q / z[0] - 1) <= 0.02: z[1] += 1; break
                    else: zones.append([q, 1])
                good = [z for z in zones if z[1] >= cfg.get("sr_min_touch", 2)]
                if not good: continue
                sup = max(z[0] for z in good)                        # närmaste stöd under kursen
                atr = float(close.iloc[max(0, i - 14):i + 1, ci].diff().abs().mean()) or px * 0.02
                lvl = sup - cfg.get("sr_buf_atr", 0.4) * atr
                # säkerhetsnät: aldrig lösare än regelns procentstopp (om ett sådant finns)
                floor_pct = max(h["px"], h["peak"]) * (1 - cfg["stop"] / 100.0) if cfg["stop"] > 0 else 0
                h["sr_stop"] = max(lvl, floor_pct)

        # --- dagliga stopp (gäller mellan ombalanseringarna) ---
        core_mode = cfg.get("core")          # kärnlager: absoluta hållvillkor, prövade månadsvis
        month_end = (i + 1 >= len(dates)) or (dates[i + 1].month != d.month)
        if cfg.get("chk_mid"):      # förskjuten månadsprövning (robusthetstest 2026-11-05): första handelsdagen på/efter den 15:e
            month_end = d.day >= 15 and (i == 0 or dates[i - 1].day < 15 or dates[i - 1].month != d.month)
        for t in list(hold):
            px = p.get(t)
            if px != px:   # NaN
                continue
            h = hold[t]
            h["peak"] = max(h["peak"], px)
            base = h["peak"] if cfg["trail"] else h["px"]
            why = None
            held_days = i - h["i"]
            if cfg.get("min_hold", 0) and held_days < cfg["min_hold"]:
                continue                                        # inga säljregler de första N dagarna
            if core_mode:
                # Kärnan: sälj bara om 12-1 < 0 eller kurs < SMA200.
                # core="month": prövas vid månadsskifte. core="daily": prövas varje dag, men villkoret
                # måste gälla confirm_days dagar i rad – ett dagsfall räcker inte.
                ci = close.columns.get_loc(t)
                # Gapregel (2026-11-02): föll aktien >= gap_sell % på en enskild dag säljs den dagen efter,
                # oavsett månadsskifte. Testar om post-earnings-driften är värd att undvika.
                gs = cfg.get("gap_sell", 0)
                if gs and i > 0:
                    if h.get("gapday") is not None and i > h["gapday"]:
                        pf = fpx(t, i, px)
                        cash += h["sh"] * pf - fee(h["sh"] * pf, t, "sell"); hold_days.append(held_days); trades.append(("gap", t, d)); del hold[t]
                        continue
                    prev = close.iat[i - 1, ci]
                    if prev == prev and px <= prev * (1 - gs / 100.0) and h.get("gapday") is None: h["gapday"] = i
                _sk, _lk = cfg.get("skip", 21), cfg.get("look", 252)
                if CAL is not None:
                    _a, _b = CAL[0].iat[i, ci], CAL[1].iat[i, ci]
                    m121 = (_a / _b - 1) if (_a == _a and _b == _b and _b > 0) else 1.0
                else:
                    m121 = (close.iat[max(0, i - _sk), ci] / close.iat[max(0, i - _lk), ci] - 1) if i >= _lk else 1.0
                s200 = EX["sma200"].iat[i, ci] if "sma200" in EX else np.nan
                buf = 1 - cfg.get("sma_buf", 0) / 100.0
                use_m, use_s = not cfg.get("hold_no_mom"), not cfg.get("hold_no_sma")   # krasch-testet stänger av villkor
                bad = "12-1 negativt" if (use_m and m121 <= 0) else ("under SMA200" if (use_s and s200 == s200 and px < s200 * buf) else None)
                if core_mode == "month":
                    if month_end and cfg.get("sma_two"):
                        # T6 (2026-11-23): SMA200-brott måste gälla två månadsskiften i rad; 12-1-villkoret säljer direkt som förut
                        if bad == "under SMA200":
                            if h.get("sbad"): why = bad
                            else: h["sbad"] = True
                        else:
                            h["sbad"] = False
                            if bad: why = bad
                    elif month_end and bad: why = bad
                    # Trötta innehav (test 2026-11-04): sälj det som legat utanför topp stale_rank i stale_days dagar
                    # i rad (prövat vid månadsskifte), även om hållregeln gäller. Diagnostik samlas i LAST_DIAG.
                    if month_end and (cfg.get("stale_rank") or cfg.get("diag")):
                        if "_rk" not in locals() or _rk_i != i:
                            _row = sc.iloc[i].dropna(); _rk = _row.rank(ascending=False, method="first"); _rk_i = i
                        rk_t = float(_rk.get(t, 1e9))
                        if cfg.get("diag"):
                            LAST_DIAG["snap"].append((d, t, rk_t, held_days, px / h["px"] - 1))
                        sr = cfg.get("stale_rank")
                        if sr:
                            if rk_t > sr:
                                if h.get("stale_since") is None: h["stale_since"] = i
                            else: h["stale_since"] = None
                            if not why and h.get("stale_since") is not None and i - h["stale_since"] >= cfg.get("stale_days", 252):
                                why = "trött"
                else:
                    if bad: h["cbad"] = h.get("cbad", 0) + 1
                    else: h["cbad"] = 0
                    if h["cbad"] >= cfg.get("confirm_days", 5): why = bad
                if why:
                    if cfg.get("diag"): LAST_DIAG["exits"].append((d, t, why, held_days, px / h["px"] - 1))
                    pf = fpx(t, i, px)
                    cash += h["sh"] * pf - fee(h["sh"] * pf, t, "sell"); hold_days.append(held_days); trades.append((why, t, d)); del hold[t]
                continue
            if cfg.get("stop_mode") == "support" and h.get("sr_stop"):
                if px <= h["sr_stop"]: why = "stopp vid stöd"
            elif cfg.get("stop_atr", 0) > 0:
                a = EX["atr"].iat[i, close.columns.get_loc(t)] if "atr" in EX else np.nan
                lvl = base - cfg["stop_atr"] * a if a == a else base * (1 - cfg["stop"] / 100)
                if px <= lvl:
                    h["below"] = h.get("below", 0) + 1
                    if h["below"] >= cfg.get("confirm_days", 1): why = "stop"
                else: h["below"] = 0
            elif cfg["stop"] > 0 and px <= base * (1 - cfg["stop"] / 100):
                h["below"] = h.get("below", 0) + 1
                if h["below"] >= cfg.get("confirm_days", 1): why = "stop"
            elif cfg["stop"] > 0: h["below"] = 0
            if why is None and "sma50" in EX:
                lv = EX["sma50"].iat[i, close.columns.get_loc(t)]
                if lv == lv and px < lv: why = "sma50"
            if why is None and "sma200" in EX:
                lv = EX["sma200"].iat[i, close.columns.get_loc(t)]
                if lv == lv and px < lv: why = "sma200"
            if why is None and "atr" in EX and cfg.get("exit_atr"):
                a = EX["atr"].iat[i, close.columns.get_loc(t)]
                if a == a and px <= h["peak"] - cfg["exit_atr"] * a: why = "atr"
            ci = close.columns.get_loc(t)
            if why is None and cfg.get("exit_obv"):
                v = DK["obv_slope"].iat[i, ci]
                if v == v and v < cfg["exit_obv"]: why = "obv"
            if why is None and cfg.get("exit_ath"):
                g = DK["ath_gap"].iat[i, ci]; w = DK["ath_worse"].iat[i, ci]
                if g == g and w == w and g > cfg["exit_ath"] and w > cfg.get("ath_worse_min", 5): why = "ath"
            if why is None and cfg.get("exit_lower"):
                v = DK["lower_tops"].iat[i, ci]
                if v == v and v >= cfg["exit_lower"]: why = "lägre toppar"
            if why is None and cfg.get("exit_score", 0) > 0:
                s_ = sc.iat[i, close.columns.get_loc(t)]
                if s_ == s_ and s_ < cfg["exit_score"]: why = "svag poäng"
            if why:
                pf = fpx(t, i, px)
                cash += h["sh"] * pf - fee(h["sh"] * pf, t, "sell")
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
                    px = fpx(t, i, px)
                    cash += hold[t]["sh"] * px - fee(hold[t]["sh"] * px, t, "sell"); hold_days.append(i - hold[t]["i"])
                    trades.append(("vix", t, d))
                del hold[t]

        # --- påfyllning: sker löpande, inte bara vid ombalansering ---
        #     (stopp och bandutgångar sker dagligen, så kapitalet ska inte ligga i kassa
        #      till nästa kvartalsskifte – appen föreslår ersättare direkt)
        _rf = cfg.get("refill_days", 1)   # 1 = varje dag, 5 = veckovis, 21 = var 21:a handelsdag (fast rutnät)
        if _rf == "M": _rf = 1 if month_end else 10**9   # "M" (paritetstest 2026-11-05): bara på månadens sista handelsdag, som appen
        # Fast rutnät (i % n) kan sammanfalla systematiskt med ombalanseringsrytmen och
        # skapa falska toppar. Med jitter sprids tillfällena slumpmässigt kring intervallet.
        if cfg.get("refill_jitter") and _rf > 1:
            _due = (i % _rf) == (hash((i // _rf, cfg.get("seed", 0))) % _rf)
        else:
            _due = _rf <= 1 or i % _rf == 0
        def entry_ok(t):
            """Inträdesfilter (G6, 2026-11-03): köp bara om kursen ligger över SMA200 – samma villkor som hållregeln."""
            if not cfg.get("entry_sma200"): return True
            try:
                ci_ = close.columns.get_loc(t); px_ = p.get(t); s_ = EX["sma200"].iat[i, ci_]
                return px_ == px_ and s_ == s_ and px_ > s_
            except Exception:
                return True
        def wfac(t, rk_):
            """Vikt efter styrka (test 2026-11-04): rank_wt=k ger topp-rankad 1+k/2 × målvikt och lägst 1−k/2 ×.
            rank_top=m ger i stället de m högst rankade dubbel vikt (trappsteg). 0 = likavikt (validerat)."""
            k = cfg.get("rank_wt", 0); m = cfg.get("rank_top", 0); n_ = cfg["n"]
            if not k and not m: return 1.0
            try: pos = min(float(rk_.get(t, n_)), n_)
            except Exception: pos = n_
            if m: return 2.0 if pos <= m else 1.0
            return 1.0 + k * (0.5 - (pos - 1) / max(1, n_ - 1))
        if cfg.get("refill") and d not in rebs and len(hold) < n_eff and _due and (BUYGATE is None or bool(BUYGATE.iloc[i])):
            row_r = sc.iloc[i].dropna()
            mk_r = cfg.get("mkt", "all")
            if mk_r == "nordic":
                row_r = row_r[[t for t in row_r.index if any(t.endswith(x) for x in NORDIC)]]
            elif mk_r == "us":
                row_r = row_r[[t for t in row_r.index if "." not in t]]
            if len(row_r) >= cfg["n"]:
                row_r = pb_adj(row_r, i)
                rk = row_r.rank(ascending=False, method="first")
                free = [t for t in rk[rk <= cfg["n"]].sort_values().index if t not in hold and entry_ok(t)]
                tot_r = value(i); nb_ = 0
                for t in free:
                    if nb_ >= n_eff - len(hold): break
                    if not sector_ok(t): continue
                    px = p.get(t)
                    if px != px or px <= 0: continue
                    nb_ += 1
                    target = tot_r / max(1, n_eff) * wfac(t, rk) * ivf(t, i, list(hold) + [t])
                    if cash < target * 0.2: break
                    amt = min(target, cash); px = fpx(t, i, px)
                    sh = (amt - fee(amt, t, "buy")) / px
                    cash -= amt
                    if ADV is not None: X["adv_buy"].append(float(ADV.iat[i, close.columns.get_loc(t)]))
                    _v = SIG60.iat[i, close.columns.get_loc(t)]; X["vol_buy"].append(float(_v)) if _v == _v else None
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
                row = pb_adj(row, i)
                rank = row.rank(ascending=False, method="first")
                top = set(rank[rank <= cfg["n"]].index)
                if cfg.get("core"):
                    # Kärnan: hållvillkoren prövas i den dagliga loopen med bekräftelse. Ombalanseringen
                    # får INTE sälja innehav som bara saknar rankning den här dagen (läckage upptäckt 2026-10-23).
                    keep = set(t for t in hold if t not in rank.index or rank[t] <= cfg["band_out"])
                else:
                    keep = set(t for t in hold if rank.get(t, 1e9) <= cfg["band_out"])
                # Rotation (test 2026-10-22): sälj den sämst rankade andelen av innehaven vid varje
                # kvartalsskifte, oavsett om de fortfarande stiger, och låt påfyllningen ta in topp-N.
                # Detta är "MINSKA i den svagare för att KÖPA den starkare" i mjuk form.
                if cfg.get("rot_frac") and hold:
                    k_rot = max(1, int(round(len(hold) * cfg["rot_frac"])))
                    worst = sorted(hold, key=lambda x: rank.get(x, 1e9), reverse=True)[:k_rot]
                    if cfg.get("rot_top"):   # bara om det finns en ersättare i den allra översta rankningen
                        fresh = [t for t in rank[rank <= cfg["rot_top"]].index if t not in hold]
                        worst = worst[:len(fresh)]
                    keep -= set(w for w in worst if rank.get(w, 1e9) > cfg["n"])   # aldrig en som står i topp-N
                # sälj allt som ramlat ur bandet
                for t in list(hold):
                    if t not in keep:
                        px = p.get(t)
                        if px == px:
                            px = fpx(t, i, px)
                            cash += hold[t]["sh"] * px - fee(hold[t]["sh"] * px, t, "sell"); hold_days.append(i - hold[t]["i"]); trades.append(("band", t, d))
                        del hold[t]
                # Öka vid stöd: innehav som just nu testar ett stöd får 1,5× vikt i den här ombalanseringen
                if cfg.get("add_at_support") and hold:
                    for t in hold:
                        ci = close.columns.get_loc(t); px = p.get(t)
                        if px != px or i < 130: hold[t]["w"] = 1.0; continue
                        win = close.iloc[i - 130:i + 1, ci].dropna().values.tolist()
                        lows = [win[k] for k in range(5, len(win) - 5) if win[k] == min(win[k - 5:k + 6])]
                        near = any(abs(px / q - 1) <= 0.03 for q in lows if sum(1 for z in lows if abs(z / q - 1) <= 0.02) >= 2)
                        s200 = EX["sma200"].iat[i, ci] if "sma200" in EX else np.nan
                        hold[t]["w"] = 1.5 if (near and s200 == s200 and px > s200) else 1.0
                # återställ vikterna till lika stora (trimma vinnare, fyll på eftersläntrare)
                if cfg.get("reweight") and hold:
                    tot = value(i); wsum = sum(h.get("w", 1.0) for h in hold.values()) or 1
                    ntr = cfg.get("tranche", 0)      # trancherad ombalansering: en tredjedel av innehaven per månad
                    for t, h in list(hold.items()):
                        if ntr and (sum(ord(c) for c in t) % ntr) != (d.month % ntr): continue
                        px = p.get(t)
                        if px != px or px <= 0: continue
                        target = tot * h.get("w", 1.0) / max(cfg["n"], wsum) if cfg.get("add_at_support") else tot / cfg["n"]
                        if cfg.get("rank_wt") or cfg.get("rank_top"):   # vikt efter styrka: målvikterna normeras över innehaven
                            fs_ = sum(wfac(x, rank) for x in hold) or 1.0
                            target = tot * wfac(t, rank) / fs_ * (len(hold) / cfg["n"])
                        if SIG is not None:                             # omvänd volatilitet: målvikterna normeras över innehaven
                            fv_ = sum(ivf(x, i, list(hold)) for x in hold) or 1.0
                            target = tot * ivf(t, i, list(hold)) / fv_ * (len(hold) / cfg["n"])
                        cur = h["sh"] * px
                        if cur > target * (1 + cfg.get("rw_band", 0.25)):
                            sell = (cur - target) / px; pf = fpx(t, i, px)
                            h["sh"] -= sell; cash += sell * pf - fee(sell * pf, t, "sell"); trades.append(("trim", t, d))
                        elif cur < target * (1 - cfg.get("rw_band", 0.25)) and cash > 0:
                            # Påfyllning av eftersläntrare (test 2026-11-04): rw_up=False stänger av den helt,
                            # rw_up_rank=N fyller bara på innehav som själva rankas topp N. Frågan är om
                            # pengarna gör mer nytta i nya topp-20-aktier än i innehav som halkat efter.
                            if cfg.get("rw_up", True) is False: continue
                            if cfg.get("rw_up_rank") and rank.get(t, 1e9) > cfg["rw_up_rank"]: continue
                            add = min(target - cur, cash); pf = fpx(t, i, px)
                            h["sh"] += (add - fee(add, t, "buy")) / pf; cash -= add; trades.append(("add", t, d))
                # fyll på upp till n med de högst rankade
                room = n_eff_now() - len(hold)
                if room > 0:
                    wide = cfg.get("cand_width", 1.0)
                    lim = cfg["n"] if not cfg.get("cost_adj") else min(cfg["band_out"], int(cfg["n"] * wide))
                    top_sel = set(rank[rank <= lim].index)
                    cand_all = [t for t in rank.sort_values().index if t not in hold and t in top_sel and entry_ok(t)]
                    if cfg.get("cost_adj"):
                        # Väg poäng mot kostnad i stället för att sortera bort dyra aktier.
                        # slope = procents avkastning per poäng, mätt på historiken.
                        # Kostnaden ska räknas på den FAKTISKA poststorleken i just denna
                        # portfölj. Vid 2 500 kr kostar en USA-affär 2,85 % – inte 0,71 %.
                        post_sek = max(1.0, value(i) / max(1, n_eff_now()) * PORT)
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
                    if cfg.get("buy_obv") or cfg.get("buy_ath") or cfg.get("buy_lower"):
                        def fresh(t):
                            ci2 = close.columns.get_loc(t)
                            if cfg.get("buy_obv"):
                                v = DK["obv_slope"].iat[i, ci2]
                                if v == v and v < cfg["buy_obv"]: return False
                            if cfg.get("buy_ath"):
                                g = DK["ath_gap"].iat[i, ci2]
                                if g == g and g > cfg["buy_ath"]: return False
                            if cfg.get("buy_lower"):
                                v = DK["lower_tops"].iat[i, ci2]
                                if v == v and v >= cfg["buy_lower"]: return False
                            return True
                        cand_all = [t for t in cand_all if fresh(t)]
                    if RSI is not None:
                        rr = RSI.iloc[i]
                        cand_all = [t for t in cand_all
                                    if not (rr.get(t) == rr.get(t) and rr.get(t) > cfg["buy_rsi"])]
                    if BUYGATE is not None and not bool(BUYGATE.iloc[i]): cand_all = []
                    picks = []
                    for t in cand_all:
                        if len(picks) >= room: break
                        if sector_ok(t): picks.append(t)
                    tot = value(i)
                    for t in picks:
                        px = p.get(t)
                        if px != px or px <= 0: continue
                        target = tot / max(1, n_eff) * wfac(t, rank) * ivf(t, i, list(hold) + [t])
                        if cash < target * 0.2: break
                        amt = min(target, cash); px = fpx(t, i, px)
                        sh = (amt - fee(amt, t, "buy")) / px
                        cash -= amt
                        if ADV is not None: X["adv_buy"].append(float(ADV.iat[i, close.columns.get_loc(t)]))
                        _v = SIG60.iat[i, close.columns.get_loc(t)]; X["vol_buy"].append(float(_v)) if _v == _v else None
                        hold[t] = {"sh": sh, "px": px, "peak": px, "i": i}; trades.append(("buy", t, d))
                # Extra platser (test 2026-11-04): kassa som blivit över när eftersläntrare INTE fylls på
                # får köpa nya topp-N-aktier utöver målantalet, högst rw_extra stycken.
                if cfg.get("rw_extra") and cash > 0 and len(row) >= cfg["n"]:
                    tot_x = value(i); target = tot_x / max(1, cfg["n"])
                    extra = [t for t in rank.sort_values().index if rank[t] <= cfg["n"] and t not in hold and entry_ok(t)]
                    for t in extra:
                        if len(hold) >= cfg["n"] + cfg["rw_extra"] or cash < target * 0.5: break
                        px = p.get(t)
                        if px != px or px <= 0: continue
                        amt = min(target, cash); px = fpx(t, i, px)
                        sh = (amt - fee(amt, t, "buy")) / px
                        cash -= amt
                        hold[t] = {"sh": sh, "px": px, "peak": px, "i": i}; trades.append(("buy", t, d))
        eq.append(value(i)); eqdates.append(d)

    e = pd.Series(eq, index=pd.DatetimeIndex(eqdates)).dropna()
    if cfg.get("vix_cut"): print(f"      nedskalad exponering {cut_days} av {len(expo)} dagar")
    return e, trades, hold_days

# ----------------------------------------------------------------------------- mått
def yearly(e):
    """Avkastning år för år. Visar hur ojämn en koncentrerad strategi faktiskt är."""
    if len(e) < 30: return {}
    out = {}
    for y, grp in e.groupby(e.index.year):
        if len(grp) > 20:
            out[int(y)] = round(float(grp.iloc[-1] / grp.iloc[0] - 1) * 100, 1)
    return out

def stats(e, trades, years, hold_days=None, x=None):
    if len(e) < 30: return {}
    r = e.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if not len(r) or e.iloc[0] <= 0: return {}
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1
    vol = r.std() * math.sqrt(252)
    dd = (e / e.cummax() - 1).min()
    buys = sum(1 for t in trades if t[0] == "buy")
    extra = {}
    if x:   # friktion och likviditet (2026-11-05): LAST_DIAG["x"] från run()
        avg = float(e.mean()) or 1.0
        extra = {"omsättning_ggr_per_år": round(x["traded"] / avg / years, 2),
                 "friktion_%_per_år": round((x["cost"] + x["slip"]) / avg / years * 100, 2),
                 "varav_slippage_%_per_år": round(x["slip"] / avg / years * 100, 2),
                 "median_ADV_vid_köp_Mkr": (round(float(np.nanmedian(x["adv_buy"])) / 1e6, 1) if x["adv_buy"] else None)}
    return {"slutkapital": round(float(e.iloc[-1]), 3),
            "CAGR_%": round(cagr * 100, 2),
            "volatilitet_%": round(float(vol) * 100, 1),
            "Sharpe": round(float(cagr / vol), 2) if vol > 0 else None,
            "max_nedgång_%": round(float(dd) * 100, 1),
            "Calmar": round(float(-cagr / dd), 2) if dd < 0 else None,
            **extra,
            "affärer": len(trades), "köp": buys,
            "köp_per_år": round(buys / years, 1),
            "innehavstid_dagar": (round(float(np.median(hold_days)) * 1.4) if hold_days else None),
            "längsta_innehav_dagar": (round(max(hold_days) * 1.4) if hold_days else None),
            "utgångar": {k: sum(1 for t in trades if t[0] == k)
                         for k in ("stop", "stopp vid stöd", "sma50", "sma200", "atr", "svag poäng", "band", "12-1 negativt", "under SMA200", "gap") 
                         if any(t[0] == k for t in trades)}}

def benchmark(close, years, start_i=210):
    sub = close.iloc[start_i:]
    r = sub.pct_change().mean(axis=1).fillna(0)       # likaviktat index av universumet
    e = (1 + r).cumprod()
    return e, stats(e, [], years)

# ----------------------------------------------------------------------------- main
DEF = dict(mom_cap=150, tie_m121=True,   # vald 2026-11-04 (--tie, X4): tak 150 %, lika poäng avgörs av 12-1
           n=10, band_out=20, stop=12, trail=True, look=252, skip=21, voladj=True,
           trendfilter=True, minturnover=3e6, cost_bps=15, reb="M", reweight=False, rw_band=0.25,
           exit_sma50=False, exit_sma200=False, exit_atr=0, exit_score=0,
           vix_cut=0, vix_q=0.80, vix_win=504,
           port_sek=10_000_000.0, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False,
           pullback=0, pb_win=60, pb_mode='pct', pb_max=1.0,
           exit_obv=0, exit_ath=0, ath_worse_min=5, exit_lower=0,
           buy_obv=0, buy_ath=0, buy_lower=0,
           mkt='all', cost_filter=False, cost_adj=False, slope=0.0, refill=True, refill_days=1, refill_jitter=False, seed=0, cand_width=2.5,
           stop_mode='pct', sr_win=130, sr_min_touch=2, sr_buf_atr=0.4, mom_win='12-1',
           core=False, min_hold=0, confirm_days=1, stop_atr=0, sma_buf=0,
           tp=0, tp_sell=50,
           w_mom=0.65, w_trend=0.35, w_vol=0.0, heat=False, pos_cap=0, buy_rsi=0)

# ----------------------------------------------------------------------------- tester 3.0 (2026-11-23)
def after_sale(close, vol, cfg_a):
    """T5 (TESTPLAN_3.0): vad hände med aktierna vi sålde på hållregeln, jämfört med dem som köptes i stället?
    Kör A med diagnostik, mäter avkastning 1/3/6/12 mån efter försäljningen (fyllnad T+1) för den sålda aktien,
    ersättarna (köp samma dag eller inom fem handelsdagar) och universumets likaviktade snitt."""
    cfg = dict(DEF); cfg.update(cfg_a); cfg["diag"] = True
    e, tr, hd = run(close, vol, cfg)
    exits = list(LAST_DIAG["exits"])
    idx = close.index; pos = {d: i for i, d in enumerate(idx)}; pxf = close.ffill()
    col = {t: k for k, t in enumerate(close.columns)}
    buys = {}
    for k_, t_, d_ in tr:
        if k_ == "buy": buys.setdefault(pos[d_], []).append(t_)
    buy_pos = {}
    for i_, ts in buys.items():
        for t_ in ts: buy_pos.setdefault(t_, []).append(i_)
    s200 = close.rolling(200, min_periods=160).mean()
    br = ((close > s200).sum(axis=1) / close.notna().sum(axis=1).replace(0, np.nan))
    H = (21, 63, 126, 252)
    U = {}
    for h in H:
        f = pxf.shift(-(h + 1)) / pxf.shift(-1) - 1
        U[h] = f.where((f > -0.95) & (f < 10)).mean(axis=1)
    def fwd(t, i, h):
        j, k = i + 1, i + 1 + h
        if k >= len(idx): return None
        a, b = pxf.iat[j, col[t]], pxf.iat[k, col[t]]
        return (b / a - 1) if (a == a and b == b and a > 0) else None
    rows = []
    for d_, t_, why, held, ret in exits:
        i = pos[d_]
        rep, dsel = [], 0
        for di in range(0, 6):
            rep = buys.get(i + di, [])
            if rep: dsel = di; break
        r = {"datum": str(d_.date()), "ticker": t_, "orsak": why, "hållen_d": int(held), "bredd": round(float(br.iat[i]), 2),
             "återköpt_3m": any(0 < q - i <= 63 for q in buy_pos.get(t_, []))}
        for h in H:
            sv = fwd(t_, i, h)
            rv = [x for x in (fwd(b, i + dsel, h) for b in rep) if x is not None]
            r[f"s{h}"] = sv; r[f"r{h}"] = (sum(rv) / len(rv)) if rv else None
            u = U[h].iat[i]; r[f"u{h}"] = float(u) if u == u else None
        rows.append(r)
    def summ(rs, lab):
        print(f"\n   {lab}: {len(rs)} försäljningar · återköpta inom 3 mån {sum(r['återköpt_3m'] for r in rs) / max(1, len(rs)) * 100:.0f} %"
              f" · vid bredd < 30 %: {sum(1 for r in rs if r['bredd'] < 0.30) / max(1, len(rs)) * 100:.0f} %")
        out = {"n": len(rs), "återköpt_3m_%": round(sum(r['återköpt_3m'] for r in rs) / max(1, len(rs)) * 100, 1)}
        print(f"   {'horisont':<10}{'såld median':>12}{'ersättare':>12}{'såld−ers. median':>18}{'andel såld>ers.':>17}{'såld−univ. median':>19}")
        for h in H:
            pr = [(r[f"s{h}"], r[f"r{h}"]) for r in rs if r[f"s{h}"] is not None and r[f"r{h}"] is not None]
            pu = [r[f"s{h}"] - r[f"u{h}"] for r in rs if r[f"s{h}"] is not None and r[f"u{h}"] is not None]
            if not pr: continue
            diff = sorted(a - b for a, b in pr); sv = sorted(a for a, _ in pr); rv = sorted(b for _, b in pr)
            med = lambda v: v[len(v) // 2]
            win = sum(1 for a, b in pr if a > b) / len(pr)
            out[f"{h}d"] = {"n": len(pr), "såld_median_%": round(med(sv) * 100, 1), "ersättare_median_%": round(med(rv) * 100, 1),
                            "diff_median_pe": round(med(diff) * 100, 1), "diff_medel_pe": round(sum(diff) / len(diff) * 100, 1),
                            "andel_såld_bättre_%": round(win * 100, 1), "mot_univ_median_pe": round(med(sorted(pu)) * 100, 1) if pu else None}
            o = out[f"{h}d"]
            print(f"   {h:>3} d     {o['såld_median_%']:>10} %{o['ersättare_median_%']:>10} %{o['diff_median_pe']:>15} pe{o['andel_såld_bättre_%']:>15} %{str(o['mot_univ_median_pe']):>16} pe")
        return out
    print(f"\n########## T5 Efterförsäljningsanalys (A = modell 2.0) ##########")
    print("   Tolkningsregel (skriven före körning): T6 körs om sålda aktier i median slår ersättaren över 3 OCH 6 mån")
    print("   OCH mer än 25 % av dem köps tillbaka inom 3 mån. Annars stryks T6.")
    res = {"alla": summ(rows, "Alla försäljningar på hållregeln"),
           "SMA200": summ([r for r in rows if r["orsak"] == "under SMA200"], "Orsak: under SMA200"),
           "12-1": summ([r for r in rows if r["orsak"] == "12-1 negativt"], "Orsak: 12-1 negativt")}
    per_year = {}
    for r in rows: per_year[r["datum"][:4]] = per_year.get(r["datum"][:4], 0) + 1
    print(f"   Försäljningar per år: {per_year}")
    a_ = res["alla"]
    trig = bool(a_.get("63d") and a_.get("126d") and a_["63d"]["diff_median_pe"] > 0 and a_["126d"]["diff_median_pe"] > 0 and a_["återköpt_3m_%"] > 25)
    print(f"   => T6 {'KÖRS' if trig else 'STRYKS'} (3 mån {a_.get('63d', {}).get('diff_median_pe')} pe, 6 mån {a_.get('126d', {}).get('diff_median_pe')} pe, återköpta {a_['återköpt_3m_%']} %)")
    res["per_år"] = per_year; res["t6_körs"] = trig; res["rader"] = rows
    return res, trig, e

def sector_sale(close, vol, eqA, thr_list=(0.20, 0.25, 0.30), holds=(126, 252), stops=(None, 0.25), modes=("abs", "rel")):
    """T8 (TESTPLAN_3.0): sektor-rea. Vid månadsskifte: sektorns medianaktie ≥ thr under 52-veckorshögsta medan universumets median
    är högst 10 % under → köp de 5 som fallit mest bland sektorns 15 mest omsatta (20-dagars omsättning i kronor, känd den dagen),
    lika vikt, fyllnad T+1, håll H dagar, 0,2 % kostnad per sida. Ingen ny signal i samma sektor förrän innehavet avslutats.
    Jämförs över exakt samma fönster med universumets likaviktade snitt, sektorns likaviktade snitt och modell 2.0 (A)."""
    try:
        _dj = json.load(open("data.json", encoding="utf-8")).get("stocks") or {}
        SEC = {t: (_dj.get(t) or {}).get("sec") for t in close.columns}
    except Exception:
        SEC = {}
    pxf = close.ffill(); idx = close.index
    hi = close.rolling(252, min_periods=200).max(); dd = close / hi - 1
    turn = (close * vol).rolling(20, min_periods=12).mean()
    liq = turn >= 3e6
    adv_sek = turn * pd.Series({t: fx_sek(t) for t in close.columns})
    ends = [idx.get_loc(d) for d in period_ends(idx, "M")]
    ends = [i for i in ends if i >= 260]
    eqA = eqA.reindex(idx).ffill()
    res = {}
    print(f"\n########## T8 Sektor-rea (fristående, priskomponenten av reafickan) ##########")
    print("   Tolkningsregel (skriven före körning): aktuell som regelstyrd reaficka bara om avkastningen 12 mån efter signal slår universumet")
    print("   i minst två tredjedelar av signalerna OCH snittet slår modell 2.0 över samma fönster. Mittvarianten (25 %, 12 mån, ingen stopp) avgör.")
    print("   Har den färre än 8 avslutade signaler är utfallet 'för få signaler'; då redovisas den relativa varianten (sektorns median minst x pe")
    print("   sämre än universumets, inget krav på universumet) som indikation, med samma regel.")
    print("   OBS: universumet är dagens indexmedlemmar – överlevnadsbias gynnar just rea-köp (de som föll och aldrig återhämtade sig saknas).")
    um_all = [float(dd.iloc[i][liq.iloc[i]].median()) for i in ends]
    um_all = [x for x in um_all if x == x]
    print(f"   Universumets median under 52v-högsta vid månadsskiften: median {sorted(um_all)[len(um_all) // 2] * 100:.1f} %,"
          f" andel månader med högst 10 % under: {sum(1 for x in um_all if x >= -0.10) / max(1, len(um_all)) * 100:.0f} %")
    for mode in modes:
      for thr in thr_list:
        for H in holds:
            for stp in stops:
                if mode == "rel" and stp: continue
                key = f"T8 fall {int(thr*100)} %, håll {H // 21} mån" + (f", stopp −{int(stp*100)} %" if stp else "") + (", relativt" if mode == "rel" else "")
                cool = {}; ev = []
                for i in ends:
                    j, k = i + 1, i + 1 + H
                    if j >= len(idx): continue
                    lq = liq.iloc[i]; ddi = dd.iloc[i]
                    um = ddi[lq].median()
                    if not (um == um) or (mode == "abs" and um < -0.10): continue
                    bysec = {}
                    for t in ddi.index[lq.values & ddi.notna().values]:
                        sct = SEC.get(t)
                        if sct: bysec.setdefault(sct, []).append(t)
                    for sct, ts in bysec.items():
                        if len(ts) < 15 or cool.get(sct, -1) >= i: continue
                        sm = float(ddi[ts].median())
                        if (mode == "abs" and sm > -thr) or (mode == "rel" and sm - um > -thr): continue
                        big = adv_sek.iloc[i][ts].sort_values(ascending=False).index[:15]
                        pick = list(ddi[big].sort_values().index[:5])
                        done = k < len(idx); kk = min(k, len(idx) - 1)
                        cool[sct] = kk
                        def ret(t):
                            c_ = close.columns.get_loc(t)
                            a = pxf.iat[j, c_]
                            if not (a == a and a > 0): return None
                            path = pxf.iloc[j:kk + 1, c_]
                            if stp:
                                hit = path[path <= a * (1 - stp)]
                                if len(hit):
                                    q = path.index.get_loc(hit.index[0])
                                    b = path.iat[min(q + 1, len(path) - 1)]
                                    return b / a - 1 - 0.004
                            b = path.iat[-1]
                            return (b / a - 1 - 0.004) if b == b else None
                        pr = [x for x in (ret(t) for t in pick) if x is not None]
                        if not pr: continue
                        def ew(ts_):
                            a = pxf.iloc[j][ts_]; b = pxf.iloc[kk][ts_]; r_ = (b / a - 1).replace([np.inf, -np.inf], np.nan)
                            r_ = r_[(r_ > -0.95) & (r_ < 10)]; return float(r_.mean())
                        uni = ew(list(ddi.index[lq.values]))
                        secr = ew(ts)
                        ar = float(eqA.iat[kk] / eqA.iat[j] - 1) if (eqA.iat[j] == eqA.iat[j] and eqA.iat[j] > 0) else None
                        ev.append({"datum": str(idx[i].date()), "sektor": sct, "sektor_median_fall_%": round(sm * 100, 1),
                                   "univ_median_fall_%": round(float(um) * 100, 1), "aktier": pick, "klar": done,
                                   "rea_%": round(sum(pr) / len(pr) * 100, 1), "univ_%": round(uni * 100, 1), "sektor_%": round(secr * 100, 1),
                                   "A_%": round(ar * 100, 1) if ar is not None else None})
                fin = [x for x in ev if x["klar"]]
                if fin:
                    m = lambda k_: round(sum(x[k_] for x in fin if x[k_] is not None) / max(1, sum(1 for x in fin if x[k_] is not None)), 1)
                    hu = sum(1 for x in fin if x["rea_%"] > x["univ_%"]) / len(fin)
                    ha = sum(1 for x in fin if x["A_%"] is not None and x["rea_%"] > x["A_%"]) / len(fin)
                    st = {"signaler": len(ev), "avslutade": len(fin), "rea_medel_%": m("rea_%"), "univ_medel_%": m("univ_%"),
                          "sektor_medel_%": m("sektor_%"), "A_medel_%": m("A_%"), "slår_univ_%": round(hu * 100), "slår_A_%": round(ha * 100),
                          "rea_median_%": sorted(x["rea_%"] for x in fin)[len(fin) // 2], "händelser": ev}
                    print(f"   {key:<38} signaler {len(ev):>3} (avslutade {len(fin):>3})  rea {st['rea_medel_%']:>6} % (median {st['rea_median_%']})  univ {st['univ_medel_%']:>6} %"
                          f"  sektor {st['sektor_medel_%']:>6} %  2.0 {st['A_medel_%']:>6} %  slår univ {st['slår_univ_%']:>3} %  slår 2.0 {st['slår_A_%']:>3} %")
                else:
                    st = {"signaler": len(ev), "avslutade": 0, "händelser": ev}
                    print(f"   {key:<38} signaler {len(ev)} – inga avslutade")
                res[key] = st
    mid = res.get("T8 fall 25 %, håll 12 mån", {})
    if (mid.get("avslutade") or 0) < 8:
        print(f"\n   => Mittvarianten har {mid.get('avslutade') or 0} avslutade signaler – för få. Indikation från relativa varianten:")
        mid = res.get("T8 fall 25 %, håll 12 mån, relativt", {})
    if (mid.get("avslutade") or 0) < 8:
        print(f"   => För få signaler ({mid.get('avslutade') or 0} avslutade) – inget utfall; T8 kan inte avgöras på tio års data.")
        res["_utfall"] = None
    elif mid.get("avslutade"):
        ok = mid["slår_univ_%"] >= 66.7 and (mid["rea_medel_%"] or -99) > (mid["A_medel_%"] or 99)
        print(f"\n   => Mittvarianten {'KLARAR' if ok else 'KLARAR INTE'} tolkningsregeln (slår universum i {mid['slår_univ_%']} % av fallen, snitt {mid['rea_medel_%']} % mot 2.0 {mid['A_medel_%']} %)")
        res["_utfall"] = bool(ok)
    for kname in ("T8 fall 25 %, håll 12 mån", "T8 fall 25 %, håll 12 mån, relativt"):
        print(f"   Händelser ({kname}):")
        for x in res.get(kname, {}).get("händelser", []):
            print(f"     {x['datum']}  {x['sektor']:<24} sektor {x['sektor_median_fall_%']:>6} %  univ {x['univ_median_fall_%']:>6} %  {', '.join(x['aktier'])}"
                  f"  → rea {x['rea_%']} %  univ {x['univ_%']} %  sektor {x['sektor_%']} %  2.0 {x['A_%']} %{'' if x['klar'] else '  (pågår)'}")
    return res

def fib_study(close):
    """T11 (TESTPLAN_3.0, 2026-11-25): rekylzonen som köpläge – händelsestudie vid varje månadsslut.
    Samma zon som skannerns fib_zone: topp = högsta stängning 252 d, botten = lägsta före toppen, uppgång >= 30 %.
    Grupper ZON (61,8–78,6 % rekyl), TOPP (högst 5 % under toppen), ZON+/TOPP+ (dessutom över SMA200).
    Köp T+1; överavkastning mot universumets likaviktade snitt 21/63/126 d."""
    C = close.values.astype(float); idx = close.index; n, m = C.shape
    s200 = close.rolling(200, min_periods=160).mean().values
    me = [i for i in range(252, n - 1) if idx[i].month != idx[i + 1].month]
    H = (21, 63, 126)
    pxf = close.ffill().values
    U = {}
    for h in H:
        f = np.full((n, m), np.nan)
        f[:n - h - 1] = pxf[1 + h:] / pxf[1:n - h] - 1
        f[(f <= -0.95) | (f >= 10)] = np.nan
        U[h] = f
    Um = {h: np.nanmean(U[h], axis=1) for h in H}
    rows = []
    for i in me:
        W = C[i - 251:i + 1]
        ok = np.isfinite(W).sum(axis=0) >= 200
        Wf = np.where(np.isfinite(W), W, -np.inf); ih = Wf.argmax(axis=0)
        hi = W[ih, np.arange(m)]
        Wl = np.where(np.isfinite(W), W, np.inf); cm = np.minimum.accumulate(Wl, axis=0)
        lo = cm[np.maximum(ih - 1, 0), np.arange(m)]
        px = C[i]
        valid = ok & (ih >= 10) & np.isfinite(px) & (lo > 0) & np.isfinite(lo) & (hi / np.where(lo > 0, lo, np.nan) - 1 >= 0.30)
        rng = hi - lo; z618 = hi - .618 * rng; z786 = hi - .786 * rng
        zon = valid & (px <= z618) & (px >= z786)
        top = valid & (px >= 0.95 * hi)
        up = np.isfinite(s200[i]) & (px > s200[i])
        for g, mask in (("ZON", zon), ("TOPP", top), ("ZON+", zon & up), ("TOPP+", top & up)):
            for j in np.where(mask)[0]:
                r = {"g": g, "d": idx[i], "t": close.columns[j]}
                for h in H:
                    v = U[h][i, j]; r[h] = (v - Um[h][i]) if (v == v and Um[h][i] == Um[h][i]) else None
                if g in ("ZON", "ZON+"):
                    k = min(n, i + 1 + 63); fut = C[i + 1:k, j]
                    r["bruten"] = bool(np.nanmin(fut) < z786[j]) if np.isfinite(fut).any() else None
                rows.append(r)
    mid = idx[len(idx) // 2]
    def st(rs, h):
        v = sorted(x[h] for x in rs if x[h] is not None)
        if not v: return None
        return {"n": len(v), "median_pe": round(v[len(v) // 2] * 100, 2), "medel_pe": round(sum(v) / len(v) * 100, 2),
                "andel_slår_univ_%": round(sum(1 for x in v if x > 0) / len(v) * 100, 1)}
    out = {}
    print("\n########## T11 Rekylzonen som köpläge (händelsestudie, månadsslut, köp T+1) ##########")
    print("   Överavkastning mot universumets likaviktade snitt, procentenheter. Regel: TESTPLAN_3.0 T11.")
    for per, f_ in (("Hela", lambda d: True), ("Första halvan", lambda d: d < mid), ("Andra halvan", lambda d: d >= mid)):
        out[per] = {}
        print(f"\n   {per}")
        print(f"   {'grupp':<7}{'n (6 mån)':>10}" + "".join(f"{f'median {h} d':>14}{f'medel {h} d':>13}{f'slår {h} d':>11}" for h in H) + f"{'bruten 3 mån':>14}")
        for g in ("ZON", "TOPP", "ZON+", "TOPP+"):
            rs = [x for x in rows if x["g"] == g and f_(x["d"])]
            o = {h: st(rs, h) for h in H}
            br = [x["bruten"] for x in rs if x.get("bruten") is not None]
            o["bruten_3m_%"] = round(sum(br) / len(br) * 100, 1) if br else None
            out[per][g] = {str(h): o[h] for h in H}; out[per][g]["bruten_3m_%"] = o["bruten_3m_%"]
            line = f"   {g:<7}{(o[126] or {}).get('n', 0):>10}"
            for h in H:
                q = o[h] or {}
                line += f"{str(q.get('median_pe', '–')):>14}{str(q.get('medel_pe', '–')):>13}{str(q.get('andel_slår_univ_%', '–')):>10}%"
            line += f"{str(o['bruten_3m_%'] if o['bruten_3m_%'] is not None else '–'):>13}"
            print(line)
    gZ, gT = out["Hela"]["ZON"], out["Hela"]["TOPP"]
    med = lambda o, h: (o.get(str(h)) or {}).get("median_pe")
    c1 = med(gZ, 126) is not None and med(gT, 126) is not None and med(gZ, 126) > 0 and med(gZ, 126) - med(gT, 126) >= 2
    c2 = med(gZ, 63) is not None and med(gT, 63) is not None and med(gZ, 63) > med(gT, 63)
    c3 = ((gZ.get("126") or {}).get("medel_pe") or -1) > 0
    c4 = all(med(out[p]["ZON"], 126) is not None and med(out[p]["TOPP"], 126) is not None and med(out[p]["ZON"], 126) > med(out[p]["TOPP"], 126) for p in ("Första halvan", "Andra halvan"))
    c1, c2, c3, c4 = bool(c1), bool(c2), bool(c3), bool(c4)
    ok = c1 and c2 and c3 and c4
    print(f"\n   Regel: 1) 6 mån ZON>0 och ≥ TOPP+2 pe: {c1} · 2) 3 mån ZON>TOPP: {c2} · 3) medel 6 mån ZON>0: {c3} · 4) båda halvor: {c4}")
    print(f"   => T11 {'KLARAR regeln – strategikörning (reaficka) är motiverad' if ok else 'KLARAR INTE regeln – zonen förblir information'}")
    out["regel"] = {"1": c1, "2": c2, "3": c3, "4": c4, "klarar": ok}
    return out

def pt_study(close, path="pt_hist.json.gz"):
    """T12 (TESTPLAN_3.0, 2026-11-25): riktkursernas uppsida och revideringar – händelsestudie vid månadsslut, köp T+1.
    Konsensus = median av varje analyshus senaste riktkurs (365 d, minst 3 analyshus); revidering = höjda minus sänkta 30 d."""
    import gzip
    J = json.load(gzip.open(path, "rt", encoding="utf-8"))
    data = {t: r for t, r in J["data"].items() if t in close.columns}
    print(f"\n   Riktkurshistorik: {len(data)} av {close.shape[1]} aktier i backtestets universum · täckning {J.get('täckning')}")
    idx = close.index; n = len(idx); col = {t: k for k, t in enumerate(close.columns)}
    C = close.values.astype(float); pxf = close.ffill().values
    s200 = close.rolling(200, min_periods=160).mean().values
    me = [i for i in range(200, n - 1) if idx[i].month != idx[i + 1].month]
    H = (63, 126, 252)
    def fwd(i, j, h):
        k = i + 1 + h
        if k >= n: return None
        a, b = pxf[i + 1, j], pxf[k, j]
        if not (a == a and b == b and a > 0): return None
        r = b / a - 1
        return r if -0.95 < r < 10 else None
    recs = []   # (månad, t, uppsida, nfirm, upp, ned, över200, f63, f126, f252)
    for t, rows in data.items():
        j = col[t]; p = 0; last = {}
        for i in me:
            D = idx[i].strftime("%Y-%m-%d")
            while p < len(rows) and rows[p][0] <= D:
                d, firm, pt, ppt = rows[p]
                prev = last.get(firm)
                last[firm] = (d, pt, ppt if ppt else (prev[1] if prev else None)); p += 1
            lo365 = (idx[i] - pd.Timedelta(days=365)).strftime("%Y-%m-%d"); lo30 = (idx[i] - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            cur = [v for v in last.values() if v[0] > lo365]
            if len(cur) < 3: continue
            px = C[i, j]
            if not (px == px and px > 0): continue
            cons = float(np.median([v[1] for v in cur])); q = cons / px
            if q < 0.25 or q > 4: continue
            up = sum(1 for v in cur if v[0] > lo30 and v[2] and v[1] > v[2] * 1.0001)
            dn = sum(1 for v in cur if v[0] > lo30 and v[2] and v[1] < v[2] * 0.9999)
            a200 = bool(s200[i, j] == s200[i, j] and px > s200[i, j])
            recs.append((i, t, q - 1, len(cur), up, dn, a200) + tuple(fwd(i, j, h) for h in H))
    df = pd.DataFrame(recs, columns=["i", "t", "ups", "nf", "up", "dn", "a200", 63, 126, 252])
    if df.empty:
        print("   Ingen data."); return {"fel": "ingen data"}
    cnt = df.groupby("i").size(); good = cnt[cnt >= 200].index
    print(f"   Månader med konsensus: {len(cnt)}; med minst 200 aktier: {len(good)} (från {idx[good[0]].date() if len(good) else '–'})")
    df = df[df.i.isin(good)].copy()
    indik = len(good) < 36
    for h in H:
        m = df.groupby("i")[h].transform("mean"); df[f"x{h}"] = df[h] - m
    df["dec"] = df.groupby("i")["ups"].transform(lambda v: pd.qcut(v.rank(method="first"), 10, labels=False) + 1)
    mid = good[len(good) // 2] if len(good) else 0
    def st(d, h):
        v = d[f"x{h}"].dropna()
        if not len(v): return None
        return {"n": int(len(v)), "median_pe": round(float(v.median()) * 100, 2), "medel_pe": round(float(v.mean()) * 100, 2),
                "andel_%": round(float((v > 0).mean()) * 100, 1)}
    G = {f"U{k}": (lambda d, k=k: d[d.dec == k]) for k in range(1, 11)}
    G["U10 (≥5 analyshus)"] = lambda d: d[(d.dec == 10) & (d.nf >= 5)]
    G["REV+"] = lambda d: d[(d.up >= 2) & (d.dn == 0)]
    G["REV−"] = lambda d: d[(d.dn >= 2) & (d.up == 0)]
    G["REV+ över SMA200"] = lambda d: d[(d.up >= 2) & (d.dn == 0) & d.a200]
    out = {"månader": int(len(good)), "indikation": indik}
    for per, f_ in (("Hela", lambda d: d), ("Första halvan", lambda d: d[d.i < mid]), ("Andra halvan", lambda d: d[d.i >= mid])):
        sub = f_(df); out[per] = {}
        print(f"\n   {per}" + (f" (till {idx[mid].date()})" if per == "Första halvan" else f" (från {idx[mid].date()})" if per == "Andra halvan" else ""))
        print(f"   {'grupp':<22}{'n (6 mån)':>10}" + "".join(f"{f'median {h}d':>13}{f'medel {h}d':>12}{f'slår {h}d':>10}" for h in H))
        for g, fn in G.items():
            d = fn(sub); o = {str(h): st(d, h) for h in H}; out[per][g] = o
            line = f"   {g:<22}{((o['126'] or {}).get('n', 0)):>10}"
            for h in H:
                q = o[str(h)] or {}
                line += f"{str(q.get('median_pe', '–')):>13}{str(q.get('medel_pe', '–')):>12}{str(q.get('andel_%', '–')):>9}%"
            print(line)
    med = lambda per, g, h: ((out[per][g].get(str(h)) or {}).get("median_pe"))
    def gt(a, b): return a is not None and b is not None and a > b
    A1 = (med("Hela", "U10", 126) or -99) > 2
    A2 = gt(med("Hela", "U10", 126), med("Hela", "U1", 126)) and gt(med("Hela", "U10", 252), med("Hela", "U1", 252))
    A3 = all((med(p, "U10", 126) or -99) > 0 for p in ("Första halvan", "Andra halvan"))
    Awarn = all((med(p, "U10", 126) if med(p, "U10", 126) is not None else 0) < -2 for p in ("Första halvan", "Andra halvan"))
    B1 = (med("Hela", "REV+", 63) or -99) > 1 and (med("Hela", "REV+", 126) or -99) > 1
    B2 = gt(med("Hela", "REV+", 63), med("Hela", "REV−", 63)) and gt(med("Hela", "REV+", 126), med("Hela", "REV−", 126))
    B3 = all((med(p, "REV+", 126) or -99) > 0 for p in ("Första halvan", "Andra halvan"))
    A, B = bool(A1 and A2 and A3), bool(B1 and B2 and B3)
    tag = " (INDIKATION – färre än 36 månader)" if indik else ""
    print(f"\n   Regel A (uppsida): 1) U10 > +2 pe 6 mån: {A1} · 2) U10 > U1 6 och 12 mån: {A2} · 3) U10 > 0 båda halvor: {A3} · varning (U10 < −2 båda halvor): {Awarn}")
    print(f"   => A {'KLARAR' if A else 'KLARAR INTE'}{tag}")
    print(f"   Regel B (revideringar): 1) REV+ > +1 pe 3 och 6 mån: {B1} · 2) REV+ > REV− 3 och 6 mån: {B2} · 3) REV+ > 0 båda halvor: {B3}")
    print(f"   => B {'KLARAR' if B else 'KLARAR INTE'}{tag}")
    out["regel"] = {"A": A, "A1": bool(A1), "A2": bool(A2), "A3": bool(A3), "A_varning": bool(Awarn), "B": B, "B1": bool(B1), "B2": bool(B2), "B3": bool(B3)}
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=10)
    ap.add_argument("--universe", default=None, help="fil med en ticker per rad")
    ap.add_argument("--out", default="backtest.json")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--quick", action="store_true", help="kör bara grundfallet")
    ap.add_argument("--combo", action="store_true", help="[historiskt, satellitläge, gammal basis] testa kombinationer i stället för en ändring i taget")
    ap.add_argument("--robust", action="store_true", help="[historiskt, satellitläge, gammal basis] håller vinnaren på data den inte valts på?")
    ap.add_argument("--exits", action="store_true", help="[historiskt, satellitläge, gammal basis] jämför utgångsmekanismer för swinghandel")
    ap.add_argument("--vix", action="store_true", help="[historiskt, satellitläge, gammal basis] testar om volatilitetsindex skyddar mot momentumkrascher")
    ap.add_argument("--vixindex", default="^VIX", help="^VIX, ^VXN eller ^V2TX")
    ap.add_argument("--entry", action="store_true", help="[historiskt, satellitläge, gammal basis] kostnader + poststorlek + rekyltajming")
    ap.add_argument("--mix", action="store_true", help="[historiskt, satellitläge, gammal basis] bästa kombination av antal, marknad och hålltid per portföljstorlek")
    ap.add_argument("--freq", action="store_true", help="[historiskt, satellitläge, gammal basis] hur ofta lönar det sig att handla?")
    ap.add_argument("--conc", action="store_true", help="[historiskt, satellitläge, gammal basis] få innehav, inget stopp, låt vinnare löpa")
    ap.add_argument("--decay", action="store_true", help="[historiskt, satellitläge, gammal basis] fångar volymbalans, ATH-avstånd och lägre toppar försämring i tid?")
    ap.add_argument("--tp", action="store_true", help="[historiskt, satellitläge, gammal basis] kapar vinstmålet vinnarna? (appens tp=35/tpSell=50 är aldrig testad)")
    ap.add_argument("--score", action="store_true", help="[historiskt, satellitläge, gammal basis] är appens poängmodell bättre än backtestets enklare?")
    ap.add_argument("--srstop", action="store_true", help="[historiskt, satellitläge, gammal basis] stopp strax under närmaste stöd mot fast procent")
    ap.add_argument("--momwin", action="store_true", help="[historiskt, satellitläge, gammal basis] momentumfönster: 12-1 mot 6-1, blandat, viktat")
    ap.add_argument("--core", action="store_true", help="kärnlager: absoluta hållvillkor, månadsprövning, brusfilter")
    ap.add_argument("--crash", action="store_true", help="gapsäljning efter rapportsmällar + stå kvar genom marknadskrascher")
    ap.add_argument("--stale", action="store_true", help="trötta innehav: blir svaga men stigande aktier kvar i åratal, och ska de tvingas ut?")
    ap.add_argument("--smooth", action="store_true", help="jämnhet: gynna aktier som stigit i små steg (prisbaserad kvalitet)?")
    ap.add_argument("--wt", action="store_true", help="vikt efter styrka: mer kapital i de högst rankade i stället för likavikt?")
    ap.add_argument("--tie", action="store_true", help="poängtaket: 30–40 aktier delar topp-poängen – spelar ordningen mellan dem roll?")
    ap.add_argument("--rw", action="store_true", help="likaviktning: fylla på eftersläntrare eller köpa nya topp-20-aktier?")
    ap.add_argument("--port", type=float, default=10_000_000, help="portföljstorlek i kronor")
    # 2026-11-05 (TESTPLAN_2.0.md): realistisk fyllnad och 2.0-kandidaten
    ap.add_argument("--t1", action="store_true", help="fyll alla affärer på NÄSTA handelsdags stängning i stället för signaldagens")
    ap.add_argument("--slip", action="store_true", help="marknadspåverkan: bas 5 bp + 2 % × sqrt(post/dagsomsättning), tak 3 % per sida")
    ap.add_argument("--minadv", type=float, default=0, help="likviditetsgolv i KRONOR (20-dagars dagsomsättning), samma för alla marknader")
    ap.add_argument("--mm2", action="store_true", help="2.0-kandidaten: A (T+1 + slippage) mot storbolagsuniversum B, med robusthet och känslighet")
    ap.add_argument("--smooth2", action="store_true", help="2.0 steg C: Clenow lutning×R² och riskjusterad 12-1 mot referens A (T+1 + slippage)")
    ap.add_argument("--costn", action="store_true", help="kostnadsläge: antal innehav 5/6/7/8/10 vid 60/100/150 tkr, 12-1 (P3) och Clenow 120")
    ap.add_argument("--fulln", action="store_true", help="antal innehav i fullt läge under 2.0: 15/20/25/30 vid 10 Mkr och 600 tkr, dubbla kostnader, förskjutet kvartal")
    ap.add_argument("--survive", action="store_true", help="överlevnadsbias: S&P 500-medlemmar som kandidater först från sitt inträdesdatum (Wikipedia)")
    ap.add_argument("--parity", action="store_true", help="paritet backtest↔app: köp vid månadsslut, 12-1 i kalendermånader, appens överhettningsformel")
    ap.add_argument("--strat", default="", help="strategitester 2026-11-18 (TESTPLAN § 9): gap | breadth | sector | invvol | all; 3.0 (2026-11-23): after | smabuf | pullback | sectorsale | t3; 2026-11-25: fibzone (T11), ptarget (T12)")
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
            try: v = SC.get_tickers(m)      # 2026-11-05: samma normalisering som skannern (tog tidigare råa namn → döda dubbletter)
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
        m = SC.collapse_share_classes(sorted(set(tick)))      # 2026-11-05: ADDT.ST + ADDT-B.ST = samma bolag
        tick = sorted(set(m.get(t, t) for t in tick))
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
    # Globala fyllnads- och likviditetsval (2026-11-05): gäller alla testlägen när de ges.
    if a.t1: DEF["t1"] = True
    if a.slip: DEF["slip"] = True
    if a.minadv > 0: DEF["minadv_sek"] = a.minadv
    DEF["report_adv"] = True

    comp2 = Counter(_grp(t) for t in close.columns)
    print("Nedladdat: " + ", ".join(f"{k} {v}" for k, v in sorted(comp2.items())))
    bench_e, bench = benchmark(close, years)
    print("\n=== JÄMFÖRELSE: likaviktat snitt av universumet (INTE ett riktigt index) ===")
    for k, v in bench.items(): print(f"   {k:<18}{v}")

    # ---------- Krasch (2026-11-02): rapportsmällar och marknadskrascher ----------
    if a.crash:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        M2 = dict(B, mom_measure="clenow", clenow_n=120)              # modell 2.0 (2026-11-05)
        VAR = {
            "A.  Modell 2.0 (referens): Clenow 120, håll 12-1>0 & >SMA200, månadsvis": dict(M2),
            "A1. Modell 1.0 (12-1) på samma basis, för jämförelse":   dict(B),
            "--- rapportsmällar: sälj dagen efter ett dagsfall (2.0) ---": None,
            "G1. A + gap 15 %":                                         dict(M2, gap_sell=15),
            "G2. A + gap 20 %":                                         dict(M2, gap_sell=20),
            "G3. A + gap 25 %":                                         dict(M2, gap_sell=25),
            "G4. A + gap 20 %, dubbla kostnader":                       dict(M2, gap_sell=20, **DBL),
            "G5. A + gap 20 %, kvartal förskjutet 1 mån":               dict(M2, gap_sell=20, reb="Q1"),
            "--- marknadskrascher: stå kvar i stället för att sälja (2.0) ---": None,
            "H1. Bara 12-1 > 0 som hållvillkor (utan SMA200)":          dict(M2, hold_no_sma=True),
            "H2. Bara kurs > SMA200 som hållvillkor (utan 12-1)":       dict(M2, hold_no_mom=True),
            "H3. Inga hållvillkor alls – säljer bara ner till 20 vid kvartal": dict(M2, hold_no_sma=True, hold_no_mom=True),
            "H4. H1, kvartal förskjutet 1 mån":                         dict(M2, hold_no_sma=True, reb="Q1"),
            "H5. H3, kvartal förskjutet 1 mån":                         dict(M2, hold_no_sma=True, hold_no_mom=True, reb="Q1"),
            "--- förskjuten månadsprövning: prövning och köp mitt i månaden i stället för vid månadsslut (2.0) ---": None,
            "S1. A med prövning den 15:e":                              dict(M2, chk_mid=True),
            "S2. A1 (1.0) med prövning den 15:e":                       dict(B, chk_mid=True),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        # kraschfönster: hur gick det just där det gjorde ont?
        WIN = {"2015–16 (aug 1 – feb 11)": ("2015-08-01", "2016-02-11"),
               "2018 Q4 (okt 1 – dec 24)": ("2018-10-01", "2018-12-24"),
               "2020 covid (feb 19 – mar 23)": ("2020-02-19", "2020-03-23"),
               "2020 återhämtning (mar 23 – aug 31)": ("2020-03-23", "2020-08-31"),
               "2022 björn (jan 3 – okt 12)": ("2022-01-03", "2022-10-12"),
               "2022–23 återhämtning (okt 12 – jul 31)": ("2022-10-12", "2023-07-31"),
               "2025 vår (feb 19 – apr 8)": ("2025-02-19", "2025-04-08")}
        def window(e, s0, s1):
            seg = e.loc[s0:s1]
            return round(float(seg.iloc[-1] / seg.iloc[0] - 1) * 100, 1) if len(seg) > 5 else None
        print(f"\n########## Krasch: rapportsmällar och marknadskrascher, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Beslutsregel (skriven innan körning): en regel går in bara om den slår A i CAGR OCH Sharpe,")
        print(f"   håller vid dubbla kostnader och vid förskjutet kvartalsdatum, och inte fördjupar nedgången.\n")
        print(f"   {'likaviktat snitt av universumet':<62}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}; eqs = {}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd); st["år"] = yearly(e)
                st["fönster"] = {k: window(e, s0, s1) for k, (s0, s1) in WIN.items()}
                res[nm] = st; eqs[nm] = e
                print(f"   {nm:<62}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d  utgångar {st.get('utgångar')}")
            except Exception as ex:
                print(f"   {nm:<62} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        print(f"\n   === KRASCHFÖNSTER: avkastning i procent inom varje fönster ===")
        names = [n for n in VAR if VAR[n] is not None and n in res and "fönster" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        print(f"   {'fönster':<40}" + "".join(f"{short[n]:>7}" for n in names) + f"{'snitt':>7}")
        for k, (s0, s1) in WIN.items():
            row = "".join(f"{(res[n]['fönster'][k] if res[n]['fönster'][k] is not None else '–'):>7}" for n in names)
            print(f"   {k:<40}{row}{window(be, s0, s1) if be is not None else '–':>7}")
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for n in names for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in names))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in names))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "krasch",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias och ingen fundamenta. Kraschfönstren är få – tolka som indikation."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Strategitester under 2.0 (2026-11-18, TESTPLAN § 9) ----------
    if a.strat:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        M2 = dict(B, mom_measure="clenow", clenow_n=120)              # modell 2.0
        which = a.strat.lower()
        VAR = {"A.  Modell 2.0 (referens)": dict(M2)}
        if which in ("gap", "all"):
            VAR["--- T1 gapsäljning: sälj dagen efter ett dagsfall ≥ x % ---"] = None
            for g in (22, 25, 30): VAR[f"T1-{g}. A + gap {g} %"] = dict(M2, gap_sell=g)
        if which in ("breadth", "all"):
            VAR["--- T2 riskbudget efter marknadsläge (bredd = andel av universumet över SMA200, dagen före) ---"] = None
            VAR["T2-a. Inga nya köp när bredd < 40 %"] = dict(M2, breadth_cut=0.40)
            VAR["T2-b. Inga nya köp när bredd < 30 %"] = dict(M2, breadth_cut=0.30)
            VAR["T2-c. Halv exponering (10 innehav) när bredd < 40 %, full igen över 40 %"] = dict(M2, breadth_cut=0.40, breadth_expo=0.5, breadth_full=0.40)
            VAR["T2-d. Halv exponering när bredd < 30 %, full igen först över 50 %"] = dict(M2, breadth_cut=0.30, breadth_expo=0.5, breadth_full=0.50)
        if which in ("sector", "all"):
            VAR["--- T3 sektortak: högst k av 20 innehav i samma sektor (Yahoo-sektor ur data.json) ---"] = None
            for k in (6, 8): VAR[f"T3-{k}. A + högst {k} per sektor"] = dict(M2, sector_cap=k)
        if which in ("invvol", "all"):
            VAR["--- T4 omvänd volatilitet: vikt ∝ 1/σ60, tak 2× och golv 0,5× likavikt, även vid kvartalslikaviktning ---"] = None
            VAR["T4. A + omvänd volatilitet"] = dict(M2, inv_vol=True)
        # ---- Tester 3.0 (2026-11-23, TESTPLAN_3.0): T5 efterförsäljning, T6 SMA200-buffert (villkorad), T7 rekyl i upptrend, T8 sektor-rea ----
        T5 = None
        T6V = {"--- T6 buffert på SMA200 ---": None,
               "T6-a. Sälj först under 0,97 × SMA200": dict(M2, sma_buf=3),
               "T6-b. Sälj först under 0,95 × SMA200": dict(M2, sma_buf=5),
               "T6-c. SMA200-brott två månadsskiften i rad": dict(M2, sma_two=True)}
        if which == "ptarget":
            res = pt_study(close)
            with open(a.out, "w", encoding="utf-8") as f: json.dump({"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "test": "T12 riktkurser", "resultat": res}, f, ensure_ascii=False, indent=1)
            print(f"\nSkrev {a.out}"); return
        if which == "fibzone":
            res = fib_study(close)
            with open(a.out, "w", encoding="utf-8") as f: json.dump({"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "test": "T11 rekylzon", "aktier": int(close.shape[1]), "resultat": res}, f, ensure_ascii=False, indent=1)
            print(f"\nSkrev {a.out}"); return
        if which in ("after", "t3"):
            T5, trig, _ = after_sale(close, vol, M2)
            if trig: VAR.update(T6V)
        if which == "smabuf": VAR.update(T6V)
        if which in ("volcap", "t9"):
            VAR["--- T9 volatilitetstak vid köp: årsvolatilitet (60 d) över taket får inte köpas ---"] = None
            for vc in (0.80, 1.00, 1.20): VAR[f"T9-{int(vc*100)}. A + inga köp över {int(vc*100)} % volatilitet"] = dict(M2, vol_cap=vc)
        if which in ("steady", "t10"):
            VAR["--- T10 jämna trender: avgörare bland aktier med (nästan) lika poäng ---"] = None
            VAR["T10-a. Jämnast trend (R² 120 d) först i stället för brantast"] = dict(M2, tie_m121=False, tie_mode="r2")
            VAR["T10-b. Lägst volatilitet först i stället för brantast"] = dict(M2, tie_m121=False, tie_mode="lowvol")
            VAR["T10-c. Tak 100 i stället för 150 + jämnast trend först"] = dict(M2, tie_m121=False, tie_mode="r2", mom_cap=100)
        if which in ("pullback", "t3"):
            VAR["--- T7 rekyl i upptrend: 1-månadsavkastning −25 till −10 % ger förtur ---"] = None
            VAR["T7-a. Rekylaktier inom topp 30 först"] = dict(M2, pb_pref="pool", pb_pool=30)
            VAR["T7-b. Rekyl ger +5 poäng"] = dict(M2, pb_pref="bonus", pb_bonus=5.0)
            VAR["T7-c. Rekyl ger +10 poäng"] = dict(M2, pb_pref="bonus", pb_bonus=10.0)
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        idx = close.index; mid = idx[len(idx) // 2]
        segs = {"Hela": (idx[0], idx[-1]), "Första halvan": (idx[0], mid), "Andra halvan": (mid - pd.Timedelta(days=430), idx[-1])}
        print(f"\n########## Strategitester under 2.0 ({which})  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print("   Beslutsregel (TESTPLAN § 9, skriven innan körning): en variant går in bara om Sharpe OCH Calmar slår A på hela perioden,")
        print("   Sharpe inte är lägre än A i någon halva, det håller med dubbla kostnader och förskjutet kvartal, CAGR faller högst 2 pe")
        print("   (riskbudget: 4 pe) och nedgången inte är djupare än A. Vinner flera nivåer väljs den mittersta.\n")
        res = {}
        def run_cfg(over, sub, subv):
            cfg = dict(DEF); cfg.update(over)
            y2 = (sub.index[-1] - sub.index[min(210, len(sub) - 1)]).days / 365.25
            e, tr, hd = run(sub, subv, cfg); st_ = stats(e, tr, y2, hd)
            vb = LAST_DIAG["x"].get("vol_buy") or []
            if vb: st_["median_vol_vid_köp_%"] = round(float(np.nanmedian(vb)) * 100, 1)
            return st_, e
        for nm, over in VAR.items():
            if over is None: print(f"\n   {nm}"); continue
            try:
                st, e = run_cfg(over, close, vol); st["år"] = yearly(e)
                for segn, (s0, s1) in segs.items():
                    if segn == "Hela": continue
                    sub = close.loc[s0:s1]; subv = vol.loc[s0:s1]
                    st[segn] = run_cfg(over, sub, subv)[0]
                st["dubbla kostnader"] = run_cfg(dict(over, **DBL), close, vol)[0]
                st["kvartal +1 mån"] = run_cfg(dict(over, reb="Q1"), close, vol)[0]
                res[nm] = st
                h1, h2, dk, q1 = st["Första halvan"], st["Andra halvan"], st["dubbla kostnader"], st["kvartal +1 mån"]
                print(f"   {nm:<70}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %  köp/år {st['köp_per_år']:>5}"
                      f"  | halvor Sharpe {h1.get('Sharpe')} / {h2.get('Sharpe')}  | dubbla {dk.get('CAGR_%')} % {dk.get('Sharpe')} {dk.get('Calmar')}  | Q+1 {q1.get('CAGR_%')} % {q1.get('Sharpe')} {q1.get('Calmar')}"
                      f"  utgångar {st.get('utgångar')}  vol vid köp {st.get('median_vol_vid_köp_%')} %")
            except Exception as ex:
                print(f"   {nm:<70} FEL {str(ex)[:80]}"); res[nm] = {"fel": str(ex)[:120]}
        # --- prövning mot beslutsregeln ---
        A = res.get("A.  Modell 2.0 (referens)", {})
        if A and "Sharpe" in A:
            print(f"\n   === PRÖVNING MOT BESLUTSREGELN (A: {A['CAGR_%']} %, Sharpe {A['Sharpe']}, Calmar {A['Calmar']}, nedgång {A['max_nedgång_%']} %) ===")
            for nm, st in res.items():
                if nm.startswith("A.") or "Sharpe" not in st: continue
                lim = 4 if nm.startswith("T2") else 2
                ok = [("Sharpe>", st["Sharpe"] > A["Sharpe"]), ("Calmar>", (st["Calmar"] or 0) > (A["Calmar"] or 0)),
                      ("halvor", all((st[k].get("Sharpe") or 0) >= (A[k].get("Sharpe") or 0) for k in ("Första halvan", "Andra halvan"))),
                      ("dubbla", (st["dubbla kostnader"].get("Sharpe") or 0) > (A["dubbla kostnader"].get("Sharpe") or 0) and (st["dubbla kostnader"].get("Calmar") or 0) > (A["dubbla kostnader"].get("Calmar") or 0)),
                      ("Q+1", (st["kvartal +1 mån"].get("Sharpe") or 0) > (A["kvartal +1 mån"].get("Sharpe") or 0) and (st["kvartal +1 mån"].get("Calmar") or 0) > (A["kvartal +1 mån"].get("Calmar") or 0)),
                      (f"CAGR −{lim} pe", st["CAGR_%"] >= A["CAGR_%"] - lim), ("nedgång", st["max_nedgång_%"] >= A["max_nedgång_%"])]
                st["prövning"] = {k: bool(v) for k, v in ok}
                print(f"   {nm:<70}{'GODKÄND' if all(v for _, v in ok) else 'faller'}  " + "  ".join(f"{k}:{'✓' if v else '✗'}" for k, v in ok))
        T8 = None
        if which in ("sectorsale", "t3"):
            cfgA = dict(DEF); cfgA.update(M2); eA, _, _ = run(close, vol, cfgA)
            T8 = sector_sale(close, vol, eA)
        print(f"\n   === ÅR FÖR ÅR ===")
        names = [n for n in res if "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        yrsall = sorted({y for n in names for y in res[n]["år"]})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>8}" for n in names))
        for y in yrsall: print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>8}" for n in names))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": f"strat-{which}",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res, "T5": T5, "T8": T8,
               "varning": "Survivorship bias och ingen fundamenta. Beslutsregel i TESTPLAN § 9."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Trötta innehav (2026-11-04) ----------
    if a.stale:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Trötta innehav: blir svaga men stigande aktier kvar i åratal?  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Del 1 mäter dagens modell: hur länge innehav ligger kvar, hur de rankas medan de ligger kvar, och vad de")
        print(f"   långliggande gav. Del 2 testar att tvinga ut dem: sälj det som legat utanför topp N i X månader i rad.")
        print(f"   Beslutsregel (skriven innan körning): en variant går in bara om den slår A i CAGR OCH Sharpe,")
        print(f"   håller vid dubbla kostnader och vid förskjutet kvartalsdatum, och inte fördjupar nedgången.\n")
        # --- Del 1: diagnostik på A ---
        cfg = dict(DEF); cfg.update(B); cfg["diag"] = True
        e, tr, hd = run(close, vol, cfg); stA = stats(e, tr, yrs, hd)
        snap = list(LAST_DIAG["snap"]); exits = list(LAST_DIAG["exits"])
        hdays = sorted(hd)
        def pct(v, q): return v[min(len(v) - 1, int(len(v) * q))] if v else None
        print(f"   A. Kärna (referens)                                               {stA['CAGR_%']:>7} %  Sharpe {stA['Sharpe']}  nedgång {stA['max_nedgång_%']} %")
        print(f"\n   === INNEHAVSTIDER (avslutade innehav, {len(hdays)} st) ===")
        print(f"   median {pct(hdays, .5)} d · 75 % {pct(hdays, .75)} d · 90 % {pct(hdays, .9)} d · längsta {hdays[-1] if hdays else '–'} d")
        print(f"   andel över 1 år: {sum(1 for x in hdays if x > 252) / max(1, len(hdays)) * 100:.0f} % · över 2 år: {sum(1 for x in hdays if x > 504) / max(1, len(hdays)) * 100:.0f} %")
        print(f"\n   === RANKNINGSPLATS MEDAN INNEHAVET LIGGER KVAR (ögonblicksbilder vid månadsskiften, {len(snap)} st) ===")
        for lo, hi, lab in ((0, 126, "0–6 mån"), (126, 252, "6–12 mån"), (252, 504, "1–2 år"), (504, 99999, "över 2 år")):
            grp = [x for x in snap if lo <= x[3] < hi]
            if not grp: continue
            rks = sorted(x[2] for x in grp)
            print(f"   {lab:<10} {len(grp):>5} bilder · median plats {pct(rks, .5):.0f} · andel utanför topp 50: {sum(1 for r_ in rks if r_ > 50) / len(rks) * 100:.0f} % · utanför topp 100: {sum(1 for r_ in rks if r_ > 100) / len(rks) * 100:.0f} %")
        print(f"\n   === AVKASTNING PÅ AVSLUTADE INNEHAV efter innehavstid (medel och median, köpkurs → säljkurs) ===")
        for lo, hi, lab in ((0, 126, "0–6 mån"), (126, 252, "6–12 mån"), (252, 504, "1–2 år"), (504, 99999, "över 2 år")):
            grp = [x[4] * 100 for x in exits if lo <= x[3] < hi]
            if not grp: continue
            g2 = sorted(grp)
            print(f"   {lab:<10} {len(grp):>5} st · medel {sum(grp) / len(grp):>7.1f} % · median {pct(g2, .5):>7.1f} % · per år {sum(grp) / len(grp) / max(0.25, ((lo + min(hi, 1000)) / 2) / 252):>6.1f} %")
        # --- Del 2: tvinga ut ---
        VAR = {
            "A. Kärna (referens)":                                        dict(B),
            "--- sälj det som legat utanför topp N i X månader i rad ---":  None,
            "S1. Utanför topp 50 i 12 mån":                               dict(B, stale_rank=50, stale_days=252),
            "S2. Utanför topp 100 i 12 mån":                              dict(B, stale_rank=100, stale_days=252),
            "S3. Utanför topp 50 i 6 mån":                                dict(B, stale_rank=50, stale_days=126),
            "S4. Utanför topp 100 i 6 mån":                               dict(B, stale_rank=100, stale_days=126),
            "S5. Utanför topp 200 i 12 mån":                              dict(B, stale_rank=200, stale_days=252),
            "S6. Utanför topp 50 i 18 mån":                               dict(B, stale_rank=50, stale_days=378),
            "--- robusthet ---":                                          None,
            "S7. S2, dubbla kostnader":                                   dict(B, stale_rank=100, stale_days=252, **DBL),
            "S8. S4, dubbla kostnader":                                   dict(B, stale_rank=100, stale_days=126, **DBL),
            "S9. A, dubbla kostnader":                                    dict(B, **DBL),
            "S10. S2, kvartal förskjutet 1 mån":                          dict(B, stale_rank=100, stale_days=252, reb="Q1"),
            "S11. S4, kvartal förskjutet 1 mån":                          dict(B, stale_rank=100, stale_days=126, reb="Q1"),
            "S12. A, kvartal förskjutet 1 mån":                           dict(B, reb="Q1"),
        }
        print(f"\n   === DEL 2: TVINGA UT TRÖTTA INNEHAV ===")
        res = {"_snitt": bst, "_diag": {"innehavstider": hdays, "bilder": len(snap)}}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd); st["år"] = yearly(e)
                st["trötta"] = sum(1 for x in tr if x[0] == "trött"); res[nm] = st
                print(f"   {nm:<64}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d  trötta sålda {st['trötta']}")
            except Exception as ex:
                print(f"   {nm:<64} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        main = [n for n in names if short[n] in ("A", "S1", "S2", "S3", "S4", "S5", "S6")]
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "trötta innehav",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias och ingen fundamenta. Tolka som indikation."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Jämnhet (2026-11-04): prisbaserad kvalitet ----------
    if a.smooth:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        VAR = {
            "A. Kärna (referens): tak 150, lika poäng → högst 12-1":       dict(B),
            "--- jämnhet som avgörare vid lika poäng (i stället för 12-1) ---": None,
            "Z1. Lika poäng → jämnast uppgång först":                      dict(B, tie_m121=False, tie_smooth=True),
            "--- jämnhet som tillägg i poängen ---":                        None,
            "Z2. ± 5 poäng efter andel positiva dagar":                     dict(B, smooth_w=2.5),
            "Z3. ± 10 poäng efter andel positiva dagar":                    dict(B, smooth_w=5),
            "Z4. ± 20 poäng efter andel positiva dagar":                    dict(B, smooth_w=10),
            "--- filter: bort med dem som haft ett djupt fall inom fönstret ---": None,
            "Z5. Inget köp om största fall inom 12-1 > 40 %":               dict(B, dd_max=40),
            "Z6. Inget köp om största fall inom 12-1 > 30 %":               dict(B, dd_max=30),
            "Z7. Inget köp om största fall inom 12-1 > 20 %":               dict(B, dd_max=20),
            "--- robusthet ---":                                           None,
            "Z8. Z3, dubbla kostnader":                                    dict(B, smooth_w=5, **DBL),
            "Z9. Z5, dubbla kostnader":                                    dict(B, dd_max=40, **DBL),
            "Z10. Z1, dubbla kostnader":                                   dict(B, tie_m121=False, tie_smooth=True, **DBL),
            "Z11. A, dubbla kostnader":                                    dict(B, **DBL),
            "Z12. Z3, kvartal förskjutet 1 mån":                           dict(B, smooth_w=5, reb="Q1"),
            "Z13. Z5, kvartal förskjutet 1 mån":                           dict(B, dd_max=40, reb="Q1"),
            "Z14. Z1, kvartal förskjutet 1 mån":                           dict(B, tie_m121=False, tie_smooth=True, reb="Q1"),
            "Z15. A, kvartal förskjutet 1 mån":                            dict(B, reb="Q1"),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Jämnhet: prisbaserad kvalitet – gynna uppgångar i små steg?  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Mått: andel positiva dagar och största fall inom 12-1-fönstret (t-252 → t-21). Ingen fundamental data,")
        print(f"   ingen framåtblick. Litteraturen (Da/Gurun/Warachka 2014) säger att jämn uppgång fortsätter oftare.")
        print(f"   Beslutsregel (skriven innan körning): en variant går in bara om den slår A i CAGR OCH Sharpe,")
        print(f"   håller vid dubbla kostnader och vid förskjutet kvartalsdatum, och inte fördjupar nedgången.\n")
        print(f"   {'likaviktat snitt av universumet':<64}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<64}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<64} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        main = [n for n in names if short[n] in ("A", "Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7")]
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "jämnhet",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias och ingen fundamenta. Tolka som indikation."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Vikt efter styrka (2026-11-04): mer kapital i de högst rankade? ----------
    if a.wt:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        VAR = {
            "A. Kärna (referens): likavikt 5 % per innehav":              dict(B),
            "--- linjär lutning efter rankningsplats ---":                None,
            "Y1. Svag lutning: plats 1 får 6,25 %, plats 20 får 3,75 %":  dict(B, rank_wt=0.5),
            "Y2. Medel: plats 1 får 7,5 %, plats 20 får 2,5 %":           dict(B, rank_wt=1.0),
            "Y3. Stark: plats 1 får 8,75 %, plats 20 får 1,25 %":         dict(B, rank_wt=1.5),
            "--- trappsteg: de högst rankade får dubbel vikt ---":         None,
            "Y4. Topp 5 dubbel vikt (ca 8 % mot 4 %)":                     dict(B, rank_top=5),
            "Y5. Topp 10 dubbel vikt (ca 6,7 % mot 3,3 %)":                dict(B, rank_top=10),
            "--- robusthet ---":                                          None,
            "Y6. Y1, dubbla kostnader":                                   dict(B, rank_wt=0.5, **DBL),
            "Y7. Y2, dubbla kostnader":                                   dict(B, rank_wt=1.0, **DBL),
            "Y8. Y4, dubbla kostnader":                                   dict(B, rank_top=5, **DBL),
            "Y9. A, dubbla kostnader":                                    dict(B, **DBL),
            "Y10. Y1, kvartal förskjutet 1 mån":                          dict(B, rank_wt=0.5, reb="Q1"),
            "Y11. Y2, kvartal förskjutet 1 mån":                          dict(B, rank_wt=1.0, reb="Q1"),
            "Y12. Y4, kvartal förskjutet 1 mån":                          dict(B, rank_top=5, reb="Q1"),
            "Y13. A, kvartal förskjutet 1 mån":                           dict(B, reb="Q1"),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Vikt efter styrka: mer kapital i de högst rankade?  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: likavikt ger alla 20 innehav 5 %. Ska de högst rankade få mer, eftersom de starkaste gett mest?")
        print(f"   Vikten sätts vid köp och återställs vid kvartalets likaviktning efter aktuell rankningsplats.")
        print(f"   Beslutsregel (skriven innan körning): en variant går in bara om den slår A i CAGR OCH Sharpe,")
        print(f"   håller vid dubbla kostnader och vid förskjutet kvartalsdatum, och inte fördjupar nedgången.\n")
        print(f"   {'likaviktat snitt av universumet':<64}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<64}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<64} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        main = [n for n in names if short[n] in ("A", "Y1", "Y2", "Y3", "Y4", "Y5")]
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "vikt efter styrka",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias och ingen fundamenta. Tolka som indikation."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Poängtaket (2026-11-04): ordningen bland aktier som delar topp-poängen ----------
    if a.tie:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        VAR = {
            "A. Kärna (referens): tak 60 %, lika poäng i listordning":   dict(B, mom_cap=60, tie_m121=False),
            "--- samma tak, men lika poäng avgörs av högre 12-1 ---":     None,
            "X1. Tak 60 %, lika poäng → högst 12-1 först":               dict(B, mom_cap=60, tie_m121=True),
            "X2. Tak 80 % (skannerns), lika poäng → högst 12-1 först":   dict(B, mom_cap=80, tie_m121=True),
            "--- högre tak: storleken på 12-1 får slå igenom ---":        None,
            "X3. Tak 100 %":                                             dict(B, mom_cap=100, tie_m121=True),
            "X4. Tak 150 %":                                             dict(B, mom_cap=150, tie_m121=True),
            "X5. Tak 300 %":                                             dict(B, mom_cap=300, tie_m121=True),
            "X6. Inget tak: percentilrankning av 12-1":                  dict(B, mom_rank=True),
            "--- lägre tak: bred topp, trenden avgör mer ---":            None,
            "X7. Tak 40 %, lika poäng → högst 12-1 först":               dict(B, mom_cap=40, tie_m121=True),
            "--- robusthet ---":                                         None,
            "X8. X1, dubbla kostnader":                                  dict(B, mom_cap=60, tie_m121=True, **DBL),
            "X9. X4, dubbla kostnader":                                  dict(B, mom_cap=150, tie_m121=True, **DBL),
            "X10. X6, dubbla kostnader":                                 dict(B, mom_rank=True, **DBL),
            "X11. A, dubbla kostnader":                                  dict(B, **DBL),
            "X12. X1, kvartal förskjutet 1 mån":                         dict(B, mom_cap=60, tie_m121=True, reb="Q1"),
            "X13. X4, kvartal förskjutet 1 mån":                         dict(B, mom_cap=150, tie_m121=True, reb="Q1"),
            "X14. X6, kvartal förskjutet 1 mån":                         dict(B, mom_rank=True, reb="Q1"),
            "X15. A, kvartal förskjutet 1 mån":                          dict(B, mom_cap=60, tie_m121=False, reb="Q1"),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Poängtaket: vilka av de aktier som delar topp-poängen ska köpas?  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Bakgrund: 12-1 över taket ger full momentumpoäng. Med full trendpoäng delar 30–40 aktier poängen 100,")
        print(f"   och vilka 20 som köps avgörs i dag av listordningen (skannern: nordiska först). Testet frågar om")
        print(f"   storleken på 12-1 bland dem spelar roll – och åt vilket håll.")
        print(f"   Beslutsregel (skriven innan körning): en variant går in bara om den slår A i CAGR OCH Sharpe,")
        print(f"   håller vid dubbla kostnader och vid förskjutet kvartalsdatum, och inte fördjupar nedgången.\n")
        print(f"   {'likaviktat snitt av universumet':<64}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<64}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<64} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        # Hur mycket av skillnaden är listordningens tur? A med slumpad kolumnordning, tre frön.
        print(f"\n   === SLUMPAD LISTORDNING: A (tak 60 %, lika poäng i listordning) med aktierna i slumpad ordning ===")
        rs_ = []
        for seed in (1, 2, 3):
            cols = list(close.columns); np.random.default_rng(seed).shuffle(cols)
            cfg = dict(DEF); cfg.update(B); cfg.update(mom_cap=60, tie_m121=False)
            try:
                e, tr, hd = run(close[cols], vol[cols], cfg); st = stats(e, tr, yrs, hd); rs_.append(st)
                print(f"   {'A, slumpordning frö ' + str(seed):<64}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %")
            except Exception as ex:
                print(f"   frö {seed}: FEL {str(ex)[:60]}")
        res["_slumpordning"] = rs_
        # Delprov: period och marknad
        print(f"\n   === UPPDELAT: A, X2 (skannerns tak, 12-1 avgör), X4 (tak 150), X6 (percentil) – Sharpe och CAGR per delprov ===")
        def group(cols):
            us, nordic, eur = [], [], []
            for t in cols:
                if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): nordic.append(t)
                elif "." in t: eur.append(t)
                else: us.append(t)
            return {"USA": us, "Norden": nordic, "Europa": eur}
        grp = group(list(close.columns)); idx = close.index; mid = idx[len(idx) // 2]
        segs = {"Hela": (idx[0], idx[-1]), "Första halvan": (idx[0], mid), "Andra halvan": (mid - pd.Timedelta(days=430), idx[-1])}
        SUB = {"A": VAR["A. Kärna (referens): tak 60 %, lika poäng i listordning"],
               "X2": VAR["X2. Tak 80 % (skannerns), lika poäng → högst 12-1 först"],
               "X4": VAR["X4. Tak 150 %"], "X6": VAR["X6. Inget tak: percentilrankning av 12-1"]}
        wins = {k: 0 for k in SUB}; split = {}
        print(f"   {'delprov':<28}{'aktier':>7}" + "".join(f"{k:>13}" for k in SUB) + "   vinnare")
        for segn, (s0, s1) in segs.items():
            for un, cols in [("Alla", list(close.columns))] + list(grp.items()):
                cols = [c for c in cols if c in close.columns]
                if len(cols) < 40: continue
                sub = close.loc[s0:s1, cols]; subv = vol.loc[s0:s1, cols]
                if len(sub) < 500: continue
                y2 = (sub.index[-1] - sub.index[min(210, len(sub) - 1)]).days / 365.25
                if y2 < 1.5: continue
                row = {}
                for k, over in SUB.items():
                    cfg = dict(DEF); cfg.update(over)
                    try: e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, y2, hd); row[k] = (st["Sharpe"], st["CAGR_%"])
                    except Exception: row[k] = (None, None)
                ok = {k: v for k, v in row.items() if v[0] is not None}
                if not ok: continue
                w = max(ok, key=lambda k: ok[k][0]); wins[w] += 1; split[f"{segn} / {un}"] = row
                print(f"   {segn + ' / ' + un:<28}{len(cols):>7}" + "".join(f"{(str(v[0]) + '  ' + str(v[1]) + '%') if v[0] is not None else '–':>13}" for v in row.values()) + f"   {w}")
        print("   Antal delprov vunna (Sharpe): " + ", ".join(f"{k} {v}" for k, v in wins.items()))
        res["_delprov"] = split
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        main = [n for n in names if short[n] in ("A", "X1", "X2", "X3", "X4", "X5", "X6", "X7")]
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "poängtak",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias och ingen fundamenta. Tolka som indikation."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- 2.0-kandidaten (2026-11-05, TESTPLAN_2.0.md § 1–3): realistisk fyllnad + storbolagsuniversum ----------
    if a.mm2:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        R = dict(t1=True, slip=True)                   # realistisk fyllnad: nästa dags stängning + marknadspåverkan
        VAR = {
            "A0. Modell 1.0 som den mätts t.o.m. 2026-11-04 (fyllnad på signaldagen, utan slippage)": dict(B),
            "A1. 1.0 med fyllnad nästa dag (T+1), utan slippage":                 dict(B, t1=True),
            "A.  1.0 med T+1 + slippage  (NY REFERENS)":                          dict(B, **R),
            "--- B: storbolagsuniversum – likviditetsgolv i kronor, samma för alla marknader ---": None,
            "B1. A + dagsomsättning ≥ 20 Mkr":                                    dict(B, **R, minadv_sek=20e6),
            "B2. A + dagsomsättning ≥ 50 Mkr":                                    dict(B, **R, minadv_sek=50e6),
            "B3. A + dagsomsättning ≥ 100 Mkr":                                   dict(B, **R, minadv_sek=100e6),
            "B4. A + dagsomsättning ≥ 250 Mkr":                                   dict(B, **R, minadv_sek=250e6),
            "--- robusthet: dubbla kostnader och förskjutet kvartal ---":         None,
            "R1. A, dubbla kostnader":                                            dict(B, **R, **DBL),
            "R2. B1, dubbla kostnader":                                           dict(B, **R, minadv_sek=20e6, **DBL),
            "R3. B2, dubbla kostnader":                                           dict(B, **R, minadv_sek=50e6, **DBL),
            "R4. B3, dubbla kostnader":                                           dict(B, **R, minadv_sek=100e6, **DBL),
            "R5. A, kvartal förskjutet 1 mån":                                    dict(B, **R, reb="Q1"),
            "R6. B1, kvartal förskjutet 1 mån":                                   dict(B, **R, minadv_sek=20e6, reb="Q1"),
            "R7. B2, kvartal förskjutet 1 mån":                                   dict(B, **R, minadv_sek=50e6, reb="Q1"),
            "R8. B3, kvartal förskjutet 1 mån":                                   dict(B, **R, minadv_sek=100e6, reb="Q1"),
            "--- känslighet (A): fönster och medelvärde ---":                     None,
            "S1. A med 11-1":                                                     dict(B, **R, look=231),
            "S2. A med 13-1":                                                     dict(B, **R, look=273),
            "S3. A med SMA180":                                                   dict(B, **R, sma_long=180),
            "S4. A med SMA220":                                                   dict(B, **R, sma_long=220),
            "--- kostnadsläge (SPEC § 4): 5 innehav, bara Norden, 100 000 kr ---": None,
            "K1. 1.0 kostnadsläge, T+1 + slippage":                               dict(B, **R, n=5, mkt="nordic", port_sek=100_000.0),
            "K2. K1 + dagsomsättning ≥ 20 Mkr":                                   dict(B, **R, n=5, mkt="nordic", port_sek=100_000.0, minadv_sek=20e6),
            "K3. 1.0 kostnadsläge, 4 innehav":                                    dict(B, **R, n=4, mkt="nordic", port_sek=100_000.0),
            "K4. 1.0 kostnadsläge, 6 innehav":                                    dict(B, **R, n=6, mkt="nordic", port_sek=100_000.0),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## 2.0-kandidaten: realistisk fyllnad och storbolagsuniversum  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Steg 0: hur mycket av 1.0:s resultat försvinner med fyllnad dagen efter signalen och med marknadspåverkan?")
        print(f"   Steg 1: B = samma modell på aktier med dagsomsättning över ett golv i KRONOR (1.0:s golv 3 M är i lokal valuta).")
        print(f"   Beslutsregel (TESTPLAN_2.0.md § 3, skriven innan körning): B vinner över A om Calmar är högre, Sharpe inte lägre,")
        print(f"   CAGR faller högst 5 procentenheter, nedgången inte är djupare, det håller vid dubbla kostnader och förskjutet")
        print(f"   kvartal, och median-dagsomsättningen vid köp är minst 5 × A:s.\n")
        print(f"   {'likaviktat snitt av universumet':<78}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd, x=LAST_DIAG.get("x")); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<78}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %"
                      f"  friktion {st.get('friktion_%_per_år')} %/år  oms {st.get('omsättning_ggr_per_år')}×  ADV-median {st.get('median_ADV_vid_köp_Mkr')} Mkr"
                      f"  köp/år {st['köp_per_år']}")
            except Exception as ex:
                print(f"   {nm:<78} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        # Beslutsregeln mekaniskt
        A = next((res[n] for n in names if short[n] == "A"), None)
        if A:
            print(f"\n   === BESLUTSREGEL mot A (CAGR {A['CAGR_%']} %, Sharpe {A['Sharpe']}, Calmar {A['Calmar']}, nedgång {A['max_nedgång_%']} %, ADV {A.get('median_ADV_vid_köp_Mkr')} Mkr) ===")
            for n in names:
                k = short[n]
                if not k.startswith("B"): continue
                s = res[n]; ok = []
                ok.append(("Calmar>", (s.get("Calmar") or 0) > (A.get("Calmar") or 0)))
                ok.append(("Sharpe≥", (s.get("Sharpe") or 0) >= (A.get("Sharpe") or 0)))
                ok.append(("CAGR≥A−5", s["CAGR_%"] >= A["CAGR_%"] - 5))
                ok.append(("nedgång≤", s["max_nedgång_%"] >= A["max_nedgång_%"]))
                ok.append(("ADV≥5×", (s.get("median_ADV_vid_köp_Mkr") or 0) >= 5 * (A.get("median_ADV_vid_köp_Mkr") or 1e9)))
                rk = {"B1": ("R2", "R6"), "B2": ("R3", "R7"), "B3": ("R4", "R8")}.get(k)
                if rk:
                    rr = [res[x] for x in names if short[x] in rk]
                    ok.append(("robust", all((r.get("Calmar") or 0) > (A.get("Calmar") or 0) for r in rr) if rr else False))
                verdict = "VINNER" if all(v for _, v in ok) else "faller"
                print(f"   {k:<4} {verdict:<8} " + "  ".join(f"{nm_}{'✓' if v else '✗'}" for nm_, v in ok))
        print(f"\n   === ÅR FÖR ÅR ===")
        main = [n for n in names if short[n] in ("A0", "A", "B1", "B2", "B3", "B4", "K1", "K2")]
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main) + f"{'snitt':>7}")
        by = yearly(be) if be is not None else {}
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main) + f"{by.get(y, '–'):>7}")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "mm2",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias (dagens indexmedlemmar) kvarstår i alla varianter. Slippage är en modell, inte uppmätt."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- 2.0 steg C (2026-11-05, TESTPLAN_2.0.md): jämnt/riskjusterat momentum mot A ----------
    if a.smooth2:
        # Omgång 3 (efter #76): bekräftelse. #76 på P3-referens: C60 32,8 / C90 49,0 / C120 42,8 / C150 35,7 / C180 36,6 / C250 30,4 %
        # mot P3 34,1 %; 90–180 slog P3 i Calmar och Sharpe, 90/120/150 höll vid dubbla kostnader och Q1. Gapfilter sämre.
        # Här: tolv delprov (period × marknad) för P3, C90, C120, C150, plus kostnadsläget (5 nordiska, 100 000 kr).
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month",
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False, t1=True, slip=True)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        CL = lambda nd, **k: dict(B, mom_measure="clenow", clenow_n=nd, **k)
        K = dict(n=5, mkt="nordic", port_sek=100_000.0)
        VAR = {
            "A.  Referens P3 (1.0 som appen: månadsslut, kalender-12-1, T+1, slippage)":   dict(B),
            "C90.  Clenow 90 d":                                                            CL(90),
            "C120. Clenow 120 d":                                                           CL(120),
            "C150. Clenow 150 d":                                                           CL(150),
            "--- kostnadsläge: 5 innehav, bara Norden, 100 000 kr ---":                    None,
            "K0. P3 kostnadsläge":                                                          dict(B, **K),
            "K90. Clenow 90 d kostnadsläge":                                                CL(90, **K),
            "K120. Clenow 120 d kostnadsläge":                                              CL(120, **K),
            "K150. Clenow 150 d kostnadsläge":                                              CL(150, **K),
        }
        SUBSET = {"P3": dict(B), "C90": CL(90), "C120": CL(120), "C150": CL(150)}
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## 2.0 steg C: jämnt och riskjusterat momentum  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Beslutsregel (TESTPLAN_2.0.md § 3, skriven innan körning): C vinner över A om Calmar är högre, Sharpe inte lägre,")
        print(f"   CAGR faller högst 5 procentenheter, nedgången inte är djupare, och det håller vid dubbla kostnader och förskjutet kvartal.\n")
        print(f"   {'likaviktat snitt av universumet':<80}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd, x=LAST_DIAG.get("x")); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<80}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %"
                      f"  friktion {st.get('friktion_%_per_år')} %/år  köp/år {st['köp_per_år']}  innehav {st['innehavstid_dagar']} d  ADV {st.get('median_ADV_vid_köp_Mkr')} Mkr")
            except Exception as ex:
                import traceback; traceback.print_exc()
                print(f"   {nm:<80} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        A = next((res[n] for n in names if short[n] == "A"), None)
        if A:
            print(f"\n   === BESLUTSREGEL mot A (CAGR {A['CAGR_%']} %, Sharpe {A['Sharpe']}, Calmar {A['Calmar']}, nedgång {A['max_nedgång_%']} %) ===")
            rob = {}
            for n in names:
                k = short[n]
                if not (k.startswith("C") or k.startswith("G")): continue
                s_ = res[n]; ok = [("Calmar>", (s_.get("Calmar") or 0) > (A.get("Calmar") or 0)),
                                   ("Sharpe≥", (s_.get("Sharpe") or 0) >= (A.get("Sharpe") or 0)),
                                   ("CAGR≥A−5", s_["CAGR_%"] >= A["CAGR_%"] - 5),
                                   ("nedgång≤", s_["max_nedgång_%"] >= A["max_nedgång_%"])]
                if k in rob:
                    rr = [res[x] for x in names if short[x] in rob[k]]
                    ok.append(("robust", all((r_.get("Calmar") or 0) > (A.get("Calmar") or 0) for r_ in rr) if rr else False))
                print(f"   {k:<4} {'VINNER' if all(v for _, v in ok) else 'faller':<8} " + "  ".join(f"{nm_}{'✓' if v else '✗'}" for nm_, v in ok))
        # Delprov: period × marknad (samma uppdelning som --tie 2026-11-04)
        print(f"\n   === DELPROV: Sharpe och CAGR per period och marknad ===")
        def group(cols):
            us, nordic, eur = [], [], []
            for t in cols:
                if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): nordic.append(t)
                elif "." in t: eur.append(t)
                else: us.append(t)
            return {"USA": us, "Norden": nordic, "Europa": eur}
        grp = group(list(close.columns)); idx = close.index; mid = idx[len(idx) // 2]
        segs = {"Hela": (idx[0], idx[-1]), "Första halvan": (idx[0], mid), "Andra halvan": (mid - pd.Timedelta(days=430), idx[-1])}
        wins = {k: 0 for k in SUBSET}; winc = {k: 0 for k in SUBSET}; split = {}
        print(f"   {'delprov':<28}{'aktier':>7}" + "".join(f"{k:>14}" for k in SUBSET) + "   vinnare Sharpe / CAGR")
        for segn, (s0, s1) in segs.items():
            for un, cols in [("Alla", list(close.columns))] + list(grp.items()):
                cols = [c for c in cols if c in close.columns]
                if len(cols) < 40: continue
                sub = close.loc[s0:s1, cols]; subv = vol.loc[s0:s1, cols]
                if len(sub) < 500: continue
                y2 = (sub.index[-1] - sub.index[min(210, len(sub) - 1)]).days / 365.25
                if y2 < 1.5: continue
                row = {}
                for k, over in SUBSET.items():
                    cfg = dict(DEF); cfg.update(over)
                    try: e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, y2, hd); row[k] = (st["Sharpe"], st["CAGR_%"])
                    except Exception: row[k] = (None, None)
                ok = {k: v for k, v in row.items() if v[0] is not None}
                if not ok: continue
                w = max(ok, key=lambda k: ok[k][0]); wins[w] += 1
                wc = max(ok, key=lambda k: ok[k][1]); winc[wc] += 1
                split[f"{segn} / {un}"] = row
                print(f"   {segn + ' / ' + un:<28}{len(cols):>7}" + "".join(f"{(str(v[0]) + ' ' + str(v[1]) + '%') if v[0] is not None else '–':>14}" for v in row.values()) + f"   {w} / {wc}")
        print("   Delprov vunna (Sharpe): " + ", ".join(f"{k} {v}" for k, v in wins.items()))
        print("   Delprov vunna (CAGR):   " + ", ".join(f"{k} {v}" for k, v in winc.items()))
        res["_delprov"] = split
        print(f"\n   === ÅR FÖR ÅR ===")
        main = [n for n in names if short[n] in ("A", "C90", "C120", "C150", "K0", "K90", "K120", "K150")]
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "smooth2",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias kvarstår. Slippage är en modell."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Kostnadsläge (2026-11-05): hur många innehav? ----------
    if a.costn:
        B = dict(voladj=False, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", mkt="nordic",
                 cour_pct=0.0009, fx_pct=0.00266, simple_cost=False, t1=True, slip=True)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        MEAS = {"P3": {}, "C120": dict(mom_measure="clenow", clenow_n=120)}
        NS = (5, 6, 7, 8, 10); PORTS = (60_000.0, 100_000.0, 150_000.0)
        VAR = {}
        for mn, mo in MEAS.items():
            for pk in PORTS:
                VAR[f"--- {mn}, {pk/1000:.0f} tkr ---"] = None
                for n in NS:
                    VAR[f"{mn}-{n}-{pk/1000:.0f}. {mn}, {n} innehav, {pk/1000:.0f} tkr"] = dict(B, n=n, port_sek=pk, **mo)
            VAR[f"--- {mn}, 100 tkr, dubbla kostnader ---"] = None
            for n in NS:
                VAR[f"{mn}-{n}-100D. {mn}, {n} innehav, 100 tkr, dubbla kostnader"] = dict(B, n=n, port_sek=100_000.0, **mo, **DBL)
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Kostnadsläge: antal innehav  ({close.shape[1]} aktier, bara Norden handlas) ##########")
        print(f"   Regeln 5 innehav kom från minimicourtaget 29 kr på utländska poster. I kostnadsläget handlas bara Norden (1 kr på .ST).")
        print(f"   Beslutsregel (skriven innan körning): antalet ändras till N om N slår 5 i Calmar OCH Sharpe vid alla tre kapitalnivåerna")
        print(f"   för samma mått, och håller vid dubbla kostnader vid 100 tkr. Flera vinnande N: välj det lägsta som vinner.\n")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd, x=LAST_DIAG.get("x")); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<58}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %"
                      f"  friktion {st.get('friktion_%_per_år')} %/år  köp/år {st['köp_per_år']}")
            except Exception as ex:
                print(f"   {nm:<58} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        print(f"\n   === BESLUTSREGEL: N mot 5, per mått ===")
        for mn in MEAS:
            def g(n, pk): return res.get(f"{mn}-{n}-{pk}. {mn}, {n} innehav, {pk} tkr", {})
            def gd(n): return res.get(f"{mn}-{n}-100D. {mn}, {n} innehav, 100 tkr, dubbla kostnader", {})
            for n in NS[1:]:
                oks = []
                for pk in ("60", "100", "150"):
                    a5, an = g(5, pk), g(n, pk)
                    oks.append(((an.get("Calmar") or 0) > (a5.get("Calmar") or 0)) and ((an.get("Sharpe") or 0) > (a5.get("Sharpe") or 0)))
                d5, dn = gd(5), gd(n)
                oks.append(((dn.get("Calmar") or 0) > (d5.get("Calmar") or 0)) and ((dn.get("Sharpe") or 0) > (d5.get("Sharpe") or 0)))
                print(f"   {mn:<5} {n:>2} innehav: {'VINNER' if all(oks) else 'faller':<7} 60✓/✗ 100 150 dubbla → " + " ".join("✓" if o else "✗" for o in oks))
        # Delprov (2026-11-05 kväll): håller 7 mot 5 i båda halvorna? Bara tidsdelning – kostnadsläget handlar bara Norden.
        print(f"\n   === DELPROV (100 tkr): Sharpe och CAGR per period ===")
        SUBSET = {"P3-5": dict(B, n=5, port_sek=100_000.0), "P3-7": dict(B, n=7, port_sek=100_000.0),
                  "C120-5": dict(B, n=5, port_sek=100_000.0, **MEAS["C120"]), "C120-7": dict(B, n=7, port_sek=100_000.0, **MEAS["C120"])}
        idx = close.index; mid = idx[len(idx) // 2]
        segs = {"Hela": (idx[0], idx[-1]), "Första halvan": (idx[0], mid), "Andra halvan": (mid - pd.Timedelta(days=430), idx[-1])}
        split = {}
        print(f"   {'delprov':<16}" + "".join(f"{k:>16}" for k in SUBSET))
        for segn, (s0, s1) in segs.items():
            sub = close.loc[s0:s1]; subv = vol.loc[s0:s1]
            y2 = (sub.index[-1] - sub.index[min(210, len(sub) - 1)]).days / 365.25
            row = {}
            for k, over in SUBSET.items():
                cfg = dict(DEF); cfg.update(over)
                try: e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, y2, hd); row[k] = (st["Sharpe"], st["CAGR_%"], st["max_nedgång_%"])
                except Exception: row[k] = (None, None, None)
            split[segn] = row
            print(f"   {segn:<16}" + "".join(f"{(str(v[0]) + ' ' + str(v[1]) + '% ' + str(v[2]) + '%') if v[0] is not None else '–':>16}" for v in row.values()))
        res["_delprov"] = split
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "kostnadsläge-antal",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias kvarstår. Slippage är en modell."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Fullt läge (2026-11-05): antal innehav under modell 2.0 ----------
    if a.fulln:
        B = dict(voladj=False, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", mom_measure="clenow", clenow_n=120,
                 cour_pct=0.0009, fx_pct=0.00266, simple_cost=False, t1=True, slip=True)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        NS = (15, 20, 25, 30)
        VAR = {}
        for pk in (10_000_000.0, 600_000.0):
            VAR[f"--- 2.0, {pk/1e6:g} Mkr ---"] = None
            for n in NS: VAR[f"N{n}-{pk/1e6:g}. {n} innehav, {pk/1e6:g} Mkr"] = dict(B, n=n, port_sek=pk)
        VAR["--- robusthet, 10 Mkr ---"] = None
        for n in NS: VAR[f"N{n}-D. {n} innehav, dubbla kostnader"] = dict(B, n=n, port_sek=10_000_000.0, **DBL)
        for n in NS: VAR[f"N{n}-Q. {n} innehav, kvartal förskjutet 1 mån"] = dict(B, n=n, port_sek=10_000_000.0, reb="Q1")
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Fullt läge: antal innehav under modell 2.0  ({close.shape[1]} aktier) ##########")
        print(f"   20 innehav validerades för 1.0 (mix-test 2026-10: 10/20/30). I kostnadsläget skilde sig optimum mellan måtten (#78).")
        print(f"   Beslutsregel (skriven innan körning): antalet ändras från 20 till N bara om N slår 20 i Calmar OCH Sharpe vid båda")
        print(f"   kapitalnivåerna och håller vid dubbla kostnader och förskjutet kvartal. Flera vinnande N: det närmast 20.\n")
        print(f"   {'likaviktat snitt av universumet':<52}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd, x=LAST_DIAG.get("x")); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<52}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %"
                      f"  friktion {st.get('friktion_%_per_år')} %/år  köp/år {st['köp_per_år']}  ADV {st.get('median_ADV_vid_köp_Mkr')} Mkr")
            except Exception as ex:
                print(f"   {nm:<52} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        def g(pref): return next((res[k] for k in res if k.startswith(pref + ".")), {})
        print(f"\n   === BESLUTSREGEL: N mot 20 ===")
        better = lambda a_, b_: (a_.get("Calmar") or 0) > (b_.get("Calmar") or 0) and (a_.get("Sharpe") or 0) > (b_.get("Sharpe") or 0)
        for n in NS:
            if n == 20: continue
            oks = [better(g(f"N{n}-10"), g("N20-10")), better(g(f"N{n}-0.6"), g("N20-0.6")), better(g(f"N{n}-D"), g("N20-D")), better(g(f"N{n}-Q"), g("N20-Q"))]
            print(f"   {n:>2} innehav: {'VINNER' if all(oks) else 'faller':<7} 10 Mkr / 600 tkr / dubbla / förskjutet → " + " ".join("✓" if o else "✗" for o in oks))
        print(f"\n   === ÅR FÖR ÅR (10 Mkr) ===")
        names = [k for k in res if k.endswith("Mkr") and "-10." in k]
        yrsall = sorted({y for k in names for y in res[k]["år"]})
        print(f"   {'år':<8}" + "".join(f"{k.split('.')[0]:>9}" for k in names))
        for y in yrsall: print(f"   {y:<8}" + "".join(f"{res[k]['år'].get(y, '–'):>9}" for k in names))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "fullt-antal",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias kvarstår. Slippage är en modell."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Överlevnadsbias (2026-11-05): hur mycket av 2.0:s resultat är uppgången FÖRE indexinträdet? ----------
    if a.survive:
        ED = sp500_entry_dates()
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", mom_measure="clenow", clenow_n=120,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False, t1=True, slip=True)
        sp = [t for t in close.columns if t in ED]
        VAR = {
            "E0. 2.0, hela universumet (som #76/#80)":                                   dict(B),
            "E1. 2.0, hela universumet, S&P 500-aktier kandidater först från inträdet":   dict(B, entry_dates=ED),
            "--- bara USA ---":                                                           None,
            "U0. 2.0, USA (S&P 500/400/600 m.fl.)":                                       dict(B, mkt="us"),
            "U1. U0, S&P 500-aktier först från inträdet":                                 dict(B, mkt="us", entry_dates=ED),
            "--- bara S&P 500 (där inträdesdatum finns) ---":                             None,
            "S0. 2.0, bara dagens S&P 500-medlemmar":                                     dict(B, universe=sp),
            "S1. S0, kandidater först från inträdet":                                     dict(B, universe=sp, entry_dates=ED),
            "--- 1.0 (12-1) för jämförelse ---":                                          None,
            "P0. 1.0 (P3), hela universumet":                                             dict(B, mom_measure=None),
            "P1. P0, S&P 500-aktier först från inträdet":                                 dict(B, mom_measure=None, entry_dates=ED),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Överlevnadsbias: kandidater först från indexinträdet  ({close.shape[1]} aktier, {len(sp)} i S&P 500 med datum) ##########")
        print(f"   Universumet är dagens indexmedlemmar. Den del av resultatet som kommer från uppgången FÖRE inträdet var aldrig")
        print(f"   investerbar med dagens lista. Skillnaden E0−E1 (och S0−S1 för rena storbolag) är en nedre gräns för biasen:")
        print(f"   bolag som fallit ur index eller gått under saknas helt i båda och kan inte mätas här.\n")
        print(f"   {'likaviktat snitt av universumet':<72}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over); uni = cfg.pop("universe", None)
            cl_, vo_ = (close[uni], vol[uni]) if uni else (close, vol)
            try:
                e, tr, hd = run(cl_, vo_, cfg); st = stats(e, tr, yrs, hd, x=LAST_DIAG.get("x")); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<72}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %  köp/år {st['köp_per_år']}")
            except Exception as ex:
                import traceback; traceback.print_exc()
                print(f"   {nm:<72} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [k for k in VAR if VAR[k] is not None and k in res and "år" in res[k]]
        short = {k: k.split(".")[0] for k in names}
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for k in names for y in res[k]["år"]})
        print(f"   {'år':<8}" + "".join(f"{short[k]:>8}" for k in names))
        for y in yrsall: print(f"   {y:<8}" + "".join(f"{res[k]['år'].get(y, '–'):>8}" for k in names))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "överlevnadsbias",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "sp500_med_datum": len(sp), "jämförelseindex": bst, "varianter": res,
               "varning": "Nedre gräns: bolag som försvunnit ur index saknas helt. Slippage är en modell."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Paritet (2026-11-05, SPEC § 18): gör backtestet samma sak som appen? ----------
    if a.parity:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days=21, stop=0,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month",
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False, t1=True, slip=True)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        VAR = {
            "A.  Referens (1.0, T+1 + slippage): köp var 21:a handelsdag, 12-1 på 21/252 rader":  dict(B),
            "--- köptillfälle ---":                                                           None,
            "P1. Köp bara på månadens sista handelsdag (som appen)":                          dict(B, refill_days="M"),
            "--- 12-1 i kalendermånader (oberoende av handelskalender) ---":                 None,
            "P2. 12-1 = stängning 1 mån tillbaka / 12 mån tillbaka, i rankning och hållregel": dict(B, m121_cal=True),
            "--- båda ---":                                                                  None,
            "P3. P1 + P2 (backtestet gör som appen)":                                         dict(B, refill_days="M", m121_cal=True),
            "--- appens överhettningsformel ---":                                            None,
            "P4. P3 + skannerns överhettningsformel (tak 15 vid +50 %, RSI fullt vid 95)":    dict(B, refill_days="M", m121_cal=True, heat_app=True),
            "--- robusthet ---":                                                             None,
            "R1. P3, dubbla kostnader":                                                       dict(B, refill_days="M", m121_cal=True, **DBL),
            "R2. P3, kvartal förskjutet 1 mån":                                               dict(B, refill_days="M", m121_cal=True, reb="Q1"),
            "R3. A, dubbla kostnader":                                                        dict(B, **DBL),
            "R4. A, kvartal förskjutet 1 mån":                                                dict(B, reb="Q1"),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Paritet backtest↔app  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan är inte vilken variant som är bäst utan om backtestets resultat håller när backtestet gör exakt som appen.")
        print(f"   Beslutsregel (skriven innan körning): P3 blir ny referens om den ligger inom ±3 procentenheter CAGR och ±0,1 Sharpe")
        print(f"   från A. Ligger den utanför har appen och backtestet aldrig testat samma modell, och den sida som avviker ändras.\n")
        print(f"   {'likaviktat snitt av universumet':<84}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd, x=LAST_DIAG.get("x")); st["år"] = yearly(e)
                res[nm] = st
                print(f"   {nm:<84}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  Calmar {st['Calmar']}  nedgång {st['max_nedgång_%']} %"
                      f"  friktion {st.get('friktion_%_per_år')} %/år  köp/år {st['köp_per_år']}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                import traceback; traceback.print_exc()
                print(f"   {nm:<84} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        print(f"\n   === ÅR FÖR ÅR ===")
        yrsall = sorted({y for n in names for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in names))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in names))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "paritet",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias kvarstår. Slippage är en modell."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Likaviktning (2026-11-04): fylla på eftersläntrare eller köpa nya ledare? ----------
    if a.rw:
        B = dict(voladj=False, n=20, band_out=100000, reweight=True, reb="Q", refill_days="M", m121_cal=True, stop=0,      # P3-basis sedan 2026-11-05 (köp vid månadsslut, kalender-12-1, T+1, slippage)
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True, core="month", t1=True, slip=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        DBL = dict(cour_pct=0.0018, fx_pct=0.00532)
        VAR = {
            "A. Kärna (referens): likavikta upp och ner vid kvartal":     dict(B),
            "--- ingen påfyllning av eftersläntrare ---":                 None,
            "W1. Bara ner: minska vinnare, fyll inte på eftersläntrare":  dict(B, rw_up=False),
            "W2. W1 + kassan köper nya topp 20 (högst 3 extra platser)": dict(B, rw_up=False, rw_extra=3),
            "W3. W1 + kassan köper nya topp 20 (högst 5 extra platser)": dict(B, rw_up=False, rw_extra=5),
            "--- påfyllning bara av innehav som själva rankas högt ---":   None,
            "W4. Fyll på bara innehav som rankas topp 20":                dict(B, rw_up_rank=20),
            "W5. Fyll på bara innehav som rankas topp 30":                dict(B, rw_up_rank=30),
            "W6. Fyll på bara innehav som rankas topp 50":                dict(B, rw_up_rank=50),
            "W7. W4 + kassan köper nya topp 20 (högst 3 extra platser)": dict(B, rw_up_rank=20, rw_extra=3),
            "--- ingen likaviktning alls ---":                            None,
            "W8. Ingen likaviktning (bara hållregel + påfyllning till 20)": dict(B, reweight=False),
            "--- robusthet ---":                                          None,
            "W9. W1, dubbla kostnader":                                   dict(B, rw_up=False, **DBL),
            "W10. W2, dubbla kostnader":                                  dict(B, rw_up=False, rw_extra=3, **DBL),
            "W11. W4, dubbla kostnader":                                  dict(B, rw_up_rank=20, **DBL),
            "W12. A, dubbla kostnader":                                   dict(B, **DBL),
            "W13. W1, kvartal förskjutet 1 mån":                          dict(B, rw_up=False, reb="Q1"),
            "W14. W2, kvartal förskjutet 1 mån":                          dict(B, rw_up=False, rw_extra=3, reb="Q1"),
            "W15. W4, kvartal förskjutet 1 mån":                          dict(B, rw_up_rank=20, reb="Q1"),
            "W16. A, kvartal förskjutet 1 mån":                           dict(B, reb="Q1"),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Likaviktning: eftersläntrare eller nya ledare?  portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: vid kvartalsskiftet köper A upp innehav som halkat under målvikt – även om de rankas lågt –")
        print(f"   före nya aktier i topp 20. Gör pengarna mer nytta i nya ledare?")
        print(f"   Beslutsregel (skriven innan körning): en variant går in bara om den slår A i CAGR OCH Sharpe,")
        print(f"   håller vid dubbla kostnader och vid förskjutet kvartalsdatum, och inte fördjupar nedgången.\n")
        print(f"   {'likaviktat snitt av universumet':<64}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd); st["år"] = yearly(e)
                st["påfyllningar"] = sum(1 for x in tr if x[0] == "add"); st["trimningar"] = sum(1 for x in tr if x[0] == "trim")
                res[nm] = st
                print(f"   {nm:<64}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d  påfyllningar {st['påfyllningar']}  trimningar {st['trimningar']}")
            except Exception as ex:
                print(f"   {nm:<64} FEL {str(ex)[:60]}"); res[nm] = {"fel": str(ex)[:120]}
        names = [n for n in VAR if VAR[n] is not None and n in res and "år" in res[n]]
        short = {n: n.split(".")[0] for n in names}
        print(f"\n   === ÅR FÖR ÅR (referens A och huvudvarianterna) ===")
        main = [n for n in names if short[n] in ("A", "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8")]
        yrsall = sorted({y for n in main for y in res[n]['år']})
        print(f"   {'år':<8}" + "".join(f"{short[n]:>7}" for n in main))
        for y in yrsall:
            print(f"   {y:<8}" + "".join(f"{res[n]['år'].get(y, '–'):>7}" for n in main))
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "likaviktning",
               "år": round(yrs, 1), "aktier": int(close.shape[1]), "jämförelseindex": bst, "varianter": res,
               "varning": "Survivorship bias och ingen fundamenta. Tolka som indikation."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Kärnlager: håll ledarna genom normala rekyler utan att tappa avkastning ----------
    if a.core:
        B = dict(voladj=False, n=20, band_out=30, reweight=True, reb="Q", refill_days=21, stop=15,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "0. Satellit (dagens): band 10/30, stopp 15, dagligen":   dict(B),
            "A. Kärna: 12-1>0 & >SMA200, prövas månadsvis":             dict(B, core="month", band_out=100000, stop=0),
            "A2. Kärna: samma, prövas DAGLIGEN, 5 dagar i rad":          dict(B, core="daily", confirm_days=5, band_out=100000, stop=0),
            "A3. Kärna daglig, 10 dagar i rad":                          dict(B, core="daily", confirm_days=10, band_out=100000, stop=0),
            "A4. Kärna daglig, 20 dagar i rad":                          dict(B, core="daily", confirm_days=20, band_out=100000, stop=0),
            "A5. Kärna daglig, 5 d, buffert 3 % under SMA200":           dict(B, core="daily", confirm_days=5, sma_buf=3, band_out=100000, stop=0),
            "A6. Kärna daglig, 5 d, buffert 5 % under SMA200":           dict(B, core="daily", confirm_days=5, sma_buf=5, band_out=100000, stop=0),
            "A7. Kärna daglig, 10 d, buffert 3 %":                       dict(B, core="daily", confirm_days=10, sma_buf=3, band_out=100000, stop=0),
            "B. Kärna daglig + minsta innehavstid 60 d":                 dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, min_hold=60),
            "C. Kärna daglig + band 50 vid kvartal":                      dict(B, core="daily", confirm_days=5, band_out=50, stop=0),
            "R1. Kärna + rotation: sälj sämsta 10 % vid kvartal":        dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.10),
            "R2. Kärna + rotation: sälj sämsta 25 % vid kvartal":        dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.25),
            "R3. Kärna + rotation 25 % bara om ersättaren rankas topp 5": dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.25, rot_top=5),
            "--- rotationens robusthet (2026-10-22) ---":                None,
            "S1. Rotation 5 %":                                           dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.05),
            "S2. Rotation 15 %":                                          dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.15),
            "T0. Kärna, kvartal förskjutet 1 mån":                        dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, reb="Q1"),
            "T1. Rotation 5 %, kvartal förskjutet":                       dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.05, reb="Q1"),
            "T2. Rotation 10 %, kvartal förskjutet":                      dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.10, reb="Q1"),
            "T3. Rotation 15 %, kvartal förskjutet":                      dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.15, reb="Q1"),
            "T4. Rotation 25 %, kvartal förskjutet":                      dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.25, reb="Q1"),
            "U0. Kärna, dubbla kostnader":                                dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, cour_pct=0.0018, fx_pct=0.00532),
            "U1. Rotation 5 %, dubbla kostnader":                         dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.05, cour_pct=0.0018, fx_pct=0.00532),
            "U2. Rotation 10 %, dubbla kostnader":                        dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.10, cour_pct=0.0018, fx_pct=0.00532),
            "U3. Rotation 15 %, dubbla kostnader":                        dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.15, cour_pct=0.0018, fx_pct=0.00532),
            "U4. Rotation 25 %, dubbla kostnader":                        dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, rot_frac=0.25, cour_pct=0.0018, fx_pct=0.00532),
            "--- ombalanseringens datum (2026-10-23) ---":                None,
            "V1. Kärna, kvartal mitt i månaden (15:e mar/jun/sep/dec)":  dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, reb="Qmid"),
            "V2. Kärna, hela portföljen varje månadsslut":               dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, reb="M"),
            "V3. Kärna, tredjedel per månad, månadsslut":                dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, reb="M", tranche=3),
            "V4. Kärna, tredjedel per månad, mitt i månaden":            dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, reb="Mmid", tranche=3),
            "V5. Kärna, tredjedel per månad, mitt i mån, dubbla kostn.": dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, reb="Mmid", tranche=3, cour_pct=0.0018, fx_pct=0.00532),
            "--- slutkandidater (2026-10-23): månadsprövning + tätare likaviktning ---": None,
            "W1. A + tredjedel per månad, mitt i månaden":               dict(B, core="month", band_out=100000, stop=0, reb="Mmid", tranche=3),
            "W2. A + hela portföljen varje månadsslut":                  dict(B, core="month", band_out=100000, stop=0, reb="M"),
            "W3. A, kvartal förskjutet 1 mån":                           dict(B, core="month", band_out=100000, stop=0, reb="Q1"),
            "W4. W1 med dubbla kostnader":                               dict(B, core="month", band_out=100000, stop=0, reb="Mmid", tranche=3, cour_pct=0.0018, fx_pct=0.00532),
            "W5. W1 + minst 60 dagars innehav":                          dict(B, core="month", band_out=100000, stop=0, reb="Mmid", tranche=3, min_hold=60),
            "J. Kärna daglig + ÖKA vid stöd (1,5× vikt)":                dict(B, core="daily", confirm_days=5, band_out=100000, stop=0, add_at_support=True),
            "K. Satellit + ÖKA vid stöd":                                dict(B, add_at_support=True),
            "--- brusfilter på satelliten ---":                          None,
            "D. Stopp 3×ATR i stället för 15 %":                        dict(B, stop_atr=3.0),
            "E. Stopp 4×ATR":                                           dict(B, stop_atr=4.0),
            "F. Stopp 15 % men kräver 5 dagar i rad under":             dict(B, confirm_days=5),
            "G. Stopp 15 % + minsta innehavstid 60 d":                  dict(B, min_hold=60),
            "H. Stopp 15 %, 5 dagar i rad, minst 60 d":                 dict(B, confirm_days=5, min_hold=60),
            "I. Utan stopp (bara band 10/30)":                           dict(B, stop=0),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Kärnlager och brusfilter, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: kan vi hålla starka aktier genom normala rekyler utan att tappa avkastning?")
        print(f"   Kärnan säljer bara när 12-1 blivit negativt eller kursen ligger under SMA200 – vid månadsskifte.\n")
        print(f"   {'likaviktat snitt av universumet':<58}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None: print(f"   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                print(f"   {nm:<58}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d"
                      + (f"  utgångar {st.get('utgångar')}" if nm[:1] in "AV" else ""))
            except Exception as ex:
                print(f"   {nm:<58} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        # --- håller kärnan i olika perioder och marknader? ---
        print(f"\n   === UPPDELAT: slutkandidater – A2 (daglig 5 d), A (månadsprövning), A förskjuten, A + tredjedel/mån, A2 + tredjedel/mån ===")
        def group(cols):
            us, nordic, eur = [], [], []
            for t in cols:
                if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): nordic.append(t)
                elif "." in t: eur.append(t)
                else: us.append(t)
            return {"USA": us, "Norden": nordic, "Europa": eur}
        grp = group(list(close.columns)); idx = close.index; mid = idx[len(idx) // 2]
        segs = {"Hela": (idx[0], idx[-1]), "Första halvan": (idx[0], mid), "Andra halvan": (mid - pd.Timedelta(days=430), idx[-1])}
        SUB = {"dagens": VAR["0. Satellit (dagens): band 10/30, stopp 15, dagligen"],
               "kärna": VAR["A2. Kärna: samma, prövas DAGLIGEN, 5 dagar i rad"],
               "A månad": VAR["A. Kärna: 12-1>0 & >SMA200, prövas månadsvis"],
               "A +1mån": VAR["W3. A, kvartal förskjutet 1 mån"],
               "A+1/3mitt": VAR["W1. A + tredjedel per månad, mitt i månaden"],
               "A2+1/3mitt": VAR["V4. Kärna, tredjedel per månad, mitt i månaden"]}
        wins = {k: 0 for k in SUB}; split = {}
        print(f"   {'delprov':<28}{'aktier':>7}" + "".join(f"{k:>13}" for k in SUB) + "   vinnare")
        for segn, (s0, s1) in segs.items():
            for un, cols in [("Alla", list(close.columns))] + list(grp.items()):
                cols = [c for c in cols if c in close.columns]
                if len(cols) < 40: continue
                sub = close.loc[s0:s1, cols]; subv = vol.loc[s0:s1, cols]
                if len(sub) < 500: continue
                y2 = (sub.index[-1] - sub.index[min(210, len(sub) - 1)]).days / 365.25
                if y2 < 1.5: continue
                row = {}
                for k, over in SUB.items():
                    cfg = dict(DEF); cfg.update(over)
                    try: e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, y2, hd); row[k] = (st["Sharpe"], st["CAGR_%"])
                    except Exception: row[k] = None
                good = {k: v[0] for k, v in row.items() if v is not None}
                if not good: continue
                w = max(good, key=good.get); wins[w] += 1; split[f"{segn} / {un}"] = row
                print(f"   {segn + ' / ' + un:<28}{len(cols):>7}" + "".join(f"{(row[k][0] if row[k] else float('nan')):>6.2f} {(row[k][1] if row[k] else float('nan')):>5.1f}%" for k in SUB) + f"   {w}")
        print(f"   Antal delprov vunna: " + ", ".join(f"{k} {v}" for k, v in wins.items()))
        res["_uppdelat"] = split; res["_vinster"] = wins

        ok = {k: v for k, v in res.items() if not k.startswith("_") and "CAGR_%" in v}
        ref = ok.get("0. Satellit (dagens): band 10/30, stopp 15, dagligen", {})
        print(f"\n   Dagens modell: {ref.get('CAGR_%')} %, Sharpe {ref.get('Sharpe')}, innehav {ref.get('innehavstid_dagar')} d")
        print(f"   Målet för kärnan: innehavstid över 200 dagar och avkastning inom 1 pe från dagens. Nedgången blir djupare – det är priset.")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "läge": "kärnlager",
               "portfölj_sek": a.port, "år": round(yrs, 1), "referens": ref.get("CAGR_%"), "varianter": res, "varning": "Survivorship bias."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Momentumfönster: väger gammal uppgång lika tungt som ny? ----------
    if a.momwin:
        B = dict(voladj=False, n=20, band_out=30, reweight=True, reb="Q", refill_days=21, stop=15,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "0. 12-1 (standard – Jegadeesh & Titman)":   dict(B, mom_win="12-1"),
            "A. 6-1 (senaste halvåret)":                 dict(B, mom_win="6-1"),
            "B. 3-1 (senaste kvartalet)":                dict(B, mom_win="3-1"),
            "C. Blandat: hälften 12-1, hälften 6-1":     dict(B, mom_win="mix"),
            "D. Viktat: senaste halvåret dubbelt":       dict(B, mom_win="weighted"),
            "E. 12-1 men kräver 6-1 > 0":                dict(B, mom_win="12-1 kräver 6-1>0"),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Momentumfönster, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: ska uppgång för 6–12 månader sedan väga lika tungt som uppgång nyligen?")
        print(f"   (Fallet HTRO: +110 % på 12 mån men +9 % på 6 – rankas över AMD med +92 % på 6 mån)\n")
        print(f"   {'likaviktat snitt av universumet':<48}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                print(f"   {nm:<48}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<48} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        # --- håller resultatet i olika perioder och marknader? ---
        print(f"\n   === UPPDELAT: 12-1 mot 3-1 mot blandat, per period och marknad (Sharpe) ===")
        def group(cols):
            us, nordic, eur = [], [], []
            for t in cols:
                if any(t.endswith(x) for x in (".ST", ".CO", ".OL", ".HE")): nordic.append(t)
                elif "." in t: eur.append(t)
                else: us.append(t)
            return {"USA": us, "Norden": nordic, "Europa": eur}
        grp = group(list(close.columns)); idx = close.index; mid = idx[len(idx) // 2]
        segs = {"Hela": (idx[0], idx[-1]), "Första halvan": (idx[0], mid), "Andra halvan": (mid - pd.Timedelta(days=430), idx[-1])}
        SUB = {"12-1": VAR["0. 12-1 (standard – Jegadeesh & Titman)"], "3-1": VAR["B. 3-1 (senaste kvartalet)"],
               "blandat": VAR["C. Blandat: hälften 12-1, hälften 6-1"]}
        wins = {k: 0 for k in SUB}; split = {}
        print(f"   {'delprov':<28}{'aktier':>7}" + "".join(f"{k:>12}" for k in SUB) + "   vinnare")
        for segn, (s0, s1) in segs.items():
            for un, cols in [("Alla", list(close.columns))] + list(grp.items()):
                cols = [c for c in cols if c in close.columns]
                if len(cols) < 40: continue
                sub = close.loc[s0:s1, cols]; subv = vol.loc[s0:s1, cols]
                if len(sub) < 500: continue
                y2 = (sub.index[-1] - sub.index[min(210, len(sub) - 1)]).days / 365.25
                if y2 < 1.5: continue
                row = {}
                for k, over in SUB.items():
                    cfg = dict(DEF); cfg.update(over)
                    try:
                        e, tr, hd = run(sub, subv, cfg); st = stats(e, tr, y2, hd); row[k] = st["Sharpe"]
                    except Exception: row[k] = None
                good = {k: v for k, v in row.items() if v is not None}
                if not good: continue
                w = max(good, key=good.get); wins[w] += 1; split[f"{segn} / {un}"] = row
                print(f"   {segn + ' / ' + un:<28}{len(cols):>7}" + "".join(f"{(row[k] if row[k] is not None else float('nan')):>12.2f}" for k in SUB) + f"   {w}")
        print(f"   Antal delprov vunna: " + ", ".join(f"{k} {v}" for k, v in wins.items()))
        res["_uppdelat"] = split; res["_vinster"] = wins

        ok = {k: v for k, v in res.items() if not k.startswith("_") and "CAGR_%" in v}
        ref = ok.get("0. 12-1 (standard – Jegadeesh & Titman)", {})
        best = max(ok, key=lambda k: ok[k]["Sharpe"], default=None)
        print(f"\n   12-1: {ref.get('CAGR_%')} %, Sharpe {ref.get('Sharpe')}")
        if best: print(f"   Bäst Sharpe: {best} ({ok[best]['CAGR_%']} %, Sharpe {ok[best]['Sharpe']})")
        print(f"   Trettio års forskning säger 12-1. Kortare fönster brukar ge mer brus och fler byten.")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "momentumfönster", "portfölj_sek": a.port, "år": round(yrs, 1),
               "referens": ref.get("CAGR_%"), "bäst": best, "varianter": res, "varning": "Survivorship bias."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Stopp vid stöd: dokumentets förslag mot vårt validerade procentstopp ----------
    if a.srstop:
        B = dict(voladj=False, n=20, band_out=30, reweight=True, reb="Q", refill_days=21,
                 w_mom=.80, w_trend=.20, w_vol=0, heat=True,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "0. Fast stopp 15 % (validerat)":                       dict(B, stop=15),
            "A. Stöd 6 mån, ≥2 träffar, 0,4 ATR under (tak 15 %)":  dict(B, stop=15, stop_mode="support", sr_win=130),
            "B. Stöd 12 mån, ≥2 träffar (tak 15 %)":                dict(B, stop=15, stop_mode="support", sr_win=250),
            "C. Stöd 6 mån, ≥3 träffar (tak 15 %)":                 dict(B, stop=15, stop_mode="support", sr_win=130, sr_min_touch=3),
            "D. Stöd 6 mån, 1 ATR under (tak 15 %)":                dict(B, stop=15, stop_mode="support", sr_buf_atr=1.0),
            "E. Stöd 6 mån, tak 20 % (lösare nät)":                 dict(B, stop=20, stop_mode="support", sr_win=130),
            "F. Stöd 6 mån, tak 25 %":                               dict(B, stop=25, stop_mode="support", sr_win=130),
            "G. Stöd 6 mån, INGET tak":                              dict(B, stop=0, stop_mode="support", sr_win=130),
            "H. Fast stopp 20 % (jämförelse)":                      dict(B, stop=20),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Stopp vid stöd, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: är ett stopp som följer aktiens stödnivåer bättre än ett fast procentstopp?\n")
        print(f"   {'likaviktat snitt av universumet':<52}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                ut = st.get("utgångar", {}); srn = ut.get("stopp vid stöd", 0); pn = ut.get("stop", 0)
                print(f"   {nm:<52}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  innehav {st['innehavstid_dagar']} d  stopp {srn or pn}")
            except Exception as ex:
                print(f"   {nm:<52} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        ok = {k: v for k, v in res.items() if k != "_snitt" and "CAGR_%" in v}
        ref = ok.get("0. Fast stopp 15 % (validerat)", {})
        best = max(ok, key=lambda k: ok[k]["CAGR_%"], default=None)
        print(f"\n   Fast 15 %: {ref.get('CAGR_%')} %, Sharpe {ref.get('Sharpe')}")
        if best: print(f"   Bäst: {best} ({ok[best]['CAGR_%']} %, Sharpe {ok[best]['Sharpe']})")
        print(f"   Med EODHD är universumet detsamma varje körning – skillnader under ~1 pe är ändå tveksamma.")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "stopp vid stöd", "portfölj_sek": a.port, "år": round(yrs, 1),
               "referens": ref.get("CAGR_%"), "bäst": best, "varianter": res,
               "varning": "Survivorship bias. Stödnivåerna räknas på stängningskurser, utan intradag."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Poängtest: är appens formel bättre än den backtestet validerat? ----------
    if a.score:
        B = dict(voladj=False, n=20, band_out=30, stop=15, reweight=True, reb="Q",
                 refill_days=21, port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "0. BACKTESTETS formel: mom 65 + trend 35":        dict(B, w_mom=.65, w_trend=.35, w_vol=0, heat=False),
            "A. APPENS formel: mom 55 + trend 30 + volym 15":  dict(B, w_mom=.55, w_trend=.30, w_vol=.15, heat=False),
            "B. Appens formel MED överhettningsavdrag":        dict(B, w_mom=.55, w_trend=.30, w_vol=.15, heat=True),
            "C. Backtestets formel med överhettningsavdrag":   dict(B, w_mom=.65, w_trend=.35, w_vol=0, heat=True),
            "--- komponenterna var för sig ---":               None,
            "D. Enbart momentum (100 %)":                      dict(B, w_mom=1.0, w_trend=0, w_vol=0),
            "E. Enbart trendkvalitet (100 %)":                 dict(B, w_mom=0, w_trend=1.0, w_vol=0),
            "F. Mom 50 + trend 50":                            dict(B, w_mom=.50, w_trend=.50, w_vol=0),
            "G. Mom 80 + trend 20":                            dict(B, w_mom=.80, w_trend=.20, w_vol=0),
            "H. Volym 30 % (dubbelt mot appen)":               dict(B, w_mom=.50, w_trend=.20, w_vol=.30),
            "--- övriga otestade regler ---":                  None,
            "I. Appens formel + RSI-tak 80 vid köp":           dict(B, w_mom=.55, w_trend=.30, w_vol=.15, heat=True, buy_rsi=80),
            "J. Appens formel + RSI-tak 70":                   dict(B, w_mom=.55, w_trend=.30, w_vol=.15, heat=True, buy_rsi=70),
            "K. ALLT som appen gör (formel+heat+RSI)":         dict(B, w_mom=.55, w_trend=.30, w_vol=.15, heat=True, buy_rsi=80),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Poängmodell, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: validerar backtestet appens rankning, eller en annan modell?")
        print(f"   (fundamenta, 38 % av appens poäng, går inte att testa – Yahoo saknar historik)\n")
        print(f"   {'likaviktat snitt av universumet':<52}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None:
                print(f"\n   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                print(f"   {nm:<52}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<52} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        ok = {k: v for k, v in res.items() if k != "_snitt" and "CAGR_%" in v}
        ref = ok.get("0. BACKTESTETS formel: mom 65 + trend 35", {}).get("CAGR_%", 0)
        app = ok.get("K. ALLT som appen gör (formel+heat+RSI)", {}).get("CAGR_%", 0)
        print(f"\n   Backtestets formel (allt vi validerat hittills): {ref} %")
        print(f"   Appens tekniska formel i sin helhet: {app} %   → skillnad {round(app - ref, 2)} procentenheter")
        best = max(ok, key=lambda k: ok[k]["CAGR_%"], default=None)
        if best: print(f"   Bäst: {best} ({ok[best]['CAGR_%']} %)")
        print(f"   (slumpvariationen mellan körningar är 2–3 procentenheter)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "poängmodell", "portfölj_sek": a.port, "år": round(yrs, 1),
               "backtestets_formel": ref, "appens_formel": app, "bäst": best, "varianter": res,
               "varning": "Fundamenta (38 % av appens poäng) kan inte testas. Slumpvariation 2–3 procentenheter."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Vinstmålstest: kapar regeln högpresterande aktier? ----------
    if a.tp:
        B = dict(voladj=False, n=20, band_out=30, stop=15, reweight=True, reb="Q",
                 refill_days=21, port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "0. INGET vinstmål (så backtestet mätts hittills)": dict(B),
            "A. +35 %, sälj halva (APPENS STANDARD)":           dict(B, tp=35, tp_sell=50),
            "B. +35 %, sälj en tredjedel":                      dict(B, tp=35, tp_sell=33),
            "C. +35 %, sälj allt":                              dict(B, tp=35, tp_sell=100),
            "D. +50 %, sälj halva":                             dict(B, tp=50, tp_sell=50),
            "E. +75 %, sälj halva":                             dict(B, tp=75, tp_sell=50),
            "F. +100 %, sälj halva":                            dict(B, tp=100, tp_sell=50),
            "G. +150 %, sälj halva":                            dict(B, tp=150, tp_sell=50),
            "H. +100 %, sälj en tredjedel":                     dict(B, tp=100, tp_sell=33),
            "I. +20 %, sälj halva (mycket tidigt)":             dict(B, tp=20, tp_sell=50),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Vinstmål, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   Frågan: kapar vinstmålet de aktier som hade fortsatt stiga?\n")
        print(f"   {'likaviktat snitt av universumet':<52}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                st["år"] = yearly(e); res[nm] = st
                ntp = sum(1 for x in tr if x[0] == "tp")
                print(f"   {nm:<52}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  innehav {st['innehavstid_dagar']} d  vinsthemtagningar {ntp}")
            except Exception as ex:
                print(f"   {nm:<52} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        ok = {k: v for k, v in res.items() if k != "_snitt" and "CAGR_%" in v}
        ref = ok.get("0. INGET vinstmål (så backtestet mätts hittills)", {}).get("CAGR_%", 0)
        app = ok.get("A. +35 %, sälj halva (APPENS STANDARD)", {}).get("CAGR_%", 0)
        print(f"\n   Utan vinstmål: {ref} %")
        print(f"   Appens standard (+35 %, halva): {app} %   → skillnad {round(app - ref, 2)} procentenheter")
        best = max(ok, key=lambda k: ok[k]["CAGR_%"], default=None)
        if best: print(f"   Bäst: {best} ({ok[best]['CAGR_%']} %)")
        print(f"   (slumpvariationen mellan körningar är 2–3 procentenheter)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "vinstmål", "portfölj_sek": a.port, "år": round(yrs, 1),
               "utan_vinstmål": ref, "appens_standard": app, "bäst": best, "varianter": res,
               "varning": "Survivorship bias. Slumpvariationen mellan körningar är 2–3 procentenheter."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Försämringstest: upptäcker måtten att momentum mattas av, i tid? ----------
    if a.decay:
        B = dict(voladj=False, n=20, band_out=30, stop=15, reweight=True, reb="Q",
                 refill_days=21, port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "0. Referens: dagens modell":                      dict(B),
            "--- som UTGÅNG (sälj när måttet försämras) ---":  None,
            "A. Volymbalans faller (OBV under -1)":             dict(B, exit_obv=-1.0),
            "B. Volymbalans faller kraftigt (OBV under -2)":    dict(B, exit_obv=-2.0),
            "C. 25 % under ATH och försämrats 10 p på en mån":  dict(B, exit_ath=25, ath_worse_min=10),
            "D. 35 % under ATH och försämrats 10 p":            dict(B, exit_ath=35, ath_worse_min=10),
            "E. 3 veckor i rad med lägre toppar":               dict(B, exit_lower=3),
            "F. 4 veckor i rad med lägre toppar":               dict(B, exit_lower=4),
            "G. OBV under -1 OCH 3 veckor lägre toppar":        dict(B, exit_obv=-1.0, exit_lower=3),
            "--- som KÖPFILTER (köp inte när måttet är dåligt) ---": None,
            "H. Köp inte när volymbalansen faller":             dict(B, buy_obv=-1.0),
            "I. Köp inte mer än 25 % under ATH":                dict(B, buy_ath=25),
            "J. Köp inte mer än 15 % under ATH":                dict(B, buy_ath=15),
            "K. Köp inte vid 3 veckor lägre toppar":            dict(B, buy_lower=3),
            "L. Alla tre köpfiltren":                           dict(B, buy_obv=-1.0, buy_ath=25, buy_lower=3),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Försämringsmått, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   {'likaviktat snitt av universumet':<52}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}")
        res = {"_snitt": bst}
        for nm, over in VAR.items():
            if over is None:
                print(f"\n   {nm}"); continue
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                ut = st.get("utgångar", {})
                extra = " ".join(f"{k}:{v}" for k, v in ut.items() if k in ("obv", "ath", "lägre toppar"))
                print(f"   {nm:<52}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d  {extra}")
            except Exception as ex:
                print(f"   {nm:<52} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        ok = {k: v for k, v in res.items() if k != "_snitt" and "CAGR_%" in v}
        ref = ok.get("0. Referens: dagens modell", {}).get("CAGR_%", 0)
        bet = [k for k in ok if ok[k]["CAGR_%"] > ref + 1]
        print(f"\n   Referensen gav {ref} %.")
        print(f"   Varianter som slog den med mer än 1 procentenhet: {', '.join(bet) if bet else 'INGEN'}")
        print(f"   (slumpvariationen mellan körningar är 2–3 procentenheter – var skeptisk)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "försämring", "portfölj_sek": a.port, "år": round(yrs, 1),
               "referens": ref, "bättre_än_referens": bet, "varianter": res,
               "varning": "Survivorship bias. Slumpvariationen mellan körningar är 2–3 procentenheter."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Koncentrationstest: få innehav som får löpa tills momentum tar slut ----------
    if a.conc:
        B = dict(voladj=False, reweight=True, reb="Q", refill_days=21,
                 port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False)
        VAR = {
            "1 innehav, inget stopp, bara band":        dict(B, n=1,  band_out=10, stop=0),
            "2 innehav, inget stopp":                   dict(B, n=2,  band_out=12, stop=0),
            "3 innehav, inget stopp":                   dict(B, n=3,  band_out=15, stop=0),
            "5 innehav, inget stopp":                   dict(B, n=5,  band_out=20, stop=0),
            "3 innehav, stopp 25 %":                    dict(B, n=3,  band_out=15, stop=25),
            "5 innehav, stopp 25 %":                    dict(B, n=5,  band_out=20, stop=25),
            "5 innehav, stopp 15 %":                    dict(B, n=5,  band_out=20, stop=15),
            "5 innehav, inget stopp, SMALT band 10":    dict(B, n=5,  band_out=10, stop=0),
            "5 innehav, inget stopp, BRETT band 50":    dict(B, n=5,  band_out=50, stop=0),
            "10 innehav, inget stopp":                  dict(B, n=10, band_out=25, stop=0),
            "10 innehav, stopp 15 %":                   dict(B, n=10, band_out=25, stop=15),
            "20 innehav, stopp 15 % (dagens modell)":   dict(B, n=20, band_out=30, stop=15),
            "20 innehav, inget stopp":                  dict(B, n=20, band_out=30, stop=0),
        }
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Koncentration, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   OBS: jämförelsemåttet nedan är ett LIKAVIKTAT GENOMSNITT av universumet,")
        print(f"   inte ett riktigt index. Survivorship bias gör det (och modellen) för optimistiskt.\n")
        print(f"   {'likaviktat snitt av universumet':<44}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}  nedgång {bst['max_nedgång_%']} %")
        res = {"_snitt": bst}
        yr_all = {}
        for nm, over in VAR.items():
            cfg = dict(DEF); cfg.update(over)
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                st["år"] = yearly(e); res[nm] = st; yr_all[nm] = st["år"]
                print(f"   {nm:<44}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>5}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<44} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        # år för år: det är här ojämnheten syns
        yrs_list = sorted({y for v in yr_all.values() for y in v})
        if yrs_list:
            print(f"\n   --- AVKASTNING ÅR FÖR ÅR (%) ---")
            print("   " + "variant".ljust(44) + "".join(f"{y:>8}" for y in yrs_list))
            for nm in VAR:
                if nm not in yr_all: continue
                print("   " + nm.ljust(44) + "".join(f"{yr_all[nm].get(y,0):>8.0f}" for y in yrs_list))
            print(f"\n   --- SÄMSTA ÅR OCH ANTAL FÖRLUSTÅR ---")
            for nm in VAR:
                if nm not in yr_all: continue
                v = list(yr_all[nm].values())
                print(f"   {nm:<44} sämsta år {min(v):>7.0f} %   förlustår {sum(1 for x in v if x<0)} av {len(v)}")
        ok = {k: v for k, v in res.items() if k != "_snitt" and "CAGR_%" in v}
        b1 = max(ok, key=lambda k: ok[k]["CAGR_%"], default=None)
        if b1: print(f"\n   HÖGST AVKASTNING: {b1}  ({ok[b1]['CAGR_%']} %, nedgång {ok[b1]['max_nedgång_%']} %)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "koncentration", "portfölj_sek": a.port, "år": round(yrs, 1),
               "bäst_avkastning": b1, "varianter": res,
               "varning": "Jämförelsemåttet är ett likaviktat snitt av universumet, inte ett riktigt index. "
                          "Survivorship bias gör alla siffror för optimistiska."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

    # ---------- Frekvenstest: hur ofta ska man sätta sig och handla? ----------
    if a.freq:
        BASE = dict(voladj=False, stop=15, reweight=True, n=20, band_out=30, reb="Q",
                    port_sek=a.port, cour_pct=0.0009, fx_pct=0.00266, simple_cost=False,
                    cost_adj=True)
        sc_panel, _ = score_panel(close, vol, dict(DEF, voladj=False))
        slope, nobs = score_to_return(close, sc_panel, 63)
        if slope is None: slope = 0.0
        print(f"\n   1 poäng ≈ {slope:.4f} % per kvartal ({nobs} mätpunkter)")
        FR = [("Varje dag", 1), ("Varannan dag", 2), ("Var tredje dag", 3),
              ("En gång i veckan", 5), ("Varannan vecka", 10), ("En gång i månaden", 21),
              ("25 dagar", 25), ("28 dagar", 28), ("30 dagar (misstänkt topp)", 30),
              ("32 dagar", 32), ("35 dagar", 35), ("40 dagar", 40), ("45 dagar", 45),
              ("En gång i kvartalet", 63), ("Aldrig mellan ombalanseringarna", 99999)]
        yrs = (close.index[-1] - close.index[210]).days / 365.25
        be, bst = benchmark(close, yrs)
        print(f"\n########## Handelstakt, portfölj {a.port:,.0f} kr  ({close.shape[1]} aktier) ##########")
        print(f"   {'index (köp och behåll)':<36}{bst['CAGR_%']:>7} %  Sharpe {bst['Sharpe']}  nedgång {bst['max_nedgång_%']} %")
        res = {"_index": bst}
        for nm, rf in FR:
            cfg = dict(DEF); cfg.update(BASE); cfg["slope"] = slope
            cfg["refill_days"] = rf
            if rf > 1000: cfg["refill"] = False
            try:
                e, tr, hd = run(close, vol, cfg); st = stats(e, tr, yrs, hd)
                res[nm] = st
                print(f"   {nm:<36}{st['CAGR_%']:>7} %  Sharpe {st['Sharpe']}  nedgång {st['max_nedgång_%']} %"
                      f"  köp/år {st['köp_per_år']:>6}  innehav {st['innehavstid_dagar']} d")
            except Exception as ex:
                print(f"   {nm:<36} FEL {str(ex)[:50]}"); res[nm] = {"fel": str(ex)[:120]}
        # Kontroll: samma intervall men utan fast rutnät. Består toppen vid 30 dagar är
        # effekten verklig; försvinner den var den en artefakt av räknerytmen.
        print(f"\n   --- Samma intervall med SLUMPMÄSSIG fördröjning (kontroll) ---")
        res["_jitter"] = {}
        for nm, rf in [x for x in FR if 20 <= x[1] <= 45]:
            vals = []
            for sd in (1, 2, 3):
                cfg = dict(DEF); cfg.update(BASE); cfg["slope"] = slope
                cfg["refill_days"] = rf; cfg["refill_jitter"] = True; cfg["seed"] = sd
                try:
                    e, tr, hd = run(close, vol, cfg); vals.append(stats(e, tr, yrs, hd)["CAGR_%"])
                except Exception: pass
            if vals:
                m = sum(vals) / len(vals)
                res["_jitter"][nm] = {"snitt": round(m, 2), "körningar": vals}
                print(f"   {nm:<36}{m:>7.2f} %  (tre slumpstarter: {', '.join(f'{v:.1f}' for v in vals)})")
        ok = {k: v for k, v in res.items() if k != "_index" and "CAGR_%" in v}
        b1 = max(ok, key=lambda k: ok[k]["CAGR_%"], default=None)
        b2 = max(ok, key=lambda k: (ok[k]["Sharpe"] or 0), default=None)
        if b1: print(f"\n   HÖGST AVKASTNING: {b1}  ({ok[b1]['CAGR_%']} %, {ok[b1]['köp_per_år']} köp/år)")
        if b2: print(f"   BÄST RISKJUSTERAT: {b2}  (Sharpe {ok[b2]['Sharpe']}, {ok[b2]['CAGR_%']} %)")
        out = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "läge": "frekvens", "portfölj_sek": a.port, "år": round(yrs, 1),
               "bäst_avkastning": b1, "bäst_sharpe": b2, "poäng_per_procent": round(slope, 5),
               "varianter": res, "varning": "Survivorship bias och ingen fundamenta."}
        with open(a.out, "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\nSkrev {a.out}"); return

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
