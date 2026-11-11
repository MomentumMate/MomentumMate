#!/usr/bin/env python3
"""
radar.py – institutionell radar (SPEC § 12: information som visas men inte styr; arbetslista 8, 2026-11-11).

Samlar vad utvalda institutioner gör och skriver radar.json som appen visar i fliken Radar. Fyra källor:

  13F     SEC Edgar, kvartalsvisa innehavsrapporter för förvaltarna i radar_watch.json (standardlista nedan).
          Senaste rapporten jämförs med den föregående: nya innehav, ökningar/minskningar > 20 %, avslutade. CUSIP → ticker via
          OpenFIGI, sparat i cusip_map.json så att varje CUSIP bara slås upp en gång. Rapporterna släpar upp till 45 dagar.
  13D/13G SEC Edgar, flaggningar ≥ 5 % i bolag i vårt universum (USA), senaste 30 dagarna, via varje bolags filingslista.
  ARK     ARK Invests dagliga innehavsfiler; dagens fil jämförs med gårdagens (sparad i radar.json) → köp/sälj per fond.
  INSYN   Finansinspektionens insynsregister (Norden/Sverige), transaktioner senaste 14 dagarna; ISIN → ticker via EODHD:s
          symbollista (lists_cache.json "_isin"), annars namnmatchning.

Allt är information. Ingen rad i förslaget ändras av radarn. Misslyckas en källa skrivs felet i radar.json och de andra körs ändå.
"""
import json, os, re, sys, time, io, csv, datetime as dt, unicodedata
import requests

OUT = "radar.json"
CUSIP_CACHE = "cusip_map.json"
# SEC kräver en User-Agent på formen "Namn Namn e-post" (prövat 2026-11-11: ett enda ord före adressen ger 403 "Undeclared Automated
# Tool"). Ägaren kan ange en egen kontaktadress som Actions-hemligheten RADAR_CONTACT – annars används repots noreply-adress.
# SEC avvisar GitHubs noreply-adress (prövat 2026-11-11). Tills ägaren lagt in RADAR_CONTACT används en platshållare – lägg in en riktig adress.
CONTACT = os.environ.get("RADAR_CONTACT") or "momentummate@example.com"
UA = {"User-Agent": f"Momentum Mate Radar {CONTACT}", "Accept-Encoding": "gzip, deflate", "Accept": "application/json, text/html, */*"}
def sec_headers(url): return dict(UA)
TODAY = dt.date.today()

# Standardlista. Namnet används för att verifiera att CIK:n hos SEC verkligen är rätt bolag (SEC:s namn måste innehålla nyckelordet).
DEFAULT_MANAGERS = [
    {"n": "Berkshire Hathaway", "cik": 1067983, "key": "BERKSHIRE"},
    {"n": "Baillie Gifford", "cik": 1088875, "key": "BAILLIE"},
    {"n": "ARK Investment Management", "cik": 1697748, "key": "ARK"},
    {"n": "Renaissance Technologies", "cik": 1037389, "key": "RENAISSANCE"},
    {"n": "Tiger Global", "cik": 1167483, "key": "TIGER"},
    {"n": "Coatue Management", "cik": 1135730, "key": "COATUE"},
    {"n": "Lone Pine Capital", "key": "LONE PINE"},
    {"n": "Baupost Group", "cik": 1061768, "key": "BAUPOST"},
    {"n": "Viking Global", "cik": 1103804, "key": "VIKING"},
    {"n": "Pershing Square", "cik": 1336528, "key": "PERSHING"},
    {"n": "Third Point", "cik": 1040273, "key": "THIRD POINT"},
    {"n": "Scion Asset Management", "cik": 1649339, "key": "SCION"},
    {"n": "Duquesne Family Office", "cik": 1536411, "key": "DUQUESNE"},
    {"n": "Appaloosa", "cik": 1656456, "key": "APPALOOSA"},
    {"n": "Altimeter Capital", "cik": 1541617, "key": "ALTIMETER"},
    {"n": "Whale Rock Capital", "key": "WHALE ROCK"},
    {"n": "D1 Capital Partners", "cik": 1747057, "key": "D1 CAPITAL"},
    {"n": "Bridgewater Associates", "cik": 1350694, "key": "BRIDGEWATER"},
    {"n": "Soros Fund Management", "cik": 1029160, "key": "SOROS"},
]
ARK_FUNDS = {"ARKK": "ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv", "ARKW": "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv",
             "ARKG": "ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv", "ARKQ": "ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
             "ARKF": "ARK_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv", "ARKX": "ARK_SPACE_EXPLORATION_&_INNOVATION_ETF_ARKX_HOLDINGS.csv"}
ARK_HOSTS = ["https://assets.ark-funds.com/fund-documents/funds-etf-csv/", "https://ark-funds.com/wp-content/uploads/funds-etf-csv/"]

def log(*a): print(*a, flush=True)
def jload(p, default):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return default
def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(AB|PUBL|PLC|INC|CORP|CORPORATION|CO|LTD|LIMITED|SA|ASA|OYJ|AS|A S|NV|SE|AG|HOLDING|HOLDINGS|GROUP|CLASS|CL|COM|ORD|SHS|SER|SERIES|NEW|THE|OF)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()

_last = {"t": 0.0}
def sec_get(url, tries=3):
    """SEC tillåter 10 anrop/s. Vi håller ~8."""
    for i in range(tries):
        wait = 0.125 - (time.time() - _last["t"])
        if wait > 0: time.sleep(wait)
        _last["t"] = time.time()
        try:
            r = requests.get(url, headers=sec_headers(url), timeout=30)
            if r.status_code == 429 or r.status_code >= 500: time.sleep(2 * (i + 1)); continue
            if r.status_code == 404: return None
            if r.status_code == 403:
                if not _last.get("dbg"): _last["dbg"] = 1; log("  SEC 403, svar: " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:300])
                time.sleep(3); continue
            r.raise_for_status(); return r
        except requests.RequestException:
            if i == tries - 1: raise
            time.sleep(2 * (i + 1))
    return None

# ---------------------------------------------------------------- universum
def load_universe():
    d = jload("data.json", {})
    S = d.get("stocks") or {}
    by_name = {}
    for t, x in S.items():
        if isinstance(x, dict) and x.get("n"): by_name.setdefault(norm_name(x["n"]), t)
    isin = (jload("lists_cache.json", {}).get("_isin") or {}).get("m") or {}
    return S, by_name, isin

def own_tickers():
    own = set()
    try:
        for line in open("holdings.txt", encoding="utf-8"):
            t = line.split("#")[0].strip()
            if t: own.add(t)
    except Exception: pass
    return own

# ---------------------------------------------------------------- 13F
def cusip_to_ticker(cusips, cache):
    """OpenFIGI utan nyckel: 10 CUSIP per anrop, 25 anrop per minut. Allt sparas i cusip_map.json (även misslyckade = '')."""
    todo = [c for c in dict.fromkeys(cusips) if c and c not in cache]
    if not todo: return 0
    log(f"  OpenFIGI: {len(todo)} nya CUSIP att slå upp (högst 240 per körning)")
    todo = todo[:240]; n = 0
    for i in range(0, len(todo), 10):
        chunk = todo[i:i + 10]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in chunk],
                              headers={"Content-Type": "application/json"}, timeout=30)
            if r.status_code == 429: time.sleep(65); continue
            r.raise_for_status()
            for c, res in zip(chunk, r.json()):
                data = (res or {}).get("data") or []
                tick = ""
                for d_ in data:
                    if d_.get("ticker") and (d_.get("securityType") in ("Common Stock", "ADR", "REIT", "Ltd Part", "MLP") or not tick):
                        tick = d_["ticker"].replace("/", "-")
                        if d_.get("securityType") in ("Common Stock", "ADR", "REIT"): break
                cache[c] = tick; n += 1
        except Exception as e:
            log(f"  OpenFIGI fel: {str(e)[:60]}"); time.sleep(5)
        time.sleep(2.6)      # 25 anrop/min
    return n

def parse_infotable(xml):
    rows = {}
    for m in re.finditer(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", xml, re.S):
        b = m.group(1)
        g = lambda tag: (re.search(rf"<(?:\w+:)?{tag}>\s*([^<]*?)\s*<", b) or [None, ""])[1]
        if (g("putCall") or "").strip(): continue
        cusip = g("cusip").strip().upper()
        if not cusip: continue
        try: val = float(g("value") or 0); sh = float(g("sshPrnamt") or 0)
        except ValueError: continue
        r = rows.setdefault(cusip, {"n": g("nameOfIssuer").strip(), "v": 0.0, "s": 0.0})
        r["v"] += val; r["s"] += sh
    return rows

def resolve_cik(name, key):
    """Edgars fulltextsök (efts) efter 13F-HR-lämnare med namnet; reserv: företagssök (atom)."""
    try:
        r = sec_get(f"https://efts.sec.gov/LATEST/search-index?q={requests.utils.quote(chr(34) + name + chr(34))}&forms=13F-HR")
        if r:
            for h in r.json().get("hits", {}).get("hits", []):
                for dn in (h.get("_source", {}).get("display_names") or []):
                    m = re.search(r"\(CIK (\d+)\)", dn)
                    if m and (not key or key.upper() in dn.upper()): return int(m.group(1))
    except Exception as e: log(f"  efts {name}: {str(e)[:60]}")
    q = requests.utils.quote(name)
    r = sec_get(f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={q}&type=13F-HR&output=atom")
    if not r: return None
    for m in re.finditer(r"<entry>(.*?)</entry>", r.text, re.S):
        b = m.group(1); nm = (re.search(r"<title>(.*?)</title>", b) or [None, ""])[1]; ck = re.search(r"CIK=(\d+)", b) or re.search(r"<id>[^<]*?(\d{10})", b)
        if ck and (not key or key.upper() in nm.upper()): return int(ck.group(1))
    return None

def manager_13f(m, cache):
    cik = m.get("cik"); secname = ""
    if cik:
        r = sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        if r:
            secname = r.json().get("name", "")
            if m.get("key") and m["key"].upper() not in secname.upper(): log(f"  13F {m['n']}: CIK {cik} är '{secname}' – söker rätt CIK"); cik = None; r = None
    if not cik:
        cik = resolve_cik(m["n"], m.get("key"))
        if not cik: return {"n": m["n"], "cik": None, "err": "hittar ingen 13F-lämnare med det namnet hos SEC"}
        r = sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        if not r: return {"n": m["n"], "cik": cik, "err": "finns inte hos SEC"}
    cik = int(cik); j = r.json(); secname = j.get("name", "") or secname
    rec = j.get("filings", {}).get("recent", {})
    filings = [(rec["accessionNumber"][i], rec["filingDate"][i], rec.get("reportDate", [""] * 999)[i], rec["primaryDocument"][i])
               for i in range(len(rec.get("form", []))) if rec["form"][i] == "13F-HR"]
    if not filings: return {"n": secname or m["n"], "cik": cik, "err": "inga 13F-HR"}
    out = {"n": secname or m["n"], "cik": cik, "filed": filings[0][1], "period": filings[0][2]}
    tables = []
    for acc, fdate, rdate, prim in filings[:2]:
        nod = acc.replace("-", "")
        # hela inlämningen som text ({acc}.txt) innehåller innehavstabellens xml oavsett filnamn
        x = sec_get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{acc}.txt")
        tb = parse_infotable(x.text) if x else None
        if x and not tb: log(f"  13F {secname}: inga infoTable-rader i {acc}.txt ({len(x.text)} tecken)")
        tables.append(tb)
    cur, prev = tables[0], (tables[1] if len(tables) > 1 else None)
    if not cur: out["err"] = "kunde inte läsa innehavstabellen"; return out
    cusip_to_ticker(list(cur.keys()) + (list(prev.keys()) if prev else []), cache)
    tot = sum(r["v"] for r in cur.values()) or 1.0
    if tot < 20e6 and len(cur) >= 5: tot *= 1000; out["unit"] = "tusental"      # Baupost, Duquesne m.fl. rapporterar fortfarande i tusental
    out["n_pos"] = len(cur); out["value_musd"] = round(tot / 1e6)       # value anges i hela USD sedan 2023
    unit = 1000 if out.get("unit") == "tusental" else 1
    ev, hold = [], []
    for c, r in cur.items():
        t = cache.get(c) or ""
        w = r["v"] * unit / tot * 100
        p = prev.get(c) if prev else None
        if prev is None: kind = "innehav"
        elif p is None: kind = "ny"
        elif p["s"] > 0 and r["s"] > p["s"] * 1.2: kind = "ökat"
        elif p["s"] > 0 and r["s"] < p["s"] * 0.8: kind = "minskat"
        else: kind = "oförändrat"
        row = {"t": t, "n": r["n"], "kind": kind, "w": round(w, 2), "v": round(r["v"] * unit / 1e6, 1), "sh": r["s"], "psh": (p["s"] if p else None)}
        hold.append(row)
        if kind in ("ny", "ökat", "minskat"): ev.append(row)
    if prev:
        for c, p in prev.items():
            if c not in cur: ev.append({"t": cache.get(c) or "", "n": p["n"], "kind": "ut", "w": 0, "v": 0, "sh": 0, "psh": p["s"]})
    order = {"ny": 0, "ökat": 1, "ut": 2, "minskat": 3}
    ev.sort(key=lambda e: (order.get(e["kind"], 9), -e["w"]))
    hold.sort(key=lambda e: -e["w"])
    out["events"] = ev[:120]; out["holdings"] = hold[:60]; out["top3"] = hold[:3]
    return out

# ---------------------------------------------------------------- 13D / 13G
def filings_13dg(S, days=60):
    r = sec_get("https://www.sec.gov/files/company_tickers.json")
    if not r: return [], "company_tickers.json saknas"
    t2cik = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in r.json().values()}
    cik2t = {}
    for t, c in t2cik.items():
        if t in S: cik2t.setdefault(c, t)
    us = [t for t in S if "." not in t and not t.endswith("-USD")]
    since = (TODAY - dt.timedelta(days=days)).isoformat()
    out, miss, seen, oldest, forms = [], 0, 0, "9999", {}
    for t in us:
        cik = t2cik.get(t.upper())
        if not cik: miss += 1; continue
        try:
            rr = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            if not rr: continue
            rec = rr.json().get("filings", {}).get("recent", {})
            for i in range(len(rec.get("form", []))):
                form, fd = rec["form"][i], rec["filingDate"][i]
                if fd < since: break
                seen += 1; oldest = min(oldest, fd); forms[form] = forms.get(form, 0) + 1
                # Edgar heter numera "SCHEDULE 13D"/"SCHEDULE 13G" (tidigare "SC 13D"). Ändringar (/A) hoppas över – mest indexfonders rutinuppdateringar.
                if re.match(r"(SC|SCHEDULE) 13[DG]$", form):
                    out.append({"t": t, "form": form, "date": fd, "acc": rec["accessionNumber"][i], "cik": cik})
        except Exception as e:
            log(f"  13D/13G {t}: {str(e)[:50]}")
    # anmälarens namn ur filingens indexsida; passiva jättar (index- och depåförvaltare, market makers) filtreras bort – de anmäler rutinmässigt
    PASSIVE = re.compile(r"VANGUARD|BLACKROCK|STATE STREET|FMR LLC|FIDELITY|CAPITAL RESEARCH|GEODE|NORTHERN TRUST|INVESCO|DIMENSIONAL|T\.? ?ROWE|WELLINGTON|JPMORGAN|JP MORGAN|BANK OF AMERICA|MORGAN STANLEY|GOLDMAN SACHS|SCHWAB|NUVEEN|AMERICAN CENTURY|BNY|MELLON|UBS|CITADEL|MILLENNIUM|SUSQUEHANNA|JANE STREET|TWO SIGMA|D\. ?E\. ?SHAW|AQR|ALLIANCEBERNSTEIN|FRANKLIN|LEGAL & GENERAL|PRUDENTIAL|MASSACHUSETTS FINANCIAL|MFS |BARCLAYS|DEUTSCHE|CREDIT SUISSE|RENAISSANCE", re.I)
    out.sort(key=lambda x: x["date"], reverse=True)
    for ev in out[:900]:
        try:
            acc = ev["acc"]; nod = acc.replace("-", "")
            r = sec_get(f"https://www.sec.gov/Archives/edgar/data/{ev['cik']}/{nod}/{acc}-index.htm")
            if r:
                # Indexsidan anger roller: "X (Subject)" och "Y (Filed by)". Ligger filingen under bolaget för att bolaget själv är
                # anmälare (köpt ≥ 5 % i ett annat bolag) flyttas händelsen till det andra bolaget – om det finns i universumet.
                import html as _html
                roles = re.findall(r'companyName">\s*([^<]+?)\s*\((Subject|Filed by|Filer)\)(.*?)(?=companyName"|$)', r.text, re.S)
                subj = next((x for x in roles if x[1] == "Subject"), None); filer = next((x for x in roles if x[1] in ("Filed by", "Filer")), None)
                if filer: ev["who"] = _html.unescape(re.sub(r"\s+", " ", filer[0]).strip())
                if subj:
                    mc = re.search(r"CIK=(\d+)", subj[2])
                    scik = int(mc.group(1)) if mc else None
                    if scik and scik != ev["cik"]:
                        t2 = cik2t.get(scik)
                        if t2: ev["t"] = t2
                        else: ev["drop"] = True
        except Exception: pass
    out = [ev for ev in out if not ev.get("drop")]
    n_all = len(out)
    out = [ev for ev in out if not (ev.get("who") and PASSIVE.search(ev["who"]))]
    for ev in out: ev.pop("acc", None); ev.pop("cik", None)
    log(f"  13D/13G: {n_all} flaggningar, {len(out)} kvar när passiva förvaltare filtrerats bort, senaste {days} dagarna, {miss} tickers utan CIK, {seen} filings lästa (äldsta {oldest}); vanligaste formtyper: {sorted(forms.items(), key=lambda x: -x[1])[:12]}")
    return out, None

# ---------------------------------------------------------------- ARK
def ark_holdings():
    hold = {}; err = []
    for fund, fn in ARK_FUNDS.items():
        got = None
        for host in ARK_HOSTS:
            try:
                r = requests.get(host + fn, headers={"User-Agent": UA["User-Agent"]}, timeout=30)
                if r.ok and "ticker" in r.text[:400].lower(): got = r.text; break
            except Exception: pass
        if not got: err.append(fund); continue
        rows = list(csv.DictReader(io.StringIO(got)))
        asof = None; h = {}
        for row in rows:
            k = {kk.strip().lower(): (v or "").strip() for kk, v in row.items() if kk}
            t = k.get("ticker", "").upper().replace(" ", "")
            if not t or not k.get("company"): continue
            try: sh = float(k.get("shares", "0").replace(",", "")); w = float(k.get("weight (%)", k.get("weight(%)", "0")).replace("%", "") or 0)
            except ValueError: continue
            asof = asof or k.get("date")
            h[t] = {"n": k["company"], "sh": sh, "w": w}
        hold[fund] = {"asof": asof, "h": h}
    return hold, err

def ark_trades(new, prev):
    tr = []
    for fund, cur in new.items():
        p = (prev.get(fund) or {}).get("h") or {}
        if not p: continue
        for t, x in cur["h"].items():
            ps = p.get(t, {}).get("sh")
            if ps is None: tr.append({"fund": fund, "t": t, "n": x["n"], "kind": "köp", "d": x["sh"], "sh": x["sh"], "w": x["w"], "date": cur["asof"]})
            elif x["sh"] > ps * 1.005: tr.append({"fund": fund, "t": t, "n": x["n"], "kind": "köp", "d": x["sh"] - ps, "sh": x["sh"], "w": x["w"], "date": cur["asof"]})
            elif x["sh"] < ps * 0.995: tr.append({"fund": fund, "t": t, "n": x["n"], "kind": "sälj", "d": x["sh"] - ps, "sh": x["sh"], "w": x["w"], "date": cur["asof"]})
        for t, x in p.items():
            if t not in cur["h"]: tr.append({"fund": fund, "t": t, "n": x["n"], "kind": "sälj", "d": -x["sh"], "sh": 0, "w": 0, "date": cur["asof"]})
    tr.sort(key=lambda e: -abs(e["d"] * (1 if e["sh"] else 1)))
    return tr

# ---------------------------------------------------------------- FI insyn
def fi_insyn(S, by_name, isin_map, days=14):
    frm = (TODAY - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    url = ("https://marknadssok.fi.se/publiceringsklient/sv-SE/Search/Search?SearchFunctionType=Insyn&Utgivare=&PersonILedandeSt%C3%A4llningNamn="
           f"&Transaktionsdatum.From={frm}&Transaktionsdatum.To=&Publiceringsdatum.From=&Publiceringsdatum.To=&button=export")
    r = requests.get(url, headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    raw = r.content
    text = None
    for enc in ("utf-16", "utf-8-sig", "cp1252"):
        try: text = raw.decode(enc); break
        except Exception: continue
    if not text or ";" not in text[:2000]: raise RuntimeError(f"oväntat format ({r.headers.get('Content-Type')}, {len(raw)} byte)")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    if rows: log("  FI insyn kolumner: " + " | ".join(list(rows[0].keys()))); log("  FI insyn exempel: " + json.dumps(rows[0], ensure_ascii=False)[:900])
    out, unmapped = [], 0
    for row in rows:
        k = {(kk or "").strip().lower(): (v or "").strip() for kk, v in row.items()}
        isin = k.get("isin", ""); issuer = k.get("emittent") or k.get("utgivare", "")
        t = isin_map.get(isin) or by_name.get(norm_name(issuer))
        if not t: unmapped += 1; continue
        kind = k.get("karaktär", k.get("karaktar", ""))
        if not re.search(r"förvärv|avyttring", kind, re.I): continue
        try: vol = float(k.get("volym", "0").replace(" ", "").replace(",", ".")); px = float(k.get("pris", "0").replace(" ", "").replace(",", "."))
        except ValueError: vol, px = 0, 0
        out.append({"t": t, "date": k.get("transaktionsdatum", "")[:10], "pub": k.get("publiceringsdatum", "")[:10], "who": k.get("person i ledande ställning", ""),
                    "role": k.get("befattning", ""), "kind": "köp" if re.search(r"förvärv", kind, re.I) else "sälj", "vol": vol, "px": px,
                    "ccy": k.get("valuta", ""), "amt": round(vol * px), "rel": bool(k.get("närstående", "").strip() and k.get("närstående", "").lower().startswith("j"))})
    out.sort(key=lambda e: e["date"], reverse=True)
    log(f"  FI insyn: {len(rows)} rader, {len(out)} i universumet, {unmapped} utan ticker")
    return out

# ---------------------------------------------------------------- huvud
def main():
    S, by_name, isin_map = load_universe()
    own = own_tickers()
    prev = jload(OUT, {})
    cache = jload(CUSIP_CACHE, {})
    watch = jload("radar_watch.json", {}).get("managers") or DEFAULT_MANAGERS
    src = {}

    # 13F
    managers = []
    for m in watch:
        try:
            res = manager_13f(m, cache); managers.append(res)
            log(f"  13F {res['n']}: " + (res.get("err") or f"{res.get('period')} ({res.get('n_pos')} positioner, {res.get('value_musd')} MUSD, {len(res.get('events', []))} händelser)"))
        except Exception as e:
            managers.append({"n": m["n"], "cik": m.get("cik"), "err": str(e)[:80]}); log(f"  13F {m['n']}: FEL {str(e)[:80]}")
    json.dump(cache, open(CUSIP_CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    src["13F"] = {"ok": sum(1 for m in managers if not m.get("err")), "n": len(managers)}

    # 13D/13G
    try:
        f13, err = filings_13dg(S); src["13DG"] = {"ok": err is None, "n": len(f13), "err": err}
    except Exception as e:
        f13 = prev.get("filings13") or []; src["13DG"] = {"ok": False, "n": len(f13), "err": str(e)[:80]}; log("  13D/13G FEL", e)

    # ARK
    try:
        hold, err = ark_holdings()
        prev_ark = prev.get("ark_hold") or {}
        trades = ark_trades(hold, prev_ark) if hold else []
        src["ARK"] = {"ok": not err, "n": len(trades), "err": ("fonder utan fil: " + ", ".join(err)) if err else None, "asof": next((v["asof"] for v in hold.values() if v.get("asof")), None)}
        log(f"  ARK: {len(hold)} fonder, {len(trades)} förändringar mot förra filen" + (f", saknar {err}" if err else ""))
        ark_hold = {k: v for k, v in hold.items()} if hold else prev_ark
        for k, v in prev_ark.items(): ark_hold.setdefault(k, v)
    except Exception as e:
        trades = []; ark_hold = prev.get("ark_hold") or {}; src["ARK"] = {"ok": False, "n": 0, "err": str(e)[:80]}; log("  ARK FEL", e)

    # FI insyn
    try:
        insyn = fi_insyn(S, by_name, isin_map); src["INSYN"] = {"ok": True, "n": len(insyn)}
    except Exception as e:
        insyn = prev.get("insyn") or []; src["INSYN"] = {"ok": False, "n": len(insyn), "err": str(e)[:80]}; log("  FI insyn FEL", e)

    # per ticker – det appen korsar mot topp 20 och portföljer
    by = {}
    def add(t, e):
        if t and t in S: by.setdefault(t, []).append(e)
    for m in managers:
        for e in m.get("events", []):
            if e["kind"] in ("ny", "ökat", "minskat", "ut"): add(e["t"], {"src": "13F", "who": m["n"], "kind": e["kind"], "date": m.get("filed"), "w": e["w"]})
    for e in f13: add(e["t"], {"src": "13DG", "who": e.get("who") or e["form"], "kind": "anmälan ≥ 5 %", "date": e["date"], "form": e["form"]})
    for e in trades: add(e["t"], {"src": "ARK", "who": e["fund"], "kind": e["kind"], "date": e["date"], "d": e["d"], "w": e["w"]})
    for e in insyn: add(e["t"], {"src": "INSYN", "who": e["who"], "kind": e["kind"], "date": e["date"], "amt": e["amt"], "ccy": e["ccy"]})

    # ARK:s fonder som egna "investerare" med innehav och dagens rörelser
    funds = []
    for fund, v in (ark_hold or {}).items():
        hl = sorted([{"t": t, "n": x["n"], "w": round(x["w"], 2), "sh": x["sh"]} for t, x in (v.get("h") or {}).items()], key=lambda e: -e["w"])
        funds.append({"id": fund, "n": "ARK " + fund, "kind": "ark", "asof": v.get("asof"), "n_pos": len(hl), "holdings": hl[:60], "top3": hl[:3],
                      "events": [e for e in trades if e["fund"] == fund][:60]})
    for m in managers: m["id"] = f"m{m.get('cik') or m['n']}"; m["kind"] = "13f"
    # konsensus: aktier där flera investerare gjort samma sak (13F senaste rapport, ARK senaste dag, 13D/13G)
    cons = {}
    def c_add(t, who, side, date):
        if not t or t not in S: return
        c = cons.setdefault(t, {"t": t, "buy": [], "sell": [], "last": ""})
        (c["buy"] if side == "buy" else c["sell"]).append(who); c["last"] = max(c["last"], date or "")
    for m in managers:
        for e in m.get("events", []):
            if e["kind"] in ("ny", "ökat"): c_add(e["t"], m["n"], "buy", m.get("filed"))
            elif e["kind"] in ("minskat", "ut"): c_add(e["t"], m["n"], "sell", m.get("filed"))
    for e in trades: c_add(e["t"], "ARK " + e["fund"], "buy" if e["kind"] == "köp" else "sell", e["date"])
    for e in f13: c_add(e["t"], e.get("who") or e["form"], "buy", e["date"])
    consensus = sorted(cons.values(), key=lambda c: (-(len(c["buy"]) + len(c["sell"])), c["last"]), reverse=False)
    consensus = sorted(consensus, key=lambda c: -(len(set(c["buy"])) + len(set(c["sell"]))))[:60]
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": src,
           "managers": managers, "funds": funds, "consensus": consensus, "filings13": f13[:200], "ark": trades[:300], "ark_hold": ark_hold, "insyn": insyn[:300],
           "byTicker": by, "own_hits": sorted(t for t in own if t in by)}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"Skrev {OUT}: {len(by)} aktier i universumet med radarhändelser, egna träffar: {out['own_hits']}")

if __name__ == "__main__":
    main()
