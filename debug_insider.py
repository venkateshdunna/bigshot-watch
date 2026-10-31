"""Tries several request formats for NSE's insider-trading feed and reports which return data."""
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import nse  # noqa: E402

t = date.today()
f7 = t - timedelta(days=7)
f30 = t - timedelta(days=30)
variants = [
    "/api/corporates-pit?index=equities",
    f"/api/corporates-pit?index=equities&from_date={f7:%d-%m-%Y}&to_date={t:%d-%m-%Y}",
    f"/api/corporates-pit?index=equities&from_date={f30:%d-%m-%Y}&to_date={t:%d-%m-%Y}",
    f"/api/corporates-pit?index=equities&from_date={f7:%d-%m-%Y}&to_date={t:%d-%m-%Y}&period=1W",
    "/api/corporates-pit?index=equities&period=1W",
    "/api/corporates-pit?index=equities&period=1D",
    "/api/corporates-pit",
    f"/api/corporates-pit?from_date={f7:%d-%m-%Y}&to_date={t:%d-%m-%Y}",
]

c = nse.NSEClient()
best = None
for i, v in enumerate(variants, 1):
    try:
        d = c.get_json(v)
        rows = d.get("data", d) if isinstance(d, dict) else d
        n = len(rows) if isinstance(rows, list) else 0
        print(f"V{i}: {n:>5} rows   {v}")
        if n and (best is None or n > best[1]):
            best = (i, n, rows)
    except Exception as ex:
        print(f"V{i}: ERROR {str(ex)[:80]}   {v}")

if best:
    i, n, rows = best
    print(f"\nBest: V{i}")
    print("Fields:", sorted(rows[0].keys()))
    for key in ("tdpTransactionType", "acqMode", "personCategory"):
        print(f"{key}:", dict(Counter(str(r.get(key)) for r in rows).most_common(8)))
    print("\nFirst row:", json.dumps(rows[0], ensure_ascii=False)[:900])
    print("\nParsed with filter:", len(nse.parse_insider(rows, True)),
          "| without:", len(nse.parse_insider(rows, False)))
else:
    print("\nNo variant returned rows.")
