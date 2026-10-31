"""SEC EDGAR feeds: Form 4 (insiders), Schedule 13D/13G (5%+ stakes), 13F-HR (funds).

Uses EDGAR's "latest filings" Atom feed, which updates within minutes of a
filing being accepted. SEC asks for <=10 requests/second and a User-Agent
with your name and email.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET

import requests

from core import Event, to_num

ATOM = "{http://www.w3.org/2005/Atom}"
CURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={t}"
           "&company=&dateb=&owner=include&start=0&count=100&output=atom")
TITLE_RE = re.compile(r"^(?P<form>.+?) - (?P<name>.+?) \((?P<cik>\d{6,10})\) \((?P<role>[^)]+)\)\s*$")


class SECClient:
    def __init__(self, user_agent: str):
        if not user_agent or "@" not in user_agent:
            raise ValueError("Set sec.user_agent (or SEC_USER_AGENT) to 'Your Name your@email.com'")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last = 0.0
        self._tickers: dict[int, str] = {}
        self._tickers_at = 0.0

    def get(self, url: str) -> requests.Response:
        wait = 0.15 - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        r = self.s.get(url, timeout=25)
        if r.status_code == 429:
            time.sleep(10)
            r = self.s.get(url, timeout=25)
        r.raise_for_status()
        return r

    def ticker_for_cik(self, cik: int | str) -> str:
        if time.time() - self._tickers_at > 86400:
            try:
                data = self.get("https://www.sec.gov/files/company_tickers.json").json()
                self._tickers = {int(v["cik_str"]): v["ticker"] for v in data.values()}
                self._tickers_at = time.time()
            except Exception as ex:
                print(f"[sec] ticker map failed: {ex}")
        try:
            return self._tickers.get(int(cik), "")
        except (TypeError, ValueError):
            return ""

    def folder_files(self, cik: str, acc: str) -> list[str]:
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/index.json"
        items = self.get(url).json().get("directory", {}).get("item", [])
        return [i["name"] for i in items]

    def doc_url(self, cik: str, acc: str, name: str) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{name}"


# ---------------------------------------------------------------- latest-filings feed

def parse_current_feed(xml_text: str) -> dict[str, dict]:
    """Group feed entries by accession number -> {form, url, updated, parties:{role:(name,cik)}}."""
    root = ET.fromstring(xml_text)
    out: dict[str, dict] = {}
    for en in root.findall(f"{ATOM}entry"):
        title = (en.findtext(f"{ATOM}title") or "").strip()
        eid = en.findtext(f"{ATOM}id") or ""
        link = en.find(f"{ATOM}link")
        href = link.get("href") if link is not None else ""
        m_acc = re.search(r"accession-number=(\d{10}-\d{2}-\d{6})", eid) or \
            re.search(r"(\d{10}-\d{2}-\d{6})", href)
        m = TITLE_RE.match(title)
        if not m_acc or not m:
            continue
        acc = m_acc.group(1)
        rec = out.setdefault(acc, {"form": m["form"].strip(), "url": href,
                                   "updated": en.findtext(f"{ATOM}updated") or "", "parties": {}})
        rec["parties"][m["role"].strip()] = (m["name"].strip(), m["cik"])
    return out


def fetch_current(sec: SECClient, form_type: str, pages: int = 1) -> dict[str, dict]:
    out: dict[str, dict] = {}
    url = CURRENT.format(t=requests.utils.quote(form_type))
    for p in range(pages):
        page = parse_current_feed(sec.get(url.replace("start=0", f"start={p * 100}")).text)
        for acc, rec in page.items():
            if acc in out:
                out[acc]["parties"].update(rec["parties"])
            else:
                out[acc] = rec
    return out


# ---------------------------------------------------------------- Form 4

def _v(node, path: str) -> str:
    if node is None:
        return ""
    x = node.find(path)
    if x is None:
        return ""
    val = x.find("value")
    return ((val.text if val is not None else x.text) or "").strip()


def parse_form4(xml_text: str, acc: str, url: str, codes: list[str]) -> list[Event]:
    root = ET.fromstring(xml_text)
    sym = (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper()
    company = (root.findtext("issuer/issuerName") or "").strip()
    owners, roles = [], []
    for ro in root.findall("reportingOwner"):
        owners.append((ro.findtext("reportingOwnerId/rptOwnerName") or "").strip())
        rel = ro.find("reportingOwnerRelationship")
        if rel is not None:
            def yes(tag):
                return (rel.findtext(tag) or "").strip().lower() in ("1", "true")
            if yes("isDirector"):
                roles.append("Director")
            if yes("isOfficer"):
                roles.append((rel.findtext("officerTitle") or "Officer").strip())
            if yes("isTenPercentOwner"):
                roles.append("10% owner")
    plan = (root.findtext("aff10b5One") or "").strip().lower() in ("1", "true")

    agg: dict[str, dict] = {}
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip().upper()
        if code not in codes:
            continue
        sh = to_num(_v(tx, "transactionAmounts/transactionShares")) or 0.0
        px = to_num(_v(tx, "transactionAmounts/transactionPricePerShare")) or 0.0
        a = agg.setdefault(code, {"sh": 0.0, "val": 0.0, "dates": set(), "after": ""})
        a["sh"] += sh
        a["val"] += sh * px
        a["dates"].add(_v(tx, "transactionDate"))
        a["after"] = _v(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction") or a["after"]

    out = []
    for code, a in agg.items():
        action = "BUY" if code == "P" else "SELL" if code == "S" else "OTHER"
        label = {"P": "INSIDER BUY", "S": "INSIDER SELL"}.get(code, f"INSIDER {code}")
        extra = []
        if a["after"]:
            extra.append(f"Owns after: {to_num(a['after']) or 0:,.0f} sh")
        if plan:
            extra.append("Pre-planned 10b5-1 trade (weaker signal)")
        out.append(Event(
            id=f"{acc}:{code}", feed="sec_form4", market="us", kind=label, action=action,
            who=" & ".join(o for o in owners if o), role=", ".join(dict.fromkeys(roles)),
            symbol=sym, company=company, qty=a["sh"] or None,
            price=(a["val"] / a["sh"]) if a["sh"] else None, value=a["val"] or None,
            trade_date=", ".join(sorted(d for d in a["dates"] if d)), url=url, extra=extra,
        ))
    return out


def fetch_form4(sec: SECClient, state, codes: list[str]) -> list[Event]:
    feed = fetch_current(sec, "4", pages=2)
    events = []
    for acc, rec in feed.items():
        if rec["form"] != "4" or state.seen("sec_form4_acc", acc):
            continue
        name, cik = rec["parties"].get("Issuer") or rec["parties"].get("Reporting") or ("", "")
        try:
            files = sec.folder_files(cik, acc)
            xml_name = next((f for f in files if f.lower().endswith(".xml")
                             and "summary" not in f.lower()), None)
            if xml_name:
                events += parse_form4(sec.get(sec.doc_url(cik, acc, xml_name)).text,
                                      acc, rec["url"], codes)
            state.add("sec_form4_acc", acc)
        except Exception as ex:
            print(f"[sec_form4] {acc}: {ex}")   # retried next cycle
    return events


# ---------------------------------------------------------------- 13D / 13G

def events_13dg(feed: dict[str, dict], sec: SECClient | None = None) -> list[Event]:
    out = []
    for acc, rec in feed.items():
        form = rec["form"].upper()
        if "13D" not in form and "13G" not in form:
            continue
        subj, subj_cik = rec["parties"].get("Subject", ("", ""))
        filer, _ = rec["parties"].get("Filed by", ("", ""))
        amend = form.endswith("/A")
        if "13D" in form:
            kind = "ACTIVIST STAKE UPDATE (13D/A)" if amend else "NEW ACTIVIST STAKE (13D)"
        else:
            kind = "PASSIVE STAKE UPDATE (13G/A)" if amend else "NEW 5%+ STAKE (13G)"
        out.append(Event(
            id=acc, feed="sec_13dg", market="us", kind=kind, action="OTHER",
            who=filer, symbol=(sec.ticker_for_cik(subj_cik) if sec and subj_cik else ""),
            company=subj, filed=rec["updated"][:10], url=rec["url"],
            extra=["Owns 5%+ of the company (filed within days of crossing)"] if not amend else [],
        ))
    return out


def fetch_13dg(sec: SECClient) -> list[Event]:
    return events_13dg(fetch_current(sec, "SCHEDULE 13"), sec)


# ---------------------------------------------------------------- 13F

def top_holdings_13f(xml_text: str, n: int = 8) -> list[tuple[str, float]]:
    xml_text = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml_text)      # drop namespaces
    xml_text = re.sub(r"<(/?)\w+:", r"<\1", xml_text)
    root = ET.fromstring(xml_text)
    agg: dict[str, float] = {}
    for it in root.iter("infoTable"):
        name = (it.findtext("nameOfIssuer") or "").strip()
        agg[name] = agg.get(name, 0.0) + (to_num(it.findtext("value")) or 0.0)
    return sorted(agg.items(), key=lambda kv: -kv[1])[:n]


def fetch_13f(sec: SECClient, state, investors: list[str]) -> list[Event]:
    feed = fetch_current(sec, "13F-HR")
    out = []
    names = [i.lower() for i in investors if i]
    for acc, rec in feed.items():
        filer, cik = rec["parties"].get("Filer", ("", ""))
        ev = Event(id=acc, feed="sec_13f", market="us", kind="FUND PORTFOLIO (13F)",
                   action="OTHER", who=filer, filed=rec["updated"][:10], url=rec["url"],
                   extra=["Quarter-end holdings (can be up to 45 days old)"])
        if any(n in filer.lower() for n in names) and not state.seen("sec_13f", acc):
            try:   # only download holdings for tracked funds
                files = sec.folder_files(cik, acc)
                info = next((f for f in files if f.lower().endswith(".xml")
                             and "primary_doc" not in f.lower()), None)
                if info:
                    top = top_holdings_13f(sec.get(sec.doc_url(cik, acc, info)).text)
                    ev.symbols = []
                    ev.extra.append("Top holdings: " + "; ".join(
                        f"{k.title()} ${v / 1e6:,.0f}M" for k, v in top))
            except Exception as ex:
                print(f"[sec_13f] {acc}: {ex}")
        out.append(ev)
    return out
