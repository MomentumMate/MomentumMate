#!/usr/bin/env python3
"""
pt_hist.py – riktkurshistorik för T12 (TESTPLAN_3.0, 2026-11-25). Information/test – styr inget.

Hämtar för hela universumet (nycklarna i data.json) Yahoos graderingshistorik med riktkurser
(datum, analyshus, ny och tidigare riktkurs) och aktiesplittar. Riktkurser satta före en split delas
med splitfaktorn, så att de kan jämföras med backtestets splitjusterade kurser.

Utfil: pt_hist.json.gz  {"skapad", "täckning": {...}, "data": {ticker: [[datum, analyshus, pt, ppt|null], ...]}}
Publiceras på grenen pthist (inte main – filen är stor).
"""
import gzip, json, os, sys, time, datetime as dt
import yfinance as yf

LIMIT_MIN = float(os.environ.get("PT_LIMIT_MIN", "150"))


def num(v):
    try:
        f = float(v)
        return f if f == f and f > 0 else None
    except Exception:
        return None


def fetch(t):
    tk = yf.Ticker(t)
    ud = tk.upgrades_downgrades
    rows = []
    if ud is None or not len(ud):
        return rows, 0
    try:
        sp = tk.splits
        splits = [(str(d)[:10], float(r)) for d, r in sp.items() if float(r) > 0 and float(r) != 1.0] if sp is not None else []
    except Exception:
        splits = []
    ud = ud.reset_index()
    for _, r in ud.iterrows():
        d = str(r.get("GradeDate", ""))[:10]
        pt, ppt = num(r.get("currentPriceTarget")), num(r.get("priorPriceTarget"))
        if not d or not pt:
            continue
        f = 1.0
        for sd, ratio in splits:          # split efter riktkursens datum → dela riktkursen
            if sd > d:
                f *= ratio
        rows.append([d, str(r.get("Firm", ""))[:40], round(pt / f, 4), round(ppt / f, 4) if ppt else None])
    rows.sort()
    return rows, len(splits)


def main():
    D = json.load(open("data.json", encoding="utf-8"))
    S = D["stocks"]
    tickers = sorted(t for t in S if not t.endswith("-USD"))
    t0 = time.time()
    out, err, nsplit = {}, 0, 0
    for k, t in enumerate(tickers):
        if (time.time() - t0) / 60 > LIMIT_MIN:
            print(f"Tidsgräns efter {k} bolag"); break
        try:
            rows, ns = fetch(t)
            if rows:
                out[t] = rows; nsplit += ns
        except Exception as e:
            err += 1
            if err <= 10: print("fel", t, str(e)[:80])
        if k % 10 == 9: time.sleep(1.0)
        if k % 200 == 199: print(f"{k + 1} bolag, {len(out)} med riktkurser, {(time.time() - t0) / 60:.1f} min", flush=True)
    # täckning: första år med riktkurs, per marknad (suffix)
    suf = lambda t: t.rsplit(".", 1)[1] if "." in t else "US"
    cov = {}
    for t, rows in out.items():
        c = cov.setdefault(suf(t), {"bolag": 0, "rader": 0, "första": "9999"})
        c["bolag"] += 1; c["rader"] += len(rows); c["första"] = min(c["första"], rows[0][0])
    per_year = {}
    for rows in out.values():
        for y in {r[0][:4] for r in rows}:
            per_year[y] = per_year.get(y, 0) + 1
    res = {"skapad": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "bolag_hämtade": len(tickers),
           "bolag_med_riktkurs": len(out), "fel": err, "splitar": nsplit, "täckning": cov,
           "bolag_med_riktkurs_per_år": dict(sorted(per_year.items())), "data": out}
    with gzip.open("pt_hist.json.gz", "wt", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({k: v for k, v in res.items() if k != "data"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
