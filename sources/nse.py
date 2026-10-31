"""NSE India feeds: SEBI PIT insider trades, bulk/block deals, SAST.

NSE's site requires a browser-like session (cookies from the home page)
and tends to block data-centre IPs, so run these feeds from your own
computer in India.
"""
from __future__ import annotations

import csv
import html
import io
import re
import time
from datetime import date, timedelta

import requests

from core import Event, make_id, pick, to_num

BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}


class NSEClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.primed_at = 0.0

    def _prime(self, force: bool = False) -> None:
        if force or time.time() - self.primed_at > 600:
            self.s.get(BASE, timeout=15)
            self.s.get(BASE + "/companies-listing/corporate-filings-insider-trading", timeout=15)
            self.primed_at = time.time()

    def get_json(self, path: str):
        self._prime()
        for attempt in range(3):
            r = self.s.get(BASE + path, timeout=20)
            if r.status_code in (401, 403):
                self._prime(force=True)
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"NSE refused {path} (status {r.status_code}) - "
                           "NSE often blocks cloud servers; run India feeds locally.")

    def get_text(self, url: str) -> str:
        r = self.s.get(url, timeout=20)
        r.raise_for_status()
        return r.text


def _range(days: int = 3) -> str:
    t = date.today()
    f = t - timedelta(days=days)
    return f"from_date={f:%d-%m-%Y}&to_date={t:%d-%m-%Y}"


def _link(sym: str) -> str:
    return f"{BASE}/get-quotes/equity?symbol={sym}" if sym else BASE


# ---------------------------------------------------------------- insider (SEBI PIT)

def parse_insider(rows: list[dict], buy_sell_only: bool = True) -> list[Event]:
    out = []
    for r in rows:
        ttype = str(pick(r, "tdpTransactionType", "acqMode", "secType")).strip()
        tl = ttype.lower()
        if "buy" in tl or "acqui" in tl or "purchase" in tl:
            action = "BUY"
        elif "sell" in tl or "sale" in tl or "dispos" in tl:
            action = "SELL"
        else:
            action = "OTHER"
        mode = str(pick(r, "acqMode")).lower()
        if buy_sell_only:
            if action == "OTHER":
                continue
            # only on-exchange trades; pledges, ESOPs, gifts, off-market transfers are not signals
            on_market = ("market" in mode and "off" not in mode) or "block" in mode or "bulk" in mode
            if mode and not on_market:
                continue
        sym = str(pick(r, "symbol")).upper()
        who = str(pick(r, "acqName", "name"))
        qty = to_num(pick(r, "secAcq", "buyQuantity", "sellquantity", default=None))
        val = to_num(pick(r, "secVal", "buyValue", "sellValue", default=None))
        price = (val / qty) if (val and qty) else None
        did = pick(r, "did", "pid", default="")
        rid = str(did) if did else make_id(sym, who, qty, val, pick(r, "acqfromDt"), pick(r, "intimDt"))
        before = pick(r, "befAcqSharesPer", default="")
        after = pick(r, "afterAcqSharesPer", default="")
        extra = []
        if before != "" and after != "":
            extra.append(f"Holding: {before}% → {after}%")
        if mode:
            extra.append(f"Mode: {pick(r, 'acqMode')}")
        out.append(Event(
            id=rid, feed="nse_insider", market="india",
            kind=f"INSIDER {action}", action=action, who=who,
            role=str(pick(r, "personCategory")), symbol=sym,
            company=str(pick(r, "company")), qty=qty, price=price, value=val,
            trade_date=str(pick(r, "acqfromDt")), filed=str(pick(r, "intimDt", "date")),
            url=str(pick(r, "xbrl", default="")) or _link(sym), extra=extra,
        ))
    return out


_FACT_RE = re.compile(r"<[\w-]+:([\w-]+)\s[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]*)</")


def _pct(v: str) -> str:
    n = to_num(v)
    if n is None:
        return ""
    return f"{n * 100:.2f}" if n <= 1 else f"{n:.2f}"     # XBRL stores 0.0854 for 8.54%


def parse_insider_xbrl(xml_text: str, meta: dict) -> list[dict]:
    """One NSE SEBI-PIT XBRL filing -> rows (one per person/disclosure) in the old JSON shape."""
    ctx: dict[str, dict[str, str]] = {}
    for tag, ref, val in _FACT_RE.findall(xml_text):
        ctx.setdefault(ref, {})[tag] = html.unescape(val.strip())
    main = ctx.get("MainI", {})
    rows = []
    for ref, f in ctx.items():
        if ref == "MainI" or "NameOfThePerson" not in f:
            continue
        rows.append({
            "did": f"{meta.get('fid', '')}#{ref}",
            "symbol": main.get("Symbol") or meta.get("symbol", ""),
            "company": main.get("NameOfTheCompany") or meta.get("companyName", ""),
            "acqName": f.get("NameOfThePerson", ""),
            "personCategory": f.get("CategoryOfPerson", ""),
            "secType": f.get("TypeOfInstrument", ""),
            "secAcq": f.get("SecuritiesAcquiredOrDisposedNumberOfSecurity", ""),
            "secVal": f.get("SecuritiesAcquiredOrDisposedValueOfSecurity", ""),
            "tdpTransactionType": f.get("SecuritiesAcquiredOrDisposedTransactionType", ""),
            "acqMode": f.get("ModeOfAcquisitionOrDisposal", ""),
            "befAcqSharesPer": _pct(f.get(
                "SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding", "")),
            "afterAcqSharesPer": _pct(f.get(
                "SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding", "")),
            "acqfromDt": f.get(
                "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate", ""),
            "intimDt": meta.get("broadcastDateTime", "") or main.get("DateOfFiling", ""),
            "xbrl": meta.get("ixbrl", ""),
        })
    return rows


def fetch_insider(nse: NSEClient, state, buy_sell_only: bool = True,
                  seeding: bool = False, max_files: int = 40) -> list[Event]:
    data = nse.get_json("/api/corporates-pit-gg?index=equities")
    filings = data.get("data", []) if isinstance(data, dict) else (data or [])
    events: list[Event] = []
    done = 0
    for fl in filings:                     # newest first
        url = fl.get("xmlFileName") or ""
        fid = url.rsplit("/", 1)[-1] or f"{fl.get('appId')}|{fl.get('broadcastDateTime')}"
        if not url or state.seen("nse_pit_files", fid):
            continue
        if seeding:
            state.add("nse_pit_files", fid)
            continue
        if done >= max_files:              # rest are picked up next cycle
            break
        try:
            rows = parse_insider_xbrl(nse.get_text(url), {**fl, "fid": fid})
            events += parse_insider(rows, buy_sell_only)
            state.add("nse_pit_files", fid)
            done += 1
        except Exception as ex:
            print(f"[nse_insider] {fid}: {ex}")
    return events


# ---------------------------------------------------------------- bulk / block deals

def _deal_event(kind: str, r: dict) -> Event | None:
    sym = str(pick(r, "symbol", "Symbol")).upper().strip()
    who = str(pick(r, "clientName", "Client Name")).strip()
    side = str(pick(r, "buySell", "Buy/Sell")).upper().strip()
    action = "BUY" if side.startswith("B") else "SELL" if side.startswith("S") else "OTHER"
    qty = to_num(pick(r, "qty", "Quantity Traded", default=None))
    price = to_num(pick(r, "watp", "Trade Price / Wght. Avg. Price", default=None))
    if not sym:
        return None
    d = str(pick(r, "date", "Date")).strip()
    return Event(
        id=make_id(kind, d, sym, who, side, qty, price), feed="nse_deals", market="india",
        kind=f"{kind} DEAL {action}", action=action, who=who, symbol=sym,
        company=str(pick(r, "name", "Security Name")), qty=qty, price=price,
        value=(qty * price) if (qty and price) else None, trade_date=d,
        url=f"{BASE}/report-detail/display-bulk-and-block-deals",
    )


def parse_largedeals(data: dict) -> list[Event]:
    out = []
    for key, kind in (("BULK_DEALS_DATA", "BULK"), ("BLOCK_DEALS_DATA", "BLOCK")):
        for r in data.get(key) or []:
            e = _deal_event(kind, r)
            if e:
                out.append(e)
    return out


def parse_deals_csv(text: str, kind: str) -> list[Event]:
    rdr = csv.DictReader(io.StringIO(text))
    rdr.fieldnames = [f.strip() for f in (rdr.fieldnames or [])]
    out = []
    for r in rdr:
        e = _deal_event(kind, {k.strip(): (v or "").strip() for k, v in r.items() if k})
        if e:
            out.append(e)
    return out


def fetch_deals(nse: NSEClient) -> list[Event]:
    try:
        return parse_largedeals(nse.get_json("/api/snapshot-capital-market-largedeal"))
    except Exception as ex:  # fall back to the daily CSV archives
        print(f"[nse_deals] JSON failed ({ex}); using CSV archive")
        out = []
        for kind, f in (("BULK", "bulk.csv"), ("BLOCK", "block.csv")):
            out += parse_deals_csv(nse.get_text(f"https://archives.nseindia.com/content/equities/{f}"), kind)
        return out


# ---------------------------------------------------------------- SAST (big stake changes)

def parse_sast(rows: list[dict]) -> list[Event]:
    out = []
    for r in rows:
        sym = str(pick(r, "symbol")).upper()
        who = str(pick(r, "acquirerName", "acqName", "nameOfAcquirer", "promoterName"))
        t = str(pick(r, "acqSaleType", "acquisitionType", "type")).lower()
        action = "SELL" if ("sale" in t or "dispos" in t) else "BUY" if t else "OTHER"
        qty = to_num(pick(r, "noOfShareAcq", "noOfShareSale", "noOfShares", default=None))
        after = pick(r, "totAcqShare", "totalShareAfter", "afterAcqPer", default="")
        extra = [f"Holding after: {after}%"] if after != "" else []
        out.append(Event(
            id=str(pick(r, "id", "did", default="")) or make_id(sym, who, qty, pick(r, "timestamp", "date")),
            feed="nse_sast", market="india", kind=f"STAKE CHANGE (SAST) {action}",
            action=action, who=who, symbol=sym, company=str(pick(r, "company")),
            qty=qty, filed=str(pick(r, "timestamp", "date", "intimDt")),
            url=str(pick(r, "attachement", "attachment", "xbrl", default="")) or _link(sym), extra=extra,
        ))
    return out


def fetch_sast(nse: NSEClient) -> list[Event]:
    data = nse.get_json(f"/api/corporate-sast-reg29?index=equities&{_range(3)}")
    rows = data.get("data", data) if isinstance(data, dict) else data
    return parse_sast(rows or [])
