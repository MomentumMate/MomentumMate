"""MomentumMate – daglig skanner (v2)

Kör:  python scanner.py        -> skriver data.json
Kräver: pip install -r requirements.txt
Valfritt: export BENZINGA_KEY=...   (Benzinga news API)

Lägga till en ny börs:  se MARKETS nedan (en rad per börs).
Lägga till enstaka aktier (t.ex. till din portfölj): skriv Yahoo-tickern i extra_tickers.txt.
"""
import json, os, io, re, sys, time, math, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
import pandas as pd, requests, yfinance as yf

UA = {"User-Agent": "Mozilla/5.0"}
BENZ = os.getenv("BENZINGA_KEY")
CHUNK = 60          # tickers per nedladdning
WORKERS = 5         # parallella anrop för info/nyheter
NEWS_KEEP = 6

# ----------------------------------------------------------------------------------
# TICKERLISTOR
# ----------------------------------------------------------------------------------
OMXS = ["VOLV-B.ST","ERIC-B.ST","HM-B.ST","ABB.ST","ATCO-A.ST","ATCO-B.ST","SEB-A.ST","SWED-A.ST","SHB-A.ST","INVE-B.ST",
        "SAND.ST","ASSA-B.ST","EVO.ST","SINCH.ST","NIBE-B.ST","HEXA-B.ST","ALFA.ST","ESSITY-B.ST","BOL.ST","SKA-B.ST",
        "TELIA.ST","SAAB-B.ST","GETI-B.ST","EPI-A.ST","KINV-B.ST","LIFCO-B.ST","ELUX-B.ST","SCA-B.ST","SSAB-A.ST","SKF-B.ST",
        "TEL2-B.ST","SECU-B.ST","LUND-B.ST","INDT.ST","EMBRAC-B.ST","FABG.ST","CAST.ST","SBB-B.ST","BALD-B.ST","LATO-B.ST",
        "AZN.ST","NDA-SE.ST","ALIV-SDB.ST","HUSQ-B.ST","TREL-B.ST","SWEC-B.ST","PEAB-B.ST","AXFO.ST","BILL.ST","AAK.ST",
        "SOBI.ST","WALL-B.ST","FPAR-A.ST","LOOMIS.ST","VOLCAR-B.ST","THULE.ST","NCC-B.ST","HPOL-B.ST","SAGA-B.ST","MTRS.ST"]
SP500_FALLBACK = ["AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","BRK-B","AVGO","TSLA","LLY","JPM","V","XOM","UNH","MA","COST",
        "HD","PG","NFLX","JNJ","CRM","ABBV","BAC","ORCL","KO","AMD","PEP","WMT","CVX","TMO","ADBE","MRK","LIN","CSCO","ACN",
        "MCD","ABT","WFC","GE","IBM","PM","NOW","INTU","TXN","CAT","ISRG","DIS","VZ","QCOM","AMAT"]
NASDAQ_FALLBACK = ["SPCX","NBIS","CRWV","ALAB","RKLB","TER","AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","AVGO","TSLA","COST","NFLX","TMUS","ASML","CSCO","AZN","LIN","PEP","ADBE","AMD","PLTR",
        "TXN","QCOM","INTU","ISRG","AMGN","BKNG","HON","AMAT","ARM","PANW","ADP","GILD","VRTX","CMCSA","ADI","MU","LRCX","KLAC","APP","MELI","SBUX","CRWD",
        "INTC","CEG","MSTR","CDNS","DASH","SNPS","PYPL","MDLZ","REGN","CTAS","ORLY","MAR","MRVL","WDAY","ADSK","ABNB","CSX","FTNT","PDD","NXPI","ROP","AEP",
        "ROST","PCAR","FAST","KDP","PAYX","EXC","XEL","CCEP","IDXX","TTWO","EA","BKR","CPRT","ODFL","DDOG","GEHC","MNST","LULU","KHC","CSGP",
        "ON","FANG","CDW","BIIB","TTD","MDB","GFS","WBD","DXCM","TEAM","LIN","MCHP","AXON","SHOP","TRI","ISRG","EXE","FER"]
DOW_FALLBACK = ["AAPL","AMGN","AMZN","AXP","BA","CAT","CRM","CSCO","CVX","DIS","GS","HD","HON","IBM","JNJ","JPM","KO","MCD","MMM",
        "MRK","MSFT","NKE","NVDA","PG","SHW","TRV","UNH","V","WMT","GOOGL"]

LIST_WARN = {}
CACHE_FILE = "lists_cache.json"
EXPECT = {"SP500": (480, 520), "NASDAQ": (95, 112), "DOW": (28, 32), "SP400": (380, 420), "SP600": (550, 650)}   # rimliga storlekar – annars litar vi inte på listan
LISTS_META = {}

def _cache_load():
    try: return json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception: return {}
_CACHE = _cache_load()
def _today(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

def register_list(key, tickers, src):
    """Godkänner en nyhämtad lista om storleken är rimlig, sparar den som 'senast kända bra' och loggar ändringar mot förra listan."""
    lo, hi = EXPECT.get(key, (1, 10**6))
    if not (lo <= len(tickers) <= hi): return False
    old = (_CACHE.get(key) or {}).get("t"); ch = (_CACHE.get(key) or {}).get("changes", [])
    if old and set(old) != set(tickers):
        ch = ([{"ts": _today(), "added": sorted(set(tickers) - set(old)), "removed": sorted(set(old) - set(tickers))}] + ch)[:5]
    _CACHE[key] = {"t": list(tickers), "src": src, "ts": _today(), "changes": ch}
    LISTS_META[key] = {"src": src, "ts": _today(), "n": len(tickers), "fb": False}
    LIST_WARN.pop(key, None)
    return True

def use_cache_or_fallback(key, fallback, why=""):
    """Alla live-källor misslyckades: använd senast lyckade lista (hellre än min inbyggda), annars den inbyggda."""
    c = _CACHE.get(key)
    if c and c.get("t"):
        LISTS_META[key] = {"src": f"senast lyckade hämtning ({c['src']})", "ts": c["ts"], "n": len(c["t"]), "fb": True}
        LIST_WARN[key] = f"live-källor misslyckades – använder lista från {c['ts']}. {why}"[:160]
        return list(c["t"])
    LISTS_META[key] = {"src": "inbyggd reservlista", "ts": "okänd", "n": len(fallback), "fb": True}
    LIST_WARN[key] = f"live-källor misslyckades och ingen sparad lista finns – inbyggd reservlista. {why}"[:160]
    return fallback

def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f: json.dump(_CACHE, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e: print("Varning: kunde inte spara listcache:", e)

def _wiki(url, cols, fallback, minimum, key=None):
    """Hämtar tickers från en Wikipedia-tabell. Letar i alla tabeller efter en kolumn vars namn innehåller något i `cols`."""
    try:
        r = requests.get(url, headers=UA, timeout=20); r.raise_for_status()
        best, seen = [], []
        for t in pd.read_html(io.StringIO(r.text), flavor="lxml"):
            t = t.copy()
            t.columns = [" ".join(str(x) for x in c if "Unnamed" not in str(x)).strip() if isinstance(c, tuple) else str(c) for c in t.columns]
            seen.append([c[:18] for c in list(t.columns)[:5]])
            for c in t.columns:
                if any(k in c.lower() for k in cols):
                    vals = []
                    for x in t[c]:
                        if not isinstance(x, str) or not x.strip(): continue
                        v = x.split(":")[-1].strip().split()[0]
                        if re.fullmatch(r"[A-Za-z0-9.\-]{1,8}", v): vals.append(v.replace(".", "-"))
                    if len(vals) > len(best): best = vals
        if len(best) >= minimum:
            res = list(dict.fromkeys(best))
            if key and not register_list(key, res, "Wikipedia"): raise ValueError(f"listan har orimlig storlek ({len(res)})")
            return res
        raise ValueError(f"ingen lämplig tabell (hittade {len(best)} tickers, behövde {minimum}; tabellkolumner: {seen[:5]})")
    except Exception as e:
        print("Varning: Wikipedia-hämtning misslyckades (", url.split("/")[-1], "):", e, "– använder reservlista.")
        return use_cache_or_fallback(key, fallback, str(e)[:100]) if key else fallback

def sp500():    return _wiki("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ["symbol", "ticker"], SP500_FALLBACK, 400, key="SP500")
NASDAQ_ALWAYS = ["SPCX", "NBIS", "CRWV", "ALAB", "RKLB", "TER"]   # bekräftade medlemmar 2026 (SPCX: snabbinträde efter börsnoteringen 12 juni; övriga: Nasdaq-100 omviktning 22 juni)

def nasdaq_api():
    """Nasdaq-100 direkt från Nasdaqs egen lista (reserv om Wikipedia inte går att läsa)."""
    try:
        r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100", headers=dict(UA, Accept="application/json"), timeout=20); r.raise_for_status()
        return [x["symbol"].replace(".", "-") for x in r.json()["data"]["data"]["rows"] if x.get("symbol")]
    except Exception as e:
        print("Varning: Nasdaq API misslyckades:", e); return []

def nasdaq100():
    api = nasdaq_api()
    if register_list("NASDAQ", api, "Nasdaq API"): got = api; print("  Nasdaq-100 hämtad från Nasdaq API")
    else: got = _wiki("https://en.wikipedia.org/wiki/Nasdaq-100", ["ticker", "symbol"], NASDAQ_FALLBACK, 80, key="NASDAQ")
    return list(dict.fromkeys(got + NASDAQ_ALWAYS))

def dow30():    return _wiki("https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies", ["symbol", "ticker"], DOW_FALLBACK, 25, key="DOW")

def _nasdaqtrader(url):
    r = requests.get(url, headers=UA, timeout=30); r.raise_for_status()
    lines = [l for l in r.text.splitlines() if l.strip() and not l.startswith("File Creation Time")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str, keep_default_na=False)

_GOOD = re.compile(r"common stock|ordinary shares?|common shares?|american depositary shares?|class [a-z] (common|ordinary)", re.I)
_BAD = re.compile(r"warrant|\brights?\b|\bunits?\b|preferred|notes? due|trust\b|\bfund\b|\betf\b|\betn\b|acquisition corp|depositary shares each", re.I)

def nasdaq_all():
    """Alla vanliga aktier noterade på Nasdaq (officiell symbollista från Nasdaq Trader)."""
    try:
        df = _nasdaqtrader("https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"); out = []
        for _, r in df.iterrows():
            sym, name = r["Symbol"].strip(), r["Security Name"]
            if r.get("ETF", "N") != "N" or r.get("Test Issue", "N") != "N" or r.get("Financial Status", "N") not in ("N", ""): continue
            if re.fullmatch(r"[A-Z]{1,5}", sym) and _GOOD.search(name) and not _BAD.search(name): out.append(sym)
        print(f"  Nasdaq-listan: {len(out)} aktier")
        return out
    except Exception as e:
        print("Varning: kunde inte hämta Nasdaq-listan:", e); return []

def nyse_all():
    """Alla vanliga aktier på NYSE och NYSE American."""
    try:
        df = _nasdaqtrader("https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"); out = []
        for _, r in df.iterrows():
            sym, name = r["ACT Symbol"].strip().replace(".", "-"), r["Security Name"]
            if r.get("Exchange") not in ("N", "A") or r.get("ETF", "N") != "N" or r.get("Test Issue", "N") != "N": continue
            if re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", sym) and _GOOD.search(name) and not _BAD.search(name): out.append(sym)
        print(f"  NYSE-listan: {len(out)} aktier")
        return out
    except Exception as e:
        print("Varning: kunde inte hämta NYSE-listan:", e); return []

def sp400(): return _wiki("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", ["symbol", "ticker"], [], 300, key="SP400")
def sp600(): return _wiki("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", ["symbol", "ticker"], [], 400, key="SP600")

LIGHT_TOP = 120            # antal aktier per hel börs som får full analys
LIGHT_MIN_DV = 3_000_000   # lägsta genomsnittliga omsättning per dag (i handelsvaluta) för full analys

# ---- Förinställda börser (kan läggas till från appen med en knapp, eller sättas som standard nedan) ----
PRESETS = {
    "OMXC": {"n": "OMX Köpenhamn", "flag": "🇩🇰", "cur": "kr", "ccy": "DKK", "reg": "EU", "tickers": [
        "NOVO-B.CO","DSV.CO","MAERSK-B.CO","MAERSK-A.CO","VWS.CO","ORSTED.CO","CARL-B.CO","COLO-B.CO","GMAB.CO","DANSKE.CO","PNDORA.CO","TRYG.CO",
        "NSIS-B.CO","DEMANT.CO","GN.CO","FLS.CO","ROCK-B.CO","AMBU-B.CO","ISS.CO","JYSK.CO","NKT.CO","RBREW.CO","BAVA.CO","ZEAL.CO","ALMB.CO","SYDB.CO","TOP.CO","NETC.CO"]},
    "OSE": {"n": "Oslo Børs", "flag": "🇳🇴", "cur": "kr", "ccy": "NOK", "reg": "EU", "tickers": [
        "EQNR.OL","DNB.OL","MOWI.OL","TEL.OL","ORK.OL","YAR.OL","NHY.OL","AKRBP.OL","SALM.OL","SUBC.OL","STB.OL","GJF.OL","KOG.OL","TOM.OL","NAS.OL",
        "AUTO.OL","FRO.OL","BAKKA.OL","AKER.OL","SCATC.OL","KIT.OL","LSG.OL","ENTRA.OL","HAFNI.OL","VAR.OL","WAWI.OL","ELK.OL","NOD.OL","TGS.OL","BWLPG.OL","AFG.OL"]},
    "DAX": {"n": "Frankfurt (XETRA)", "flag": "🇩🇪", "cur": "€", "ccy": "EUR", "reg": "EU", "tickers": [
        "ADS.DE","AIR.DE","ALV.DE","BAS.DE","BAYN.DE","BEI.DE","BMW.DE","BNR.DE","CBK.DE","CON.DE","1COV.DE","DTG.DE","DBK.DE","DB1.DE","DHL.DE","DTE.DE",
        "EOAN.DE","FRE.DE","FME.DE","HNR1.DE","HEI.DE","HEN3.DE","IFX.DE","MBG.DE","MRK.DE","MTX.DE","MUV2.DE","P911.DE","PAH3.DE","QIA.DE","RHM.DE","RWE.DE",
        "SAP.DE","SRT3.DE","SIE.DE","ENR.DE","SHL.DE","SY1.DE","VOW3.DE","VNA.DE","ZAL.DE","LHA.DE","TUI1.DE","HFG.DE","AFX.DE","G1A.DE","KGX.DE","LEG.DE"]},
    "CAC": {"n": "Paris (Euronext)", "flag": "🇫🇷", "cur": "€", "ccy": "EUR", "reg": "EU", "tickers": [
        "AC.PA","AI.PA","AIR.PA","ALO.PA","CS.PA","BNP.PA","EN.PA","CAP.PA","CA.PA","ACA.PA","BN.PA","DSY.PA","EDEN.PA","ENGI.PA","EL.PA","ERF.PA","RMS.PA",
        "KER.PA","OR.PA","LR.PA","MC.PA","ML.PA","ORA.PA","RI.PA","PUB.PA","RNO.PA","SAF.PA","SGO.PA","SAN.PA","SU.PA","GLE.PA","STLAP.PA","STMPA.PA","TEP.PA",
        "HO.PA","TTE.PA","VIE.PA","DG.PA","WLN.PA","AM.PA","SW.PA","ATO.PA","UBI.PA"]},
    "FTSE": {"n": "London (LSE)", "flag": "🇬🇧", "cur": "p", "ccy": "GBX", "reg": "EU", "tickers": [
        "AZN.L","SHEL.L","HSBA.L","ULVR.L","BP.L","RIO.L","GSK.L","DGE.L","BATS.L","LSEG.L","REL.L","GLEN.L","NG.L","CPG.L","BARC.L","LLOY.L","NWG.L","VOD.L",
        "TSCO.L","RR.L","BA.L","PRU.L","AAL.L","ANTO.L","EXPN.L","IMB.L","SSE.L","STAN.L","III.L","LGEN.L","AV.L","BT-A.L","NXT.L","WPP.L","IAG.L","ABF.L","FLTR.L",
        "RKT.L","HLMA.L","CNA.L","SGE.L","SMT.L","SPX.L","WTB.L","MNG.L","AHT.L","BNZL.L","EDV.L","PSON.L","RMV.L","SDR.L","SMIN.L","SN.L","SVT.L","UU.L","WEIR.L",
        "BKG.L","LAND.L","BLND.L","DPLM.L","ENT.L","HIK.L","ICG.L","INF.L","ITRK.L","JD.L","KGF.L","MKS.L","PHNX.L","TW.L"]},
    "CRYPTO": {"n": "Krypto", "flag": "🪙", "cur": "$", "ccy": "USD", "reg": None, "tickers": [
        "BTC-USD","ETH-USD","BNB-USD","XRP-USD","SOL-USD","DOGE-USD","ADA-USD","AVAX-USD","LINK-USD","DOT-USD","LTC-USD","BCH-USD",
        "XLM-USD","ATOM-USD","NEAR-USD","TRX-USD","SHIB-USD","HBAR-USD","ETC-USD","XMR-USD"]},
    "SP400": {"n": "S&P MidCap 400", "flag": "🇺🇸", "cur": "$", "ccy": "USD", "reg": None, "tickers": sp400},
    "SP600": {"n": "S&P SmallCap 600", "flag": "🇺🇸", "cur": "$", "ccy": "USD", "reg": None, "tickers": sp600},
    "NASDAQ_ALL": {"n": "Nasdaq (alla)", "flag": "🇺🇸", "cur": "$", "ccy": "USD", "reg": None, "light": True, "tickers": nasdaq_all},
    "NYSE_ALL": {"n": "NYSE (alla)", "flag": "🇺🇸", "cur": "$", "ccy": "USD", "reg": None, "light": True, "tickers": nyse_all},
    "AEX": {"n": "Euronext Amsterdam", "flag": "🇳🇱", "cur": "€", "ccy": "EUR", "reg": "EU", "tickers": [
        "ASML.AS","PRX.AS","INGA.AS","HEIA.AS","AD.AS","WKL.AS","PHIA.AS","ADYEN.AS","DSFIR.AS","MT.AS","RAND.AS","NN.AS","AKZA.AS","BESI.AS","IMCD.AS","ASM.AS",
        "ABN.AS","KPN.AS","EXO.AS","UMG.AS","BFIT.AS","SBMO.AS","AALB.AS"]},
    "OMXH": {"n": "OMX Helsingfors", "flag": "🇫🇮", "cur": "€", "ccy": "EUR", "reg": "EU", "tickers": [
        "NOKIA.HE","KNEBV.HE","NESTE.HE","FORTUM.HE","UPM.HE","STERV.HE","SAMPO.HE","WRT1V.HE","ELISA.HE","ORNBV.HE","KESKOB.HE","NDA-FI.HE","METSO.HE","HUH1V.HE","KCR.HE","OUT1V.HE","FSKRS.HE"]},
    "SMI": {"n": "Zürich (SMI)", "flag": "🇨🇭", "cur": "CHF", "ccy": "CHF", "reg": "EU", "tickers": [
        "NESN.SW","ROG.SW","NOVN.SW","UBSG.SW","ZURN.SW","ABBN.SW","CFR.SW","SIKA.SW","LONN.SW","ALC.SW","GIVN.SW","HOLN.SW","SREN.SW","SCMN.SW","PGHN.SW","SLHN.SW","GEBN.SW","LOGN.SW"]},
    "IBEX": {"n": "Madrid (IBEX)", "flag": "🇪🇸", "cur": "€", "ccy": "EUR", "reg": "EU", "tickers": [
        "SAN.MC","BBVA.MC","ITX.MC","IBE.MC","TEF.MC","REP.MC","AMS.MC","FER.MC","CABK.MC","SAB.MC","ELE.MC","GRF.MC","ACS.MC","AENA.MC","MAP.MC","IAG.MC","ENG.MC","RED.MC","CLNX.MC","BKT.MC"]},
    "MIB": {"n": "Milano (FTSE MIB)", "flag": "🇮🇹", "cur": "€", "ccy": "EUR", "reg": "EU", "tickers": [
        "ENEL.MI","ISP.MI","UCG.MI","ENI.MI","STLAM.MI","RACE.MI","G.MI","MB.MI","PRY.MI","LDO.MI","TRN.MI","SRG.MI","BAMI.MI","BPE.MI","MONC.MI","CPR.MI","DIA.MI","REC.MI","TIT.MI","AMP.MI"]},
}
DEFAULT_PRESETS = ("OMXC", "OSE", "DAX", "CAC", "FTSE")      # standardbörser utöver OMXS/USA
GROUP_NAMES = {"EU": ("Europa", "🇪🇺")}

def _preset(k):
    p = PRESETS[k]
    return {"k": k, "n": p["n"], "flag": p["flag"], "cur": p["cur"], "ccy": p["ccy"], "reg": p["reg"], "light": bool(p.get("light")), "source": p["tickers"]}

# ---- BÖRSER. Lägg till en rad för att utöka (k = unik nyckel, source = lista eller funktion) ----
MARKETS = [
    {"k": "OMXS",   "n": "OMX Stockholm", "flag": "🇸🇪", "cur": "kr", "ccy": "SEK", "reg": "EU", "source": OMXS},
    {"k": "SP500",  "n": "S&P 500",       "flag": "🇺🇸", "cur": "$",  "ccy": "USD", "source": sp500},
    {"k": "NASDAQ", "n": "Nasdaq-100",    "flag": "🇺🇸", "cur": "$",  "ccy": "USD", "source": nasdaq100},
    {"k": "DOW",    "n": "Dow Jones",     "flag": "🇺🇸", "cur": "$",  "ccy": "USD", "source": dow30},
    # Exempel på fler börser (ta bort # och lägg till egna tickers):
    # {"k": "OMXH", "n": "OMX Helsinki", "flag": "🇫🇮", "cur": "€", "ccy": "EUR", "source": ["NOKIA.HE", "KNEBV.HE"]},
]
MARKETS += [_preset(k) for k in DEFAULT_PRESETS]
FEEDS = [  # allmänna marknadsnyheter (RSS). Misslyckas en källa hoppas den över.
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Dagens industri", "https://www.di.se/rss"),
    ("Google News – marknaden", "https://news.google.com/rss/search?q=stock+market+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Google News – börsen", "https://news.google.com/rss/search?q=b%C3%B6rsen+aktier+when:1d&hl=sv&gl=SE&ceid=SE:sv"),
]

def read_extra():
    try:
        return [l.split("#")[0].strip() for l in open("extra_tickers.txt", encoding="utf-8") if l.split("#")[0].strip()]
    except OSError:
        return []

def get_tickers(m):
    src = m["source"]
    return list(dict.fromkeys(src() if callable(src) else src))

# ----------------------------------------------------------------------------------
# HÄMTNING
# ----------------------------------------------------------------------------------
def retry(fn, tries=3, wait=2):
    for i in range(tries):
        try: return fn()
        except Exception:
            if i == tries - 1: raise
            time.sleep(wait * (i + 1))

def split_frames(df, tickers):
    out = {}
    if df is None or df.empty: return out
    if isinstance(df.columns, pd.MultiIndex):
        lv0 = set(df.columns.get_level_values(0))
        for t in tickers:
            if t in lv0:
                x = df[t].dropna(how="all")
                if len(x): out[t] = x
    else:
        out[tickers[0]] = df.dropna(how="all")
    return out

def dl(tickers, **kw):
    out = {}
    for i in range(0, len(tickers), CHUNK):
        ch = tickers[i:i + CHUNK]
        try:
            df = retry(lambda: yf.download(ch, group_by="ticker", auto_adjust=False, progress=False, threads=True, timeout=45, **kw))
            out.update(split_frames(df, ch))
        except Exception as e:
            print("Varning: nedladdning misslyckades för", ch[:2], "…", e)
        print(f"  nedladdat {min(i + CHUNK, len(tickers))}/{len(tickers)}", flush=True)
    return out

CUR_SYM = {"USD": "$", "EUR": "€", "SEK": "kr", "DKK": "kr", "NOK": "kr", "GBP": "£", "GBX": "p", "CHF": "CHF", "CAD": "$"}
def norm_ccy(c): return {"GBp": "GBX", "GBX": "GBX"}.get(c, c)
def is_crypto(t): return t.endswith("-USD")
def is_us(t): return "." not in t and not is_crypto(t)
SESS = {".L": ("Europe/London", 480), ".SW": ("Europe/Zurich", 540), ".HE": ("Europe/Helsinki", 600), ".CO": ("Europe/Copenhagen", 540),
        ".OL": ("Europe/Oslo", 540), ".ST": ("Europe/Stockholm", 540), ".DE": ("Europe/Berlin", 540), ".PA": ("Europe/Paris", 540),
        ".AS": ("Europe/Amsterdam", 540), ".MC": ("Europe/Madrid", 540), ".MI": ("Europe/Rome", 540)}
def session(t):
    """(tidszon, öppningsminut) för tickerns börs. USA: New York 09:30."""
    if is_us(t): return "America/New_York", 570
    for suf, v in SESS.items():
        if t.endswith(suf): return v
    return "Europe/Stockholm", 540

def get_info(t):
    try: return retry(lambda: yf.Ticker(t).info or {}, tries=2, wait=2)
    except Exception: return {}

def _ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception: return 0

def _clean(s, n=170):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()[:n]

NEWS_DIAG = {"err": 0, "last": ""}
FEED_STATS = {}

def get_news(t):
    items = []
    try:
        for n in (yf.Ticker(t).news or [])[:8]:
            c = n.get("content") or n
            title = c.get("title") or ""
            if not title: continue
            url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link") or "")
            src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"
            ts = _ts(c["pubDate"]) if c.get("pubDate") else int(c.get("providerPublishTime") or 0)
            items.append({"h": title, "s": _clean(c.get("summary") or c.get("description")), "src": src, "u": url, "ts": int(ts)})
    except Exception as e:
        NEWS_DIAG["err"] += 1; NEWS_DIAG["last"] = f"{type(e).__name__}: {e}"[:140]
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x["ts"]):
        k = it["u"] or it["h"]
        if it["h"] and k not in seen:
            seen.add(k); out.append(it)
    return out[:NEWS_KEEP]

def merge_news(*lists):
    seen, out = set(), []
    for it in sorted([x for l in lists for x in l], key=lambda x: -x["ts"]):
        k = it["u"] or it["h"]
        if it["h"] and k not in seen:
            seen.add(k); out.append(it)
    return out[:NEWS_KEEP]

def benzinga_news(t):
    if not BENZ: return []
    try:
        r = requests.get("https://api.benzinga.com/api/v2/news", timeout=10, headers={"accept": "application/json"},
                         params={"token": BENZ, "tickers": t.replace("-", "."), "pageSize": 5, "displayOutput": "abstract"}).json()
        out = []
        for n in r:
            try: ts = int(parsedate_to_datetime(n["created"]).timestamp())
            except Exception: ts = 0
            out.append({"h": n.get("title", ""), "s": _clean(n.get("teaser") or n.get("body")), "src": "Benzinga", "u": n.get("url", ""), "ts": ts})
        return out
    except Exception:
        return []

def fill_benzinga(recs, news, rank):
    """Benzinga hämtas bara för rankade amerikanska aktier (sparar API-anrop)."""
    if not BENZ: return 0, 0
    ids = []
    for k, r in rank.items():
        ids += (r["cand"][:30] + r["near"][:30]) if k == "DT" else (r["mom"] + r["val"])
    ids = [t for t in dict.fromkeys(ids) if t in recs and is_us(t)][:200]
    ok = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        for t, items in ex.map(lambda x: (x, benzinga_news(x)), ids):
            if items: news[t] = merge_news(items, news.get(t, [])); ok += 1
    return len(ids), ok

def enrich(t):
    return t, get_info(t), get_news(t)

def google_rss(url):
    """Google News RSS (gratis, ingen nyckel). Titeln har formen 'Rubrik - Källa'."""
    r = requests.get(url, headers=UA, timeout=12); r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title: continue
        src = (it.findtext("source") or "").strip()
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            title, src = head, (src or tail)
        try: ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception: ts = 0
        out.append({"h": title, "s": "", "src": src or "Google News", "u": (it.findtext("link") or "").strip(), "ts": ts})
    return out

def google_news(q, hl="en-US", gl="US", ceid="US:en"):
    from urllib.parse import quote_plus
    return google_rss(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}")[:NEWS_KEEP]

def fill_news(recs, news, rank):
    """Reservkälla: hämtar Google News för rankade aktier som saknar nyheter från Yahoo."""
    ids = []
    for k, r in rank.items():
        ids += (r["cand"][:30] + r["near"][:30]) if k == "DT" else (r["mom"] + r["val"])
    ids = [t for t in dict.fromkeys(ids) if t in recs and not news.get(t)][:250]
    def one(t):
        name = re.sub(r"\b(Inc|Corp|Corporation|Ltd|plc|AB|ASA|SE|NV|AG|SA|Holdings?|Company)\b\.?", "", recs[t]["n"]).strip(" ,.")
        sv = t.endswith(".ST")
        try:
            return t, (google_news(name + " aktie", "sv", "SE", "SE:sv") if sv else google_news(name + " stock"))
        except Exception:
            return t, []
    ok = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for t, items in ex.map(one, ids):
            if items: news[t] = items; ok += 1
    return len(ids), ok

def fetch_feeds():
    out = []; FEED_STATS.clear()
    for name, url in FEEDS:
        n0 = len(out)
        try:
            if url.startswith("https://news.google.com/"):
                out += [dict(x, src=x["src"] if x["src"] != "Google News" else name) for x in google_rss(url)]
            else:
                r = requests.get(url, headers=UA, timeout=10); r.raise_for_status()
                root = ET.fromstring(r.content)
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    if not title: continue
                    try: ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
                    except Exception: ts = 0
                    out.append({"h": title, "s": _clean(it.findtext("description")), "src": name, "u": (it.findtext("link") or "").strip(), "ts": ts})
            FEED_STATS[name] = len(out) - n0
        except Exception as e:
            FEED_STATS[name] = 0
            print("Varning: RSS-källa misslyckades:", name, e)
    seen, res = set(), []
    for x in sorted(out, key=lambda x: -x["ts"]):
        k = x["u"] or x["h"]
        if k not in seen: seen.add(k); res.append(x)
    return res[:80]

def fx_rates(ccys):
    out = {"SEK": 1.0}
    for c in ccys:
        if c == "SEK": continue
        try:
            base = "GBP" if c == "GBX" else c                     # London handlas i pence (GBX)
            h = yf.Ticker(f"{base}SEK=X").history(period="5d")
            v = float(h["Close"].dropna().iloc[-1])
            out[c] = round(v / 100 if c == "GBX" else v, 4)
        except Exception: pass
    return out

# ----------------------------------------------------------------------------------
# ANALYS
# ----------------------------------------------------------------------------------
POS = ("beat", "upgrade", "raises", "surge", "record", "growth", "strong", "buy", "höjer", "rekord")
NEG = ("miss", "downgrade", "cuts", "falls", "probe", "lawsuit", "weak", "sell", "sänker", "varning")

def sentiment(news):
    s = 0
    for n in news:
        h = n["h"].lower()
        s += any(w in h for w in POS) - any(w in h for w in NEG)
    return max(-1, min(1, s / 3)) if news else 0

def rsi(c, n=14):
    d = c.diff(); u = d.clip(lower=0).rolling(n).mean(); l = (-d.clip(upper=0)).rolling(n).mean()
    uv, lv = u.iloc[-1], l.iloc[-1]
    if pd.isna(uv) or pd.isna(lv): return 50.0
    if lv == 0: return 100.0 if uv > 0 else 50.0
    return float(100 - 100 / (1 + uv / lv))

def intraday_stats(df):
    """Pre-market high/volym, öppning och senaste kurs för en amerikansk aktie (tider i New York)."""
    try:
        df = df.dropna(subset=["Close"])
        if df.empty: return None
        idx = df.index
        idx = idx.tz_localize("UTC") if idx.tz is None else idx
        df = df.copy(); df.index = idx.tz_convert("America/New_York")
        day = df.index[-1].date()
        td = df[df.index.date == day]
        mins = td.index.hour * 60 + td.index.minute
        pre = td[mins < 570]; reg = td[(mins >= 570) & (mins < 960)]
        return {"day": day, "last": float(td["Close"].iloc[-1]),
                "pre_high": float(pre["High"].max()) if len(pre) else None,
                "pre_vol": int(pre["Volume"].sum()) if len(pre) else 0,
                "open": float(reg["Open"].iloc[0]) if len(reg) else None}
    except Exception:
        return None

def num(x, d=1):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)): return None
        return round(float(x), d)
    except Exception: return None

def short_about(txt, n=240):
    """Kortar Yahoos företagsbeskrivning till ~2 meningar."""
    t = re.sub(r"\s+", " ", (txt or "")).strip()
    if not t: return None
    if len(t) <= n: return t
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", t):
        if len(out) + len(sent) + 1 > n: break
        out = (out + " " + sent).strip()
    return out or (t[:n - 1].rsplit(" ", 1)[0] + "…")

def build(t, d, intr, info, news, mk0, sent=None, nf=None):
    """Räknar fram en aktiepost. Ren funktion – inget nätverk."""
    d = d.dropna(subset=["Close"])
    if len(d) < 61: return None
    us = is_us(t)
    if intr:
        past = d[d.index.date < intr["day"]]
        if len(past) < 60: return None
        prev = past.iloc[-1]; px = intr["last"]; closes = past["Close"].tolist() + [px]
        opn, pre_high, pre_vol = intr["open"], intr["pre_high"], intr["pre_vol"]
        vols = past["Volume"]
    else:
        prev = d.iloc[-2]; px = float(d["Close"].iloc[-1]); closes = d["Close"].tolist()
        opn = float(d["Open"].iloc[-1]) if "Open" in d else None
        pre_high, pre_vol = None, 0
        vols = d["Volume"].iloc[:-1]
    c = pd.Series(closes, dtype="float64")
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    sma50, sma200, sma200_prev, sma50_prev = float(s50.iloc[-1]), float(s200.iloc[-1]), float(s200.iloc[-2]), float(s50.iloc[-2])
    ema12, ema26 = float(c.ewm(span=12).mean().iloc[-1]), float(c.ewm(span=26).mean().iloc[-1])
    pc = float(prev["Close"]); chg = (px / pc - 1) * 100
    def ret(n): return (px / float(c.iloc[-n - 1]) - 1) * 100 if len(c) > n else 0.0
    r1, r3, r6 = ret(21), ret(63), ret(126)
    v20 = float(vols.iloc[-20:].mean()) if len(vols) else 0
    volx = float(vols.iloc[-1]) / v20 if v20 else 1.0
    rs = rsi(c)

    # --- grundvärden ---
    pe, pb = info.get("trailingPE"), info.get("priceToBook")
    eg = (info.get("earningsGrowth") or 0) * 100; rg = (info.get("revenueGrowth") or 0) * 100
    pm = (info.get("profitMargins") or 0) * 100
    de, cr = info.get("debtToEquity"), info.get("currentRatio")
    dy = (info.get("trailingAnnualDividendYield") or 0) * 100
    ev, ebitda, roa = info.get("enterpriseValue"), info.get("ebitda"), info.get("returnOnAssets")
    grc = [bool(pe and 0 < pe < 15), bool(pb and 0 < pb < 1.5), bool(pe and pb and 0 < pe * pb < 22.5),
           bool(de is not None and de < 100), bool(cr is not None and cr >= 1.5), bool(eg > 0)]
    gr = bool(pe and pb and pe > 0 and pb > 0 and sum(grc) >= 5)
    ey = (ebitda / ev * 100) if (ev and ev > 0 and ebitda and ebitda > 0) else None
    roc = roa * 100 if roa is not None else None
    excl = info.get("sector") in ("Financial Services", "Utilities")   # Greenblatt utesluter finans/kraft

    sent = sentiment(news) if sent is None else sent
    nf = (any(n["ts"] > time.time() - 86400 for n in news) if nf is None else nf)
    hist = past if intr else d.iloc[:-1]
    pdh, pdl = float(prev["High"]), float(prev["Low"])
    try:
        h_, l_, c_ = hist["High"].astype(float), hist["Low"].astype(float), hist["Close"].astype(float)
        tr = pd.concat([h_ - l_, (h_ - c_.shift()).abs(), (l_ - c_.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.iloc[-14:].mean())
    except Exception:
        atr = px * 0.02
    last20 = c.iloc[-21:-1]
    rng = (float(last20.max()) - float(last20.min())) / float(last20.mean()) * 100
    bo = bool(px > float(last20.max()) and volx >= 1.2 and px > sma50)      # breakout över 20-dagarshögsta på volym
    stag = bool(rng < 8 and abs(r1) < 3)                                    # stagnation: smalt intervall, ingen rörelse
    tech = (min(max(r1, 0), 30) / 30 * 25 + min(max(r3, 0), 60) / 60 * 25 + (px > sma50) * 15 + (px > sma200) * 15
            + (ema12 > ema26) * 10 + min(volx, 3) / 3 * 10)
    fund = min(max(eg, 0), 50) / 50 * 40 + min(max(rg, 0), 30) / 30 * 40 + min(max(pm, 0), 25) / 25 * 20
    score = round(tech * .6 + fund * .25 + (sent + 1) / 2 * 15, 1)

    hit = bool(chg >= 5 or (us and px - pc >= 3) or pre_vol >= 50_000)
    c1 = bool(px > float(prev["High"])); c2 = bool(pc > sma200_prev) if sma200_prev == sma200_prev else None
    c3 = bool(px > pre_high) if pre_high is not None else None
    c4 = bool(px > opn) if opn is not None else None
    c5 = bool(pre_vol >= 50_000) if (us and intr) else None
    dtl = [c1, c2, c3, c4, c5]

    # --- tekniska larm ---
    al = []
    if pc < sma50_prev <= px: al.append({"k": "sma50", "t": "Bröt upp genom SMA50"})
    elif pc > sma50_prev >= px: al.append({"k": "sma50", "t": "Bröt ner genom SMA50"})
    if rs < 30: al.append({"k": "rsi", "t": f"RSI översåld ({rs:.0f})"})
    elif rs > 70: al.append({"k": "rsi", "t": f"RSI överköpt ({rs:.0f})"})
    seg = c.iloc[-126:]; hi, lo = float(seg.max()), float(seg.min()); rng = (hi - lo) or 1
    for r in (0.236, 0.382, 0.5, 0.618, 0.786):
        lvl = hi - rng * r
        if lvl and abs(px / lvl - 1) <= 0.012:
            al.append({"k": "fib", "t": f"Pris vid Fibonacci {r * 100:.1f}%".replace(".0%", "%")}); break

    dte = sum(1 for x in dtl if x is not None); dtn = sum(1 for x in dtl if x is True)
    atrp = atr / px * 100 if px else 0
    dts = round(max(0, min(max(chg, 0), 10) / 10 * 35 + min(math.log10((pre_vol or 0) + 1) / 6, 1) * 25 + min(volx, 4) / 4 * 20
                  + min(atrp, 6) / 6 * 10 + (10 if nf else 0) + (5 if sent > 0 else -5 if sent < 0 else 0)), 1)
    m = mk0
    return {"t": t, "d": t.replace(".ST", ""), "n": info.get("shortName") or info.get("longName") or t,
            "m": [m["k"]], "cur": m["cur"], "ccy": m["ccy"],
            "px": round(px, 2), "chg": round(chg, 2), "sc": score, "rsi": round(rs), "r1m": num(r1), "r3m": num(r3), "r6m": num(r6),
            "vx": num(volx), "a50": bool(px > sma50), "a200": bool(px > sma200), "macd": 1 if ema12 > ema26 else -1,
            "pe": num(pe), "pb": num(pb, 2), "eg": num(eg, 0), "rg": num(rg, 0), "pm": num(pm, 0), "dy": num(dy),
            "hi": round(float(max(closes[-252:])), 2), "lo": round(float(min(closes[-252:])), 2),
            "gr": gr, "grn": sum(grc), "grc": grc, "ey": num(ey), "roc": num(roc), "mfr": {}, "mf": False, "_ex": excl,
            "al": al, "dt": dtl, "dok": all(x is True for x in dtl), "hit": hit, "sent": sent,
            "sp": [round(x, 2) for x in closes[-45:]],
            "ab": short_about(info.get("longBusinessSummary")), "sec": info.get("sector"), "ind": info.get("industry"), "ct": info.get("country"),
            "emp": info.get("fullTimeEmployees"), "mc": info.get("marketCap"),
            "sh": bool(len(d) < 201), "nb": int(len(d)), "lt": str(d.index[-1].date()), "bo": bo, "stag": stag, "rng": num(rng), "atr": round(atr, 2), "pdh": round(pdh, 2), "pdl": round(pdl, 2), "pdc": round(pc, 2),
            "pmv": int(pre_vol or 0), "pmh": round(pre_high, 2) if pre_high else None, "nf": bool(nf), "dts": dts, "dtn": dtn, "dte": dte}

# ----------------------------------------------------------------------------------
# HORISONT, MAGIC FORMULA, RANKNING
# ----------------------------------------------------------------------------------
def horizon(s):
    r1, r3, r6 = s["r1m"] or 0, s["r3m"] or 0, s["r6m"] or 0
    eg, rg = s["eg"] or 0, s["rg"] or 0
    if s["sc"] >= 60 and r1 > 5 and s["a50"] and s["macd"] > 0 and r3 > 10:
        return "1-3", "Starkt kortsiktigt momentum: stark 1–3 mån-avkastning, över SMA50 och MACD uppåt."
    if s["a200"] and s["a50"] and r3 > 0 and r6 > 0 and s["sc"] >= 45:
        return "3-6", "Etablerad uppåttrend över SMA50 och SMA200 med positiv 3–6 mån-avkastning."
    if s["a200"] and eg > 10 and rg > 5:
        return "6-12", "Trenden håller och vinst/omsättning växer – case som byggs över 6–12 mån."
    if s["mf"] or (s["gr"] and s["a200"]):
        return "12-24", "Värdecase (Magic Formula/Graham) med trend som håller – 1–2 års sikt."
    if s["gr"]:
        return "24+", "Graham-värdecase utan trendbekräftelse – kräver tålamod, 2+ år."
    return "none", "Ingen tydlig kombination av trend, tillväxt eller värde just nu – avvakta."

def magic_rank(members, recs, mk):
    elig = [t for t in members if recs[t]["ey"] and recs[t]["roc"] is not None and recs[t]["ey"] > 0 and recs[t]["roc"] > 0 and not recs[t]["_ex"]]
    if not elig: return
    r_ey = {t: i for i, t in enumerate(sorted(elig, key=lambda t: -recs[t]["ey"]))}
    r_roc = {t: i for i, t in enumerate(sorted(elig, key=lambda t: -recs[t]["roc"]))}
    order = sorted(elig, key=lambda t: r_ey[t] + r_roc[t])
    for i, t in enumerate(order):
        recs[t]["mfr"][mk] = round(i / len(order), 3)

def finalize(recs, markets):
    keys = [m["k"] for m in markets]
    groups = {}
    for m in markets:
        if m.get("reg"): groups.setdefault(m["reg"], set()).add(m["k"])
    def member(k, r): return (k == "ALL" and any(x != "CRYPTO" for x in r["m"])) or k in r["m"] or (k in groups and bool(groups[k] & set(r["m"])))
    for k in keys + list(groups) + ["ALL"]:
        members = [t for t, r in recs.items() if member(k, r)]
        magic_rank(members, recs, k)
    for t, r in recs.items():
        r["mf"] = r["mfr"].get(r["m"][0], 1) <= 0.2
        if r["gr"]: r["al"].append({"k": "graham", "t": "Graham Screener-kandidat"})
        if r["mf"]: r["al"].append({"k": "magic", "t": "Magic Formula-kandidat"})
        r["h"], r["hr"] = horizon(r)
        if r.get("sh"): r["hr"] += f" Kort kurshistorik ({r['nb']} handelsdagar) – SMA200 saknas."
        r.pop("_ex", None)
    rank = {}
    for k in keys + list(groups) + ["ALL"]:
        members = [t for t, r in recs.items() if member(k, r)]
        if not members: continue
        by_sc = sorted(members, key=lambda t: -recs[t]["sc"])
        hits = [t for t in by_sc if recs[t]["hit"]]
        top = hits[:25] if len(hits) >= 25 else hits + [t for t in by_sc if t not in hits][:25 - len(hits)]
        mom = sorted(top, key=lambda t: -recs[t]["sc"])
        val = []
        for t in members:
            r = recs[t]; mfp = r["mfr"].get(k)
            mf_ok = mfp is not None and mfp <= 0.2
            if r["gr"] or mf_ok:
                v = 0.5 * (r["grn"] / 6 * 100) + 0.5 * ((1 - mfp) * 100 if mfp is not None else 0)
                r.setdefault("vs", {})[k] = round(v, 1)
                val.append(t)
        val = sorted(val, key=lambda t: -recs[t]["vs"][k])[:25]
        rank[k] = {"mom": mom, "val": val, "all": by_sc}
    rank["DT"] = rank_dt(recs)
    return rank

def rank_dt(recs, ids=None):
    ids = list(ids if ids is not None else recs)
    ids = [t for t in ids if not is_crypto(t)]
    cand = sorted([t for t in ids if recs[t]["dok"]], key=lambda t: -recs[t]["dts"])[:300]
    def near_ok(r):
        miss = r["dte"] - r["dtn"]
        return (not r["dok"]) and ((miss == 0 and 3 <= r["dte"] < 5) or (miss == 1 and r["dte"] == 5))
    near = sorted([t for t in ids if near_ok(recs[t])], key=lambda t: -recs[t]["dts"])[:300]
    return {"cand": cand, "near": near}

def clean(o):
    if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, float): return None if (math.isnan(o) or math.isinf(o)) else o
    if hasattr(o, "item"):
        try: return clean(o.item())
        except Exception: return None
    return o

# ----------------------------------------------------------------------------------
# DAGHANDEL: intradag, VWAP, entry/stop/mål och köp/sälj-flaggor
# ----------------------------------------------------------------------------------
def make_dtx(t, df, rec):
    """Bygger underlag för daghandelsdiagrammet. Regler (vedertagna): trigger = över PM-high och gårdagens high,
    stop under närmaste struktur (VWAP/PM-high/gårdagens high/öppning/svinglow) minus 0,25×ATR(5 min), begränsad till
    0,6×daglig ATR, +25 % bredare vid nyhetskatalysator; mål 1R och 2R."""
    try:
        tz, open_m = session(t)
        df = df.dropna(subset=["Close"])
        if df.empty: return None
        idx = df.index
        idx = idx.tz_localize("UTC") if idx.tz is None else idx
        df = df.copy(); df.index = idx.tz_convert(tz)
        day = df.index[-1].date(); td = df[df.index.date == day]
        if len(td) < 3: return None
        mins = list(td.index.hour * 60 + td.index.minute)
        o, h, l, c, v = (td[k].astype(float).tolist() for k in ("Open", "High", "Low", "Close", "Volume"))
        n = len(c)
        cum_v = pd.Series(v).cumsum(); cum_tp = (pd.Series([(h[i] + l[i] + c[i]) / 3 * v[i] for i in range(n)])).cumsum()
        vwap = [float(cum_tp[i] / cum_v[i]) if cum_v[i] > 0 else c[i] for i in range(n)]
        cs = pd.Series(c); ema9 = cs.ewm(span=9).mean().tolist(); rs5 = [None] * n
        try:
            d_ = cs.diff(); u_ = d_.clip(lower=0).rolling(14).mean(); lo_ = (-d_.clip(upper=0)).rolling(14).mean()
            rs5 = (100 - 100 / (1 + u_ / lo_.replace(0, float("nan")))).tolist()
        except Exception: pass
        pm_i = [i for i in range(n) if mins[i] < open_m]
        pm_h = max(h[i] for i in pm_i) if pm_i else None; pm_l = min(l[i] for i in pm_i) if pm_i else None
        pm_v = int(sum(v[i] for i in pm_i)) if pm_i else 0
        fr = next((i for i in range(n) if mins[i] >= open_m), None)
        open_px = o[fr] if fr is not None else None
        pdh, pdl, pdc = rec["pdh"], rec["pdl"], rec["pdc"]; atr_d = rec.get("atr") or c[-1] * 0.02
        trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
        atr5 = float(sum(trs[-14:]) / len(trs[-14:])) if trs else c[-1] * 0.003
        trig = max(x for x in (pm_h, pdh) if x)
        last = c[-1]

        def levels(i, entry):
            """Entry/stop/mål utifrån vad som var känt vid stapel i."""
            sup = {"VWAP": vwap[i], "PM-high": pm_h, "gårdagens high": pdh,
                   "dagens öppning": open_px if (fr is not None and i >= fr) else None, "senaste svinglow": min(l[max(0, i - 5):i + 1])}
            below = {k: x for k, x in sup.items() if x and x < entry * 0.998}
            name, struct = max(below.items(), key=lambda kv: kv[1]) if below else ("ATR", entry - 0.5 * atr_d)
            risk = entry - (struct - 0.25 * atr5)
            why = f"Stop under {name} ({struct:.2f}) minus 0,25×ATR(5 min)."
            mx, mn = 0.6 * atr_d, 0.15 * atr_d
            if risk > mx: risk = mx; why = f"Närmaste struktur ({name}) ligger för långt bort – stop begränsad till 0,6× daglig ATR ({atr_d:.2f})."
            if risk < mn: risk = mn; why += " Breddad till minst 0,15× daglig ATR för att undvika brus."
            if rec.get("nf"): risk *= 1.25; why += " +25 % bredare stop p.g.a. nyhetskatalysator senaste dygnet."
            risk = max(risk, 0.01)
            return {"entry": round(entry, 2), "stop": round(entry - risk, 2), "t1": round(entry + risk, 2), "t2": round(entry + 2 * risk, 2), "risk": risk, "why": why}

        pl = levels(n - 1, round(trig * 1.001, 2) if last <= trig * 1.002 else last)
        entry, stop, t1, t2, risk, why = pl["entry"], pl["stop"], pl["t1"], pl["t2"], pl["risk"], pl["why"]

        flags, tr_, cool = [], None, 0
        for i in range(max(fr or 0, 3), n):
            av = sum(v[max(0, i - 10):i]) / max(1, len(v[max(0, i - 10):i]))
            if tr_ is None:
                if cool > 0: cool -= 1; continue
                if c[i] > trig and v[i] >= 1.2 * av and c[i] > vwap[i]:
                    tr_ = levels(i, c[i]); flags.append({"i": i, "k": "BUY", "t": f"Breakout över {trig:.2f} på volym"})
                elif l[i - 1] <= vwap[i - 1] * 1.001 and c[i] > o[i] and c[i] > vwap[i] and ema9[i] > vwap[i] and c[i] > (pm_h or 0) * 0.995:
                    tr_ = levels(i, c[i]); flags.append({"i": i, "k": "BUY", "t": "Återtest av VWAP"})
            else:
                body = abs(c[i] - o[i]) or 0.001; wick = h[i] - max(c[i], o[i]); why_s = None
                if c[i] <= tr_["stop"]: why_s = "Stop loss nådd"
                elif c[i] >= tr_["t2"]: why_s = "Mål 2 nått"
                elif c[i] < vwap[i] and c[i - 1] < vwap[i - 1]: why_s = "Två stängningar under VWAP"
                elif rs5[i] is not None and rs5[i] == rs5[i] and rs5[i] > 80 and wick > 2 * body and c[i] < o[i]: why_s = "Överköpt + avvisning (lång överskugga)"
                if why_s: flags.append({"i": i, "k": "SELL", "t": why_s}); tr_ = None; cool = 3
            if len(flags) >= 8: break
        in_pos = tr_ is not None
        if in_pos: stop, t1, t2 = tr_["stop"], tr_["t1"], tr_["t2"]; entry = tr_["entry"]; why = tr_["why"]; risk = tr_["risk"]
        hhmm = lambda i: f"{mins[i] // 60:02d}:{mins[i] % 60:02d}"
        if flags and flags[-1]["i"] == n - 1 and flags[-1]["k"] == "BUY": st = {"k": "BUY", "t": f"KÖP-signal nu ({flags[-1]['t']}). Entry ≈ {last:.2f}, stop {stop:.2f}."}
        elif flags and flags[-1]["i"] == n - 1 and flags[-1]["k"] == "SELL": st = {"k": "SELL", "t": f"SÄLJ-signal nu ({flags[-1]['t']})."}
        elif in_pos: st = {"k": "HOLD", "t": f"Modellen är i position sedan {hhmm(flags[-1]['i'])}. Behåll tills stop {stop:.2f} eller mål {t1:.2f}/{t2:.2f}."}
        elif last <= trig: st = {"k": "WAIT", "t": f"Vänta – köp först vid break över {trig:.2f} (PM-high/gårdagens high) med ökad volym."}
        else: st = {"k": "WAIT", "t": f"Kursen ligger redan över {trig:.2f} – jaga inte. Vänta på återtest av VWAP eller {trig:.2f}."}
        notes = []
        gap = (last / pdc - 1) * 100
        notes.append(f"Gap/rörelse: {gap:+.1f}% mot gårdagens stängning ({pdc:.2f}).")
        notes.append(f"Pre-market: volym {pm_v:,}".replace(",", " ") + (f", high {pm_h:.2f}, low {pm_l:.2f}." if pm_i else " (ingen pre-market-data)."))
        notes.append(f"Gårdagens intervall: {pdl:.2f}–{pdh:.2f}. Dagens trigger = över {trig:.2f} (högsta av PM-high och gårdagens high).")
        notes.append(f"Kursen är {'över' if last > vwap[-1] else 'under'} VWAP ({vwap[-1]:.2f}); relativ volym i går {rec.get('vx')}× snitt.")
        notes.append(f"Daglig ATR ≈ {atr_d:.2f} ({atr_d / last * 100:.1f}% av kursen), ATR(5 min) ≈ {atr5:.2f}.")
        notes.append("Nyheter: " + ("katalysator senaste dygnet – räkna med högre volatilitet (stop breddad)." if rec.get("nf") else "ingen tydlig nyhetskatalysator senaste dygnet.")
                     + (" Nyhetsbilden är negativ – högre risk." if (rec.get("sent") or 0) < 0 else ""))
        return {"tz": tz, "open": open_m, "bars": [[int(td.index[i].timestamp()), round(o[i], 2), round(h[i], 2), round(l[i], 2), round(c[i], 2), int(v[i])] for i in range(n)],
                "vwap": [round(x, 2) for x in vwap], "pm": {"h": round(pm_h, 2) if pm_h else None, "l": round(pm_l, 2) if pm_l else None, "v": pm_v},
                "pd": {"h": pdh, "l": pdl, "c": pdc},
                "lv": {"trig": round(trig, 2), "entry": entry, "stop": stop, "t1": t1, "t2": t2, "risk": round(risk, 2), "atr": round(atr_d, 2), "atr5": round(atr5, 3)},
                "flags": flags, "st": st, "notes": notes, "stopWhy": why}
    except Exception as e:
        print("make_dtx misslyckades för", t, "–", e)
        return None

def dtx_targets(rk, recs):
    """Vilka aktier som får intradagsdiagram: topp 25 totalt + topp 8 per börs (så att börsfiltret i appen har diagram)."""
    top = list(rk["cand"][:25]); top += [t for t in rk["near"] if t not in top][:max(0, 25 - len(top))]
    by_m = {}
    for t in rk["cand"] + rk["near"]:
        for m in recs[t]["m"]: by_m.setdefault(m, []).append(t)
    for lst in by_m.values():
        top += [t for t in lst[:8] if t not in top]
    return top[:130]

LIVE_KEYS = ("px", "chg", "dt", "dok", "pmv", "pmh", "dts", "vx", "rsi", "a50", "a200", "macd", "bo", "pdh", "pdl", "pdc", "atr", "dtn", "dte", "hit")

def live():
    """Snabb uppdatering (var 15:e minut under börstid): bara kandidater/toppaktier. Skriver daytrade.json."""
    base = json.load(open("data.json", encoding="utf-8")); recs = base["stocks"]
    pool = []
    for k, r in base["rank"].items():
        pool += (r["cand"] + r["near"]) if k == "DT" else r["mom"]
    pool = [t for t in dict.fromkeys(pool) if t in recs]
    print(f"Live-uppdatering av {len(pool)} aktier")
    daily = dl(pool, period="1y", interval="1d"); intra = dl(pool, period="5d", interval="5m", prepost=True)
    mk_by = {m["k"]: m for m in base["markets"]}
    upd = {}
    for t in pool:
        if t not in daily: continue
        old = recs[t]
        try:
            stats = intraday_stats(intra[t]) if (is_us(t) and t in intra) else None
            new = build(t, daily[t], stats, {}, [], mk_by[old["m"][0]], sent=old.get("sent", 0), nf=old.get("nf", False))
            if new:
                upd[t] = {k: new[k] for k in LIVE_KEYS}
                for k in LIVE_KEYS: old[k] = new[k]
        except Exception as e:
            print("Hoppar över", t, "–", e)
    rk = rank_dt(recs, list(upd))
    dtx = {}
    for t in dtx_targets(rk, recs):
        if t in intra:
            x = make_dtx(t, intra[t], recs[t])
            if x: dtx[t] = x
    out = {"v": 2, "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "stocks": upd, "dt": rk, "dtx": dtx}
    with open("daytrade.json", "w", encoding="utf-8") as f:
        json.dump(clean(out), f, ensure_ascii=False, separators=(",", ":"))
    print(f"Klart: {len(upd)} uppdaterade, {len(rk['cand'])} kandidater, {len(dtx)} diagram")

CCY_FLAG = {"SEK": "🇸🇪", "USD": "🇺🇸", "EUR": "🇪🇺", "DKK": "🇩🇰", "NOK": "🇳🇴", "GBP": "🇬🇧", "CHF": "🇨🇭", "CAD": "🇨🇦"}

def analyze_one(sym):
    """Fullständig analys av en enskild ticker (används vid uppslag). Returnerar (post, nyheter)."""
    try:
        d = dl([sym], period="1y", interval="1d").get(sym)
        if d is None or len(d) < 201: return None, []
        info = get_info(sym)
        news = merge_news(get_news(sym), benzinga_news(sym))
        if not news:
            try: news = google_news(re.sub(r"\b(Inc|Corp|Corporation|Ltd|plc|AB|ASA|SE|NV|AG|SA|Holdings?|Company)\b\.?", "", info.get("shortName") or sym).strip(" ,.") + " stock")
            except Exception: news = []
        cc = norm_ccy(info.get("currency")) or "USD"
        intr = None
        if is_us(sym):
            i5 = dl([sym], period="5d", interval="5m", prepost=True).get(sym)
            intr = intraday_stats(i5) if i5 is not None else None
        rec = build(sym, d, intr, info, news, {"k": "WATCH", "cur": CUR_SYM.get(cc, cc), "ccy": cc})
        if not rec: return None, news
        rec["m"] = ["WATCH"]
        if rec["gr"]: rec["al"].append({"k": "graham", "t": "Graham Screener-kandidat"})
        rec["h"], rec["hr"] = horizon(rec); rec.pop("_ex", None); rec["prov"] = True
        return rec, news
    except Exception as e:
        print("Analys misslyckades för", sym, "–", e)
        return None, []

def lookup(q):
    """Slår upp en aktie (namn eller ticker) hos Yahoo och sparar lookup.json."""
    q = q.strip(); syms = []
    qs = [q]
    mm = re.fullmatch(r"([A-Za-z0-9.]{1,10})-USDT?", q, re.I)
    if mm: qs.append(mm.group(1))                       # "NBIS-USD" -> sök även aktien NBIS
    for qq in qs:
        try:
            for r in (yf.Search(qq, max_results=8, news_count=0).quotes or []):
                if r.get("quoteType") in ("EQUITY", "ETF") and r.get("symbol"): syms.append(r)
        except Exception as e:
            print("Yahoo-sökning misslyckades:", e)
    if not syms:
        for qq in qs:
            if re.fullmatch(r"[A-Za-z0-9.\-]{1,15}", qq): syms.append({"symbol": qq.upper()})
    syms = list({x["symbol"]: x for x in syms}.values())
    items = []
    for r in syms[:6]:
        sym = r["symbol"]; info = get_info(sym)
        if not info: continue
        items.append({"t": sym, "n": r.get("longname") or r.get("shortname") or info.get("longName") or info.get("shortName") or sym,
                      "exch": r.get("exchDisp") or info.get("fullExchangeName") or info.get("exchange"), "type": r.get("quoteType") or info.get("quoteType"),
                      "ccy": info.get("currency"), "px": info.get("currentPrice") or info.get("regularMarketPrice"),
                      "chg": info.get("regularMarketChangePercent"), "pe": info.get("trailingPE"), "mcap": info.get("marketCap"),
                      "sector": info.get("sector"), "flag": CCY_FLAG.get(info.get("currency"), "🌐")})
        if len(items) <= 3:
            rec, nw = analyze_one(sym)
            items[-1]["rec"] = rec; items[-1]["news"] = nw
    with open("lookup.json", "w", encoding="utf-8") as f:
        json.dump(clean({"q": q, "ts": int(time.time()), "items": items}), f, ensure_ascii=False)
    print(f"Slog upp '{q}': {len(items)} träffar")

def read_extra_markets():
    try:
        return json.load(open("markets_extra.json", encoding="utf-8")).get("markets", [])
    except Exception:
        return []

# ----------------------------------------------------------------------------------
# KURSHISTORIK FÖR GRAFEN (en liten fil per aktie, publiceras på grenen "hist")
# ----------------------------------------------------------------------------------
def hist_name(t): return re.sub(r"[^A-Za-z0-9._=-]", "_", t)

def _series(idx, vals):
    return {"t": [int(i.timestamp()) for i in idx], "p": [round(float(v), 2) for v in vals]}

def write_hist(recs, daily, intra_a, intra_b):
    """1D/1V från intradata, 1M–2Å från daglig stängning (2 år), 5Å veckovis."""
    os.makedirs("hist/h", exist_ok=True); n = 0
    for t in recs:
        d = daily.get(t)
        if d is None or "Close" not in d: continue
        try:
            c = d["Close"].dropna()
            h = {"u": int(time.time()), "2y": _series(c.index[-504:], c.iloc[-504:])}
            wk = c.resample("W").last().dropna()
            h["5y"] = _series(wk.index[-262:], wk.iloc[-262:])
            idf = intra_a.get(t) if t in intra_a else intra_b.get(t)
            if idf is not None and len(idf):
                cl = idf["Close"].dropna()
                ix = cl.index
                ix = ix.tz_localize("UTC") if ix.tz is None else ix
                tz, _ = session(t)
                cl = cl.copy(); cl.index = ix.tz_convert(tz)
                if is_us(t):
                    mins = cl.index.hour * 60 + cl.index.minute
                    cl = cl[(mins >= 570) & (mins < 960)]
                if len(cl):
                    days = sorted(set(cl.index.date)); keep = set(days[-5:])
                    last = cl[[x.date() == days[-1] for x in cl.index]]
                    h["1d"] = _series(last.index, last)
                    w5 = cl[[x.date() in keep for x in cl.index]].resample("30min").last().dropna()
                    h["1w"] = _series(w5.index, w5)
            with open(f"hist/h/{hist_name(t)}.json", "w", encoding="utf-8") as f:
                json.dump(h, f, separators=(",", ":"))
            n += 1
        except Exception as e:
            print("hist misslyckades för", t, "–", e)
    print(f"  kurshistorik skriven för {n} aktier")

# ----------------------------------------------------------------------------------
def main():
    t0 = time.time()
    uni = {}                                    # ticker -> marknad(er)
    for m in MARKETS:
        for t in get_tickers(m): uni.setdefault(t, []).append(m["k"])
    markets_used = list(MARKETS)
    for e in read_extra_markets():                       # börser/aktier tillagda via appen (markets_extra.json)
        k = e.get("k"); base = PRESETS.get(k, {})
        tickers = e.get("tickers") or base.get("tickers") or []
        if callable(tickers): tickers = tickers()
        if not k or not tickers: continue
        if k not in {m["k"] for m in markets_used}:
            markets_used.append({"k": k, "n": e.get("n") or base.get("n", k), "flag": e.get("flag") or base.get("flag", "🌐"),
                                 "cur": e["cur"] if e.get("cur") is not None else base.get("cur", ""), "ccy": e.get("ccy") or base.get("ccy", "USD"),
                                 "reg": e.get("reg") or base.get("reg"), "mixed": bool(e.get("mixed")), "light": bool(base.get("light")), "source": []})
        for t in tickers:
            if k not in uni.setdefault(t, []): uni[t].append(k)
    extra = [t for t in read_extra() if t not in uni]
    if extra:
        markets_used.append({"k": "EXTRA", "n": "Egna tickers", "flag": "⭐", "cur": "$", "ccy": "USD", "hidden": True, "mixed": True, "source": extra})
        for t in extra: uni[t] = ["EXTRA"]
    tickers = list(uni)
    print(f"Universum: {len(tickers)} aktier i {len(markets_used)} marknader")

    mk_by = {m["k"]: m for m in markets_used}
    light_keys = {m["k"] for m in markets_used if m.get("light")}
    light_only = {t for t in tickers if uni[t] and all(k in light_keys for k in uni[t])}
    core = [t for t in tickers if t not in light_only]
    print("Hämtar kurshistorik (5 år)…");   daily = dl(core, period="5y", interval="1d")
    selected, light_stats = set(), {}
    if light_only:
        print(f"Lätt läge: skannar kurser för {len(light_only)} aktier på hela börser…")
        ld = dl(sorted(light_only), period="1y", interval="1d")
        selected, light_stats = select_light(light_only, ld, uni, mk_by)
        print(f"  {len(selected)} aktier valda för full analys {light_stats}")
        daily.update(dl(sorted(selected), period="5y", interval="1d"))
    deep = [t for t in tickers if t in selected or t not in light_only]
    us = [t for t in deep if is_us(t) and t in daily]
    print("Hämtar pre-market/intradag…"); intra = dl(us, period="5d", interval="5m", prepost=True)
    print("Hämtar nyckeltal och nyheter…")
    info, news = {}, {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, (t, inf, nw) in enumerate(ex.map(enrich, [t for t in deep if t in daily])):
            info[t], news[t] = inf, nw
            if (i + 1) % 100 == 0: print(f"  {i + 1} klara", flush=True)

    miss = [t for t in info if not info[t]]
    if miss:
        print(f"  hämtar om bolagsinfo för {len(miss)} aktier (långsamt)…", flush=True)
        for t in miss[:400]:
            time.sleep(0.4); inf = get_info(t)
            if inf: info[t] = inf
    recs, dropped = {}, []
    for t in deep:
        if t not in daily:
            dropped.append([t, "ingen kursdata hos Yahoo", uni[t]]); continue
        try:
            m0 = mk_by[uni[t][0]]
            if m0.get("mixed"):
                cc = norm_ccy(info.get(t, {}).get("currency")) or m0["ccy"]
                m0 = dict(m0, ccy=cc, cur=CUR_SYM.get(cc, cc))
            r = build(t, daily[t], intr_of(intra, t), info.get(t, {}), news.get(t, []), m0)
            if r:
                r["m"] = uni[t]; recs[t] = r
            else:
                dropped.append([t, "för kort kurshistorik (under 61 handelsdagar)", uni[t]])
        except Exception as e:
            dropped.append([t, "analysfel: " + str(e)[:60], uni[t]])
            print("Hoppar över", t, "–", e)
    print(f"Analys: {len(recs)} aktier klara, {len(dropped)} saknas (Yahoo saknar data eller för kort historik).")
    if len(recs) < max(10, 0.3 * len(deep)):
        print(f"FEL: bara {len(recs)} av {len(deep)} aktier kunde analyseras. Behåller gamla data.json.")
        sys.exit(1)

    rank = finalize(recs, markets_used)
    n_yahoo = sum(1 for t in recs if news.get(t))
    bz_try, bz_ok = fill_benzinga(recs, news, rank)
    fb_try, fb_ok = fill_news(recs, news, rank)
    feed = fetch_feeds()
    print(f"Nyheter: Yahoo gav nyheter för {n_yahoo}/{len(recs)} aktier (fel: {NEWS_DIAG['err']}, senast: {NEWS_DIAG['last'] or '–'}); "
          f"Benzinga: {bz_ok}/{bz_try}; reserv Google News: {fb_ok}/{fb_try}; marknadsflöde: {len(feed)} artiklar {dict(FEED_STATS)}")
    dtx = {}
    dt_ids = dtx_targets(rank["DT"], recs)
    need = [t for t in dt_ids if t not in intra]
    if need: intra.update(dl(need, period="5d", interval="5m", prepost=False))
    for t in dt_ids:
        if t in intra:
            x = make_dtx(t, intra[t], recs[t])
            if x: dtx[t] = x
    out = {"v": 2, "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "fx": fx_rates({m["ccy"] for m in markets_used} | {r["ccy"] for r in recs.values()}),
           "markets": [{"k": "ALL", "n": "Alla marknader", "flag": "🌍", "cur": "", "ccy": ""}] +
                      [{"k": g, "n": GROUP_NAMES.get(g, (g, "🌐"))[0], "flag": GROUP_NAMES.get(g, (g, "🌐"))[1], "cur": "", "ccy": "", "group": True}
                       for g in sorted({m["reg"] for m in markets_used if m.get("reg")})] +
                      [{k: m[k] for k in ("k", "n", "flag", "cur", "ccy", "reg") if k in m} | ({"hidden": True} if m.get("hidden") else {}) | ({"light": True} if m.get("light") else {}) for m in markets_used],
           "fail": [d_[0] for d_ in dropped][:400],
           "stocks": recs, "rank": rank, "dtx": dtx,
           "news": {t: n for t, n in news.items() if n and t in recs},
           "feed": feed,
           "stats": {"universe": len(tickers), "ok": len(recs), "list_fallback": dict(LIST_WARN), "lists": LISTS_META,
                     "list_changes": {k: v["changes"][0] for k, v in _CACHE.items() if v.get("changes") and v["changes"][0]["ts"] >= (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%d")}, "dropped": dropped[:600], "light": light_stats, "news_yahoo": n_yahoo, "news_benzinga": bz_ok, "news_fallback": fb_ok, "info_ok": sum(1 for t in recs if info.get(t)),
                     "news_tickers": sum(1 for t in recs if news.get(t)), "feed_items": len(feed), "feed_src": dict(FEED_STATS),
                     "news_err": NEWS_DIAG["err"], "news_last_err": NEWS_DIAG["last"]}}
    save_cache()
    print("Bygger kurshistorik för grafen…")
    try:
        intra_o = dl([t for t in recs if t not in intra], period="5d", interval="15m")
        write_hist(recs, daily, intra, intra_o)
        out["hist"] = True
    except Exception as e:
        print("Varning: kurshistoriken kunde inte byggas:", e)
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(clean(out), f, ensure_ascii=False, separators=(",", ":"))
    print(f"Klart: {len(recs)} aktier, {sum(len(v['mom']) for v in rank.values() if 'mom' in v)} i momentumlistor – {time.time() - t0:.0f} s")

def select_light(light_only, ld, uni, mk_by):
    """Lätt läge för hela börser: kurser för alla, full analys (info, nyheter, fundamenta) för de LIGHT_TOP starkaste likvida."""
    scored = {}
    for t in light_only:
        d = ld.get(t)
        if d is None or len(d) < 61: continue
        try:
            px = float(d["Close"].iloc[-1]); dv = float((d["Close"] * d["Volume"]).iloc[-20:].mean())
            if px < 1 or dv < LIGHT_MIN_DV: continue
            r = build(t, d, None, {}, [], mk_by[uni[t][0]], sent=0, nf=False)
            if r: scored[t] = r["sc"]
        except Exception:
            pass
    sel, stats = set(), {}
    for k in {k for t in light_only for k in uni[t]}:
        members = [t for t in light_only if k in uni[t]]
        top = sorted((t for t in members if t in scored), key=lambda t: -scored[t])[:LIGHT_TOP]
        sel.update(top)
        stats[k] = {"universe": len(members), "scanned": sum(1 for t in members if t in ld), "liquid": sum(1 for t in members if t in scored), "deep": len(top)}
    return sel, stats

def intr_of(intra, t):
    return intraday_stats(intra[t]) if t in intra else None

if __name__ == "__main__":
    if "--daytrade" in sys.argv: live()
    elif "--lookup" in sys.argv: lookup(sys.argv[sys.argv.index("--lookup") + 1])
    else: main()
