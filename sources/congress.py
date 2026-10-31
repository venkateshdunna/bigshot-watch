"""US House members' stock trades (STOCK Act Periodic Transaction Reports).

The House Clerk republishes a yearly index (ZIP with XML) as filings arrive;
each PTR is a PDF. We read the index, then pull tickers/amounts from new PDFs.
Note: by law these can be filed up to 45 days after the trade.
(Senate filings sit behind an interactive site and are not covered yet.)
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

import requests

from core import Event

BASE = "https://disclosures-clerk.house.gov/public_disc"
UA = {"User-Agent": "Mozilla/5.0 (BigShotWatch personal alerts)"}

TICKER_RE = re.compile(r"\(([A-Z][A-Z.\-]{0,6})\)\s*\[(?:ST|OP|ET|PS)\]")
AMOUNT_RE = re.compile(r"\$[\d,]+\s*-\s*\$?[\d,]+|Over \$[\d,]+")
TYPE_RE = re.compile(r"(?:^|\s)(P|S \(partial\)|S|E)(?=\s+\d{2}/\d{2}/\d{4})")


def parse_index(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for m in root.findall("Member"):
        if (m.findtext("FilingType") or "").strip() != "P":
            continue
        name = " ".join(x for x in (
            (m.findtext("Prefix") or "").strip(), (m.findtext("First") or "").strip(),
            (m.findtext("Last") or "").strip(), (m.findtext("Suffix") or "").strip()) if x)
        out.append({
            "doc": (m.findtext("DocID") or "").strip(),
            "name": name, "district": (m.findtext("StateDst") or "").strip(),
            "date": (m.findtext("FilingDate") or "").strip(),
            "year": (m.findtext("Year") or "").strip(),
        })
    return out


def parse_ptr_text(text: str) -> list[dict]:
    """Best-effort: list of {ticker, type, amount} from a PTR's text."""
    flat = re.sub(r"\s+", " ", text)
    trades = []
    for m in TICKER_RE.finditer(flat):
        tail = flat[m.end(): m.end() + 220]
        t = TYPE_RE.search(" " + tail)
        a = AMOUNT_RE.search(tail)
        trades.append({
            "ticker": m.group(1),
            "type": (t.group(1) if t else "?"),
            "amount": (a.group(0).replace(" ", "") if a else ""),
        })
    return trades


def _pdf_text(blob: bytes) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(blob)).pages)
    except Exception:
        return ""


def build_event(f: dict, trades: list[dict], url: str) -> Event:
    buys = [t for t in trades if t["type"] == "P"]
    sells = [t for t in trades if t["type"].startswith("S")]
    action = "BUY" if buys and not sells else "SELL" if sells and not buys else "OTHER"
    label = {"P": "Buy", "S": "Sell", "S (partial)": "Partial sell", "E": "Exchange"}
    extra = [f"{label.get(t['type'], t['type'])} {t['ticker']} {t['amount']}".strip()
             for t in trades[:12]]
    if len(trades) > 12:
        extra.append(f"... +{len(trades) - 12} more")
    if not trades:
        extra.append("Couldn't read tickers (scanned PDF) - open the filing")
    extra.append("Amounts are ranges; trades may be up to 45 days old")
    return Event(
        id=f["doc"], feed="house_ptr", market="us", kind="CONGRESS TRADE",
        action=action, who=f["name"], role=f"US House, {f['district']}",
        symbols=[t["ticker"] for t in trades], filed=f["date"], url=url, extra=extra,
    )


def fetch_house(state, seeding: bool = False, max_new: int | None = None) -> list[Event]:
    year = date.today().year
    r = requests.get(f"{BASE}/financial-pdfs/{year}FD.zip", headers=UA, timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml_name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        filings = parse_index(z.read(xml_name))
    events = []
    fresh = [f for f in filings if f["doc"] and not state.seen("house_ptr", f["doc"])]
    if seeding:                                  # first run: mark as seen, skip PDFs
        return [Event(id=f["doc"], feed="house_ptr", market="us",
                      kind="CONGRESS TRADE", action="OTHER") for f in fresh]
    if max_new:                                  # test runs: only the latest few
        fresh = sorted(fresh, key=lambda f: f["doc"])[-max_new:]
    for f in fresh:
        url = f"{BASE}/ptr-pdfs/{f['year'] or year}/{f['doc']}.pdf"
        trades = []
        try:
            pr = requests.get(url, headers=UA, timeout=60)
            if pr.ok:
                trades = parse_ptr_text(_pdf_text(pr.content))
        except requests.RequestException as ex:
            print(f"[house_ptr] {f['doc']}: {ex}")
            continue
        events.append(build_event(f, trades, url))
    return events
