"""
dtlog.py – logg över daghandelskandidater och hur de gick (2026-11-24, information – styr inget).

Varför: daghandeln kan inte backtestas (vi har bara intradagsdata för dagen och veckan). Loggen bygger egna data:
varje handelsdag sparas de 30 kandidater som ligger högst på det sammanvägda måttet (dtq) vid första skanningen
mellan 12:00 och 15:00 UTC (Norden mitt på dagen, USA före öppning). Nästa skanning efter dagen fyller i utfallet ur
dagsdata: öppning, högsta, lägsta, stängning – avkastning från öppning till stängning, från loggens kurs till stängning,
högsta uppgång från öppning, samt samma dags median för alla aktier på samma börs (jämförelse).

Utfil: dtlog.json (main). Appen visar en sammanfattning på daghandelssidan.
"""
import json, datetime as dt

PATH = "dtlog.json"
KEEP_DAYS = 400

def _load():
    try: return json.load(open(PATH, encoding="utf-8"))
    except Exception: return {"v": 1, "days": []}

def _suf(t): return t.rsplit(".", 1)[1] if "." in t else ""

def _bar(df, day):
    try:
        m = df[df.index.date == day]
        if not len(m): return None
        r = m.iloc[-1]
        o, h, l, c = float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
        return (o, h, l, c) if o > 0 and c > 0 else None
    except Exception:
        return None

def settle(log, daily, today):
    """Fyll i utfall för loggade dagar före i dag, med dagsdata (öppning/högsta/lägsta/stängning)."""
    n = 0
    for dday in log["days"]:
        if dday.get("done") or dday["date"] >= today: continue
        day = dt.date.fromisoformat(dday["date"])
        bench = {}
        for e in dday["rows"]:
            df = daily.get(e["t"])
            b = _bar(df, day) if df is not None else None
            if not b: continue
            o, h, l, c = b
            e["o"], e["c"] = round(o, 4), round(c, 4)
            e["oc"] = round((c / o - 1) * 100, 2)
            e["lc"] = round((c / e["px"] - 1) * 100, 2) if e.get("px") else None
            e["mfe"] = round((h / o - 1) * 100, 2); e["mae"] = round((l / o - 1) * 100, 2)
            bench.setdefault(_suf(e["t"]), None)
        for sf in bench:   # samma dags median öppning→stängning för alla aktier på börsen
            v = []
            for t, df in daily.items():
                if _suf(t) != sf: continue
                b = _bar(df, day)
                if b: v.append((b[3] / b[0] - 1) * 100)
            if v: bench[sf] = round(sorted(v)[len(v) // 2], 2)
        dday["bench"] = {k: v for k, v in bench.items() if v is not None}
        if any("oc" in e for e in dday["rows"]): dday["done"] = True; n += 1
    return n

def record(log, recs, dtrank, now):
    """Spara dagens 30 högst rankade kandidater (kandidater + nästan-kandidater, alla börser) en gång per dag."""
    today = now.date().isoformat()
    if now.weekday() >= 5 or not (12 <= now.hour < 15): return False
    if any(d["date"] == today for d in log["days"]): return False
    ids = [t for t in dict.fromkeys((dtrank or {}).get("cand", []) + (dtrank or {}).get("near", [])) if t in recs]
    ids.sort(key=lambda t: -(recs[t].get("dtq") or 0))
    rows = []
    for t in ids[:30]:
        r = recs[t]
        rows.append({"t": t, "dtq": r.get("dtq"), "c5": r.get("dtqc"), "dok": bool(r.get("dok")), "dtn": r.get("dtn"), "dte": r.get("dte"),
                     "px": r.get("px"), "chg": r.get("chg"), "vx": r.get("vx"), "pmv": r.get("pmv")})
    if not rows: return False
    log["days"].append({"date": today, "at": now.strftime("%H:%MZ"), "rows": rows})
    return True

def update(recs, dtrank, daily):
    now = dt.datetime.now(dt.timezone.utc)
    log = _load()
    s = settle(log, daily, now.date().isoformat())
    r = record(log, recs, dtrank, now)
    cutoff = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    log["days"] = [d for d in log["days"] if d["date"] >= cutoff]
    log["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
    json.dump(log, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"Daghandelslogg: {'ny dag loggad' if r else 'ingen ny loggning'}, {s} dagar fick utfall, totalt {len(log['days'])} dagar")
