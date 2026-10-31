"""Shared pieces: event model, config, state, formatting, Telegram."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

# Windows consoles default to a legacy code page; never crash on ₹ or emoji.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- event


@dataclass
class Event:
    id: str                    # unique per feed
    feed: str                  # e.g. "nse_insider"
    market: str                # "india" | "us"
    kind: str                  # headline label, e.g. "INSIDER BUY"
    action: str                # "BUY" | "SELL" | "OTHER"
    who: str = ""              # person / fund
    role: str = ""             # promoter, director, CEO, 10% owner...
    symbol: str = ""
    company: str = ""
    qty: float | None = None
    price: float | None = None
    value: float | None = None  # in local currency (INR / USD)
    trade_date: str = ""
    filed: str = ""
    url: str = ""
    extra: list[str] = field(default_factory=list)   # extra lines for the message
    symbols: list[str] = field(default_factory=list) # for multi-stock items (Congress, 13F)
    reasons: list[str] = field(default_factory=list) # filled by the filter

    def all_symbols(self) -> set[str]:
        s = {x.upper() for x in self.symbols if x}
        if self.symbol:
            s.add(self.symbol.upper())
        return s


def make_id(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def to_num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("₹", "").replace("$", "").strip()
    if s in ("", "-", "NA", "N/A", "Nil", "nil"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(d: dict, *keys: str, default: Any = "") -> Any:
    """First non-empty value among several possible field names."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", "-"):
            return v
    return default


# ---------------------------------------------------------------- config

DEFAULTS: dict = {
    "telegram": {"bot_token": "", "chat_id": ""},
    "sec": {"user_agent": ""},
    "watchlist": {"india": [], "us": []},
    "investors": {"india": [], "us": []},
    "thresholds": {"india_inr": 1e8, "us_usd": 1e6, "us_initial_13d_anywhere": True},
    "feeds": {},
    "india_buy_sell_only": True,
    "seed_on_first_run": True,
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path: str | None) -> dict:
    data: dict = {}
    if os.environ.get("CONFIG_YAML"):               # whole config from a secret (GitHub)
        data = yaml.safe_load(os.environ["CONFIG_YAML"]) or {}
    elif path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULTS, data)
    env = os.environ.get
    cfg["telegram"]["bot_token"] = env("TELEGRAM_BOT_TOKEN") or cfg["telegram"]["bot_token"]
    cfg["telegram"]["chat_id"] = env("TELEGRAM_CHAT_ID") or cfg["telegram"]["chat_id"]
    cfg["sec"]["user_agent"] = env("SEC_USER_AGENT") or cfg["sec"]["user_agent"]
    for m in ("india", "us"):
        cfg["watchlist"][m] = [str(s).upper().strip() for s in cfg["watchlist"].get(m) or []]
        cfg["investors"][m] = [str(s).strip() for s in cfg["investors"].get(m) or []]
    return cfg


def feed_cfg(cfg: dict, name: str) -> dict:
    return {"enabled": True, "every_min": 5, **(cfg["feeds"].get(name) or {})}


# ---------------------------------------------------------------- state


class State:
    """Remembers which items were already processed, per feed."""

    CAP = 6000

    def __init__(self, path: str):
        self.path = Path(path)
        self.data: dict[str, list[str]] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        self._sets = {k: set(v) for k, v in self.data.items()}

    def known_feed(self, feed: str) -> bool:
        return feed in self.data

    def seen(self, feed: str, item_id: str) -> bool:
        return item_id in self._sets.get(feed, set())

    def add(self, feed: str, item_id: str) -> None:
        lst = self.data.setdefault(feed, [])
        st = self._sets.setdefault(feed, set())
        if item_id not in st:
            lst.append(item_id)
            st.add(item_id)
            if len(lst) > self.CAP:
                drop = lst[: len(lst) - self.CAP]
                del lst[: len(lst) - self.CAP]
                st.difference_update(drop)

    def touch(self, feed: str) -> None:
        self.data.setdefault(feed, [])
        self._sets.setdefault(feed, set())

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        tmp.replace(self.path)


# ---------------------------------------------------------------- formatting


def fmt_inr(v: float | None) -> str:
    if v is None:
        return ""
    if v >= 1e7:
        return f"₹{v / 1e7:,.2f} cr"
    if v >= 1e5:
        return f"₹{v / 1e5:,.2f} lakh"
    return f"₹{v:,.0f}"


def fmt_usd(v: float | None) -> str:
    if v is None:
        return ""
    if v >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:,.1f}K"
    return f"${v:,.0f}"


def fmt_qty(q: float | None) -> str:
    return "" if q is None else f"{q:,.0f} sh"


REASON_ICON = {"watchlist": "⭐ Watchlist", "investor": "👤 Tracked investor", "large": "💰 Large trade"}


def format_event(e: Event) -> str:
    flag = "🇮🇳" if e.market == "india" else "🇺🇸"
    arrow = {"BUY": "🟢", "SELL": "🔴"}.get(e.action, "🔵")
    money = fmt_inr if e.market == "india" else fmt_usd
    esc = html.escape
    title = (e.symbol or (", ".join(sorted(e.all_symbols())[:6]) if e.symbols else "")
             or e.company or e.who or "-")
    lines = [f"{flag}{arrow} <b>{esc(e.kind)}</b> · <b>{esc(title)}</b>"]
    if e.company and e.company.upper() != (e.symbol or "").upper():
        lines.append(esc(e.company))
    if e.who:
        lines.append(f"👤 {esc(e.who)}" + (f" <i>({esc(e.role)})</i>" if e.role else ""))
    nums = " · ".join(x for x in (
        fmt_qty(e.qty),
        (money(e.price) + "/sh") if e.price else "",
        f"<b>{money(e.value)}</b>" if e.value else "",
    ) if x)
    if nums:
        lines.append(nums)
    for x in e.extra:
        lines.append(esc(x))
    dates = " · ".join(x for x in (
        f"Trade: {e.trade_date}" if e.trade_date else "",
        f"Filed: {e.filed}" if e.filed else "",
    ) if x)
    if dates:
        lines.append(dates)
    if e.reasons:
        lines.append(" ".join(REASON_ICON.get(r, r) for r in e.reasons))
    if e.url:
        lines.append(f'<a href="{esc(e.url, quote=True)}">View filing</a>')
    return "\n".join(lines)


# ---------------------------------------------------------------- notifier


class Notifier:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.token = cfg["telegram"]["bot_token"]
        self.chat = str(cfg["telegram"]["chat_id"])
        self.dry = dry_run or not (self.token and self.chat)
        if self.dry and not dry_run:
            print("[warn] Telegram not configured - printing alerts to the console instead.")

    def send_text(self, text: str) -> None:
        if self.dry:   # readable console version: drop HTML, keep link targets
            plain = re.sub(r'<a href="([^"]+)">[^<]*</a>', r"\1", text)
            plain = html.unescape(re.sub(r"</?(b|i)>", "", plain))
            print("\n" + "-" * 60 + "\n" + plain, flush=True)
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(4):
            try:
                r = requests.post(url, data={
                    "chat_id": self.chat, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": "true"}, timeout=20)
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 5)
                    time.sleep(wait + 1)
                    continue
                if r.ok:
                    return
                print(f"[telegram] {r.status_code}: {r.text[:200]}")
                return
            except requests.RequestException as ex:
                print(f"[telegram] {ex}")
                time.sleep(3 * (attempt + 1))

    def send_events(self, events: list[Event]) -> None:
        """One message each when few; a combined digest when many (avoids rate limits)."""
        if not events:
            return
        blocks = [format_event(e) for e in events]
        if len(blocks) <= 8:
            for b in blocks:
                self.send_text(b)
                time.sleep(0.4)
            return
        chunk = f"📣 <b>{len(blocks)} new big-money moves</b>\n\n"
        for b in blocks:
            if len(chunk) + len(b) + 2 > 3900:
                self.send_text(chunk)
                time.sleep(0.5)
                chunk = ""
            chunk += b + "\n\n"
        if chunk.strip():
            self.send_text(chunk)
