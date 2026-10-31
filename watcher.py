"""Big Shot Watch - Telegram alerts when insiders, big investors, funds or
Congress members buy/sell stocks (NSE India + SEC US + US House).

Usage:
  python watcher.py                      # run forever, all enabled feeds
  python watcher.py --feeds india        # only NSE feeds (run on your PC)
  python watcher.py --feeds us --duration 3500   # US feeds for ~58 min (GitHub Actions)
  python watcher.py --once --dry-run     # one pass, print instead of sending
  python watcher.py --test-telegram      # send a test message
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import Notifier, State, feed_cfg, load_config  # noqa: E402
from filters import match_reasons  # noqa: E402
from sources import congress, nse, sec  # noqa: E402

INDIA = ["nse_insider", "nse_deals", "nse_sast"]
US = ["sec_form4", "sec_13dg", "sec_13f", "house_ptr"]


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class Runner:
    def __init__(self, cfg: dict, state: State, notifier: Notifier, feeds: list[str]):
        self.cfg, self.state, self.notifier = cfg, state, notifier
        self.feeds = [f for f in feeds if feed_cfg(cfg, f)["enabled"]]
        self.next_due = {f: 0.0 for f in self.feeds}
        self.errors = {f: 0 for f in self.feeds}
        self._nse = None
        self._sec = None
        self.verbose = False

    @property
    def nse(self):
        if self._nse is None:
            self._nse = nse.NSEClient()
        return self._nse

    @property
    def sec(self):
        if self._sec is None:
            self._sec = sec.SECClient(self.cfg["sec"]["user_agent"])
        return self._sec

    def fetch(self, feed: str):
        c = self.cfg
        if feed == "nse_insider":
            seeding = not self.state.known_feed(feed) and c.get("seed_on_first_run", True)
            return nse.fetch_insider(self.nse, self.state, c.get("india_buy_sell_only", True),
                                     seeding=seeding)
        if feed == "nse_deals":
            return nse.fetch_deals(self.nse)
        if feed == "nse_sast":
            return nse.fetch_sast(self.nse)
        if feed == "sec_form4":
            if not self.state.known_feed("sec_form4") and c.get("seed_on_first_run", True):
                for acc in sec.fetch_current(self.sec, "4", pages=2):   # don't download old XMLs
                    self.state.add("sec_form4_acc", acc)
                return []
            codes = [x.upper() for x in feed_cfg(c, "sec_form4").get("codes", ["P", "S"])]
            return sec.fetch_form4(self.sec, self.state, codes)
        if feed == "sec_13dg":
            return sec.fetch_13dg(self.sec)
        if feed == "sec_13f":
            return sec.fetch_13f(self.sec, self.state, c["investors"]["us"])
        if feed == "house_ptr":
            seeding = not self.state.known_feed(feed) and c.get("seed_on_first_run", True)
            return congress.fetch_house(self.state, seeding,
                                        max_new=None if seeding or self.state.known_feed(feed) else 15)
        raise ValueError(feed)

    def run_feed(self, feed: str) -> int:
        events = self.fetch(feed)
        seeding = not self.state.known_feed(feed) and self.cfg.get("seed_on_first_run", True)
        self.state.touch(feed)
        to_send = []
        for e in events:
            if self.state.seen(feed, e.id):
                continue
            self.state.add(feed, e.id)
            if seeding:
                continue
            e.reasons = match_reasons(e, self.cfg)
            if e.reasons:
                to_send.append(e)
        if seeding:
            log(f"[{feed}] first run: marked {len(events)} existing items as seen (no alerts)")
        elif self.verbose:
            log(f"[{feed}] OK - fetched {len(events)} item(s), {len(to_send)} matched")
        self.notifier.send_events(to_send)
        return len(to_send)

    def cycle(self) -> None:
        now = time.time()
        for feed in self.feeds:
            if now < self.next_due[feed]:
                continue
            every = float(feed_cfg(self.cfg, feed)["every_min"]) * 60
            try:
                n = self.run_feed(feed)
                self.errors[feed] = 0
                if n:
                    log(f"[{feed}] sent {n} alert(s)")
                self.next_due[feed] = now + every
            except Exception as ex:
                self.errors[feed] += 1
                backoff = min(every * (2 ** self.errors[feed]), 3600)
                self.next_due[feed] = now + backoff
                log(f"[{feed}] error #{self.errors[feed]}: {ex} (retry in {backoff / 60:.0f} min)")
                if self.errors[feed] == 5:
                    self.notifier.send_text(f"⚠️ Big Shot Watch: feed <b>{feed}</b> keeps failing: "
                                            f"{str(ex)[:300]}")
            finally:
                self.state.save()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--state", default=str(HERE / "state.json"))
    ap.add_argument("--feeds", default="all", help="all | india | us | comma list of feed names")
    ap.add_argument("--once", action="store_true", help="one pass then exit")
    ap.add_argument("--duration", type=int, default=0, help="stop after N seconds")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--no-seed", action="store_true",
                    help="alert on matching recent items even on first run (for testing)")
    a = ap.parse_args()

    cfg = load_config(a.config)
    if a.no_seed:
        cfg["seed_on_first_run"] = False
    notifier = Notifier(cfg, dry_run=a.dry_run)
    if a.test_telegram:
        notifier.send_text("✅ Big Shot Watch is connected. You'll get alerts here.")
        log("test message sent")
        return

    feeds = {"all": INDIA + US, "india": INDIA, "us": US}.get(a.feeds) or \
        [f.strip() for f in a.feeds.split(",") if f.strip()]
    runner = Runner(cfg, State(a.state), notifier, feeds)
    runner.verbose = a.once or a.dry_run
    log(f"watching: {', '.join(runner.feeds)}")
    start = time.time()
    while True:
        try:
            runner.cycle()
        except Exception:
            traceback.print_exc()
        if a.once or (a.duration and time.time() - start > a.duration):
            break
        time.sleep(20)
    log("stopped")


if __name__ == "__main__":
    main()
