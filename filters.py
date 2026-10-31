"""Decides whether an event is worth a notification, and why."""
from __future__ import annotations

import re

from core import Event

STOP = {"of", "the", "and", "&", "ltd", "limited", "inc", "llc", "lp", "co"}


def match_reasons(e: Event, cfg: dict) -> list[str]:
    reasons: list[str] = []
    m = e.market
    watch = set(cfg["watchlist"].get(m) or [])
    if watch and e.all_symbols() & watch:
        reasons.append("watchlist")

    who = (e.who or "").lower()
    if who:
        words = set(re.findall(r"[a-z0-9]+", who))
        for name in cfg["investors"].get(m) or []:
            n = name.lower().strip()
            toks = [t for t in re.findall(r"[a-z0-9]+", n) if t not in STOP]
            # exact phrase, or every word of the name appears (handles middle names / initials)
            if n and (n in who or (toks and all(t in words for t in toks))):
                reasons.append("investor")
                break

    th = cfg["thresholds"]
    limit = th.get("india_inr") if m == "india" else th.get("us_usd")
    if e.value and limit and e.value >= float(limit) and e.action in ("BUY", "SELL"):
        reasons.append("large")
    if (e.feed == "sec_13dg" and th.get("us_initial_13d_anywhere")
            and e.kind == "NEW ACTIVIST STAKE (13D)" and "large" not in reasons):
        reasons.append("large")
    if e.feed == "house_ptr" and cfg["feeds"].get("house_ptr", {}).get("alert_all", True):
        if not reasons:
            reasons.append("Congress trade")
    return reasons
