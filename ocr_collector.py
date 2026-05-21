# ──────────────────────────────────────────────────────────────────────────
# ocr_collector.py
# Soccabet Visual Odds Recorder
# Group 2 Complete Rebuild – Modified for Soccabet
# ──────────────────────────────────────────────────────────────────────────

import cv2
import re
import time
import sqlite3
import logging
import threading
import requests
import numpy as np

from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List, Tuple

# ──────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────

VPS_URL = "http://45.151.152.197:5555/odds"

DB_PATH = "soccabet_odds.db"          # ← changed from bet105_odds.db
LOG_PATH = "ocr.log"

CAPTURE_INTERVAL = 3

TTL_PREMATCH = 60
TTL_LIVE = 8

MIN_CONFIDENCE = 0.60

MIN_ODDS = 1.01
MAX_ODDS = 100.0

# ──────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("ocr")

# ──────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class OddsEntry:
    bookmaker: str
    match: str
    market: str
    selection: str
    odds: float
    timestamp: str
    unix_ts: float
    is_live: bool
    confidence: float
    raw_text: str = ""

    def age(self):
        return round(time.time() - self.unix_ts, 1)

    def ttl(self):
        return TTL_LIVE if self.is_live else TTL_PREMATCH

    def is_fresh(self):
        return self.age() < self.ttl()

# ──────────────────────────────────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────────────────────────────────

class OddsDatabase:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._init()

    def _conn(self):
        return sqlite3.connect(self.path)

    def _init(self):
        c = self._conn()
        c.execute("""
        CREATE TABLE IF NOT EXISTS latest_odds (
            bookmaker TEXT,
            match TEXT,
            market TEXT,
            selection TEXT,
            odds REAL,
            timestamp TEXT,
            unix_ts REAL,
            is_live INTEGER,
            confidence REAL,
            PRIMARY KEY (bookmaker, match, market, selection)
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS odds_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bookmaker TEXT,
            match TEXT,
            market TEXT,
            selection TEXT,
            odds REAL,
            prev_odds REAL,
            timestamp TEXT,
            unix_ts REAL,
            is_live INTEGER,
            confidence REAL,
            raw_text TEXT
        )
        """)
        c.commit()
        c.close()
        log.info(f"DB initialized: {self.path}")

    def upsert(self, e: OddsEntry):
        c = self._conn()
        try:
            row = c.execute("""
                SELECT odds FROM latest_odds
                WHERE bookmaker=? AND match=? AND market=? AND selection=?
            """, (e.bookmaker, e.match, e.market, e.selection)).fetchone()
            prev_odds = row[0] if row else None
            changed = (prev_odds is None or abs(prev_odds - e.odds) > 0.001)

            c.execute("""
                INSERT INTO latest_odds (bookmaker, match, market, selection, odds, timestamp, unix_ts, is_live, confidence)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(bookmaker, match, market, selection) DO UPDATE SET
                    odds=excluded.odds,
                    timestamp=excluded.timestamp,
                    unix_ts=excluded.unix_ts,
                    is_live=excluded.is_live,
                    confidence=excluded.confidence
            """, (e.bookmaker, e.match, e.market, e.selection, e.odds, e.timestamp, e.unix_ts, int(e.is_live), e.confidence))

            if changed:
                c.execute("""
                    INSERT INTO odds_history (bookmaker, match, market, selection, odds, prev_odds, timestamp, unix_ts, is_live, confidence, raw_text)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (e.bookmaker, e.match, e.market, e.selection, e.odds, prev_odds, e.timestamp, e.unix_ts, int(e.is_live), e.confidence, e.raw_text))
            c.commit()
        except Exception as ex:
            log.error(f"DB error: {ex}")
        finally:
            c.close()

    def save_many(self, entries):
        for e in entries:
            self.upsert(e)

# ──────────────────────────────────────────────────────────────────────────
# SCREEN CAPTURE
# ──────────────────────────────────────────────────────────────────────────

class ScreenCapture:
    def __init__(self, region=None):
        self.region = region

    def select_region(self):
        import mss
        with mss.mss() as sct:
            img = np.array(sct.grab(sct.monitors[1]))
            full = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        region = [0,0,0,0]
        drawing = [False]
        start = [0,0]
        def cb(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                drawing[0] = True
                start[0], start[1] = x, y
            elif event == cv2.EVENT_MOUSEMOVE and drawing[0]:
                tmp = full.copy()
                cv2.rectangle(tmp, tuple(start), (x,y), (0,255,0), 2)
                cv2.imshow("Select Region", tmp)
            elif event == cv2.EVENT_LBUTTONUP:
                drawing[0] = False
                region[0] = min(start[0], x)
                region[1] = min(start[1], y)
                region[2] = abs(x - start[0])
                region[3] = abs(y - start[1])
        cv2.namedWindow("Select Region")
        cv2.setMouseCallback("Select Region", cb)
        cv2.imshow("Select Region", full)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        self.region = tuple(region)
        log.info(f"Selected region: {self.region}")

    def capture(self):
        try:
            import mss
            with mss.mss() as sct:
                if self.region:
                    x,y,w,h = self.region
                    mon = {"top": y, "left": x, "width": w, "height": h}
                else:
                    mon = sct.monitors[1]
                img = np.array(sct.grab(mon))
                return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        except Exception as ex:
            log.error(f"Capture error: {ex}")
            return None

# ──────────────────────────────────────────────────────────────────────────
# OCR
# ──────────────────────────────────────────────────────────────────────────

class OCRLayer:
    def __init__(self):
        self.reader = None

    def _init(self):
        if self.reader is None:
            import easyocr
            self.reader = easyocr.Reader(['en'], gpu=False)
            log.info("EasyOCR initialized")

    def extract(self, img):
        try:
            self._init()
            result = self.reader.readtext(img)
            return [(t.strip(), c) for (_, t, c) in result if t.strip()]
        except Exception as ex:
            log.error(f"OCR error: {ex}")
            return []

# ──────────────────────────────────────────────────────────────────────────
# PARSER
# ──────────────────────────────────────────────────────────────────────────

class OddsParser:
    MONEYLINE_KEYWORDS = ["1x2", "winner", "moneyline", "match result", "match winner"]
    SPREAD_KEYWORDS = ["spread", "handicap", "asian handicap", "ah"]
    BTTS_KEYWORDS = ["btts", "both teams to score", "gg/ng", "bts"]

    SELECTION_MAP = {
        "1": "home", "2": "away", "x": "draw",
        "home": "home", "away": "away", "draw": "draw",
        "over": "over", "under": "under",
        "o": "over", "u": "under",
        "yes": "yes", "no": "no"
    }

    def parse(self, raw_lines, match, is_live):
        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        unix_now = now.timestamp()
        texts = [t for t,_ in raw_lines]
        confs = [c for _,c in raw_lines]
        entries = []
        current_market = "unknown"

        for idx, line in enumerate(texts):
            conf = confs[idx]
            clean = line.strip()
            if len(clean) < 2:
                continue
            market = self._detect_market(clean)
            if market:
                current_market = market
            odds_val, selection, parse_conf = self._parse_odds(clean, texts, idx)
            if odds_val is None:
                continue
            if not (MIN_ODDS <= odds_val <= MAX_ODDS):
                continue
            combined_conf = min(conf, parse_conf)
            if combined_conf < MIN_CONFIDENCE:
                continue
            entries.append(
                OddsEntry(
                    bookmaker="soccabet",          # ← changed from "bet105"
                    match=match,
                    market=current_market,
                    selection=self._normalize_selection(selection),
                    odds=odds_val,
                    timestamp=ts,
                    unix_ts=unix_now,
                    is_live=is_live,
                    confidence=combined_conf,
                    raw_text=clean
                )
            )
        return self._deduplicate(entries)

    def _detect_market(self, line):
        ll = line.lower()
        total_match = re.search(r"(over|under|o|u)\s*([0-9]+(?:\.[0-9]+)?)", ll)
        if total_match:
            value = total_match.group(2)
            return f"total_{value}"
        if any(k in ll for k in self.SPREAD_KEYWORDS):
            spread_match = re.search(r"([+-]?[0-9]+(?:\.[0-9]+)?)", ll)
            if spread_match:
                handicap = spread_match.group(1)
                return f"spread_{handicap}"
            return "spread"
        if any(k in ll for k in self.BTTS_KEYWORDS):
            return "btts"
        if any(k in ll for k in self.MONEYLINE_KEYWORDS):
            return "moneyline"
        return None

    def _parse_odds(self, line, all_lines, idx):
        dec = re.search(r"\b([1-9][0-9]?\.[0-9]{1,3})\b", line)
        if dec:
            odds = float(dec.group(1))
            selection = line[:dec.start()].strip() or (all_lines[idx-1].strip() if idx>0 else "")
            return odds, selection, 0.92
        american = re.search(r"([+-]\d{3,4})", line)
        if american:
            am = int(american.group(1))
            if am > 0:
                odds = round((am / 100) + 1, 3)
            else:
                odds = round((100 / abs(am)) + 1, 3)
            selection = line[:american.start()].strip() or (all_lines[idx-1].strip() if idx>0 else "")
            return odds, selection, 0.82
        return None, None, 0.0

    def _normalize_selection(self, selection):
        s = selection.lower().strip()
        return self.SELECTION_MAP.get(s, selection.strip() or "unknown")

    def _deduplicate(self, entries):
        best = {}
        for e in entries:
            key = f"{e.match}|{e.market}|{e.selection}"
            if key not in best or e.confidence > best[key].confidence:
                best[key] = e
        return list(best.values())

# ──────────────────────────────────────────────────────────────────────────
# VPS SENDER
# ──────────────────────────────────────────────────────────────────────────

class VPSSender:
    def send(self, entries):
        if not entries:
            return
        payload_by_match = {}
        for e in entries:
            if e.match not in payload_by_match:
                payload_by_match[e.match] = {
                    "bookmaker": "soccabet",          # ← changed to soccabet
                    "match": e.match,
                    "is_live": e.is_live,
                    "timestamp": e.timestamp,
                    "source": "ocr",
                    "odds": {}
                }
            if e.market not in payload_by_match[e.match]["odds"]:
                payload_by_match[e.match]["odds"][e.market] = {}
            payload_by_match[e.match]["odds"][e.market][e.selection] = {
                "odds": e.odds,
                "confidence": e.confidence,
                "age": e.age()
            }
        for match_name, payload in payload_by_match.items():
            try:
                r = requests.post(VPS_URL, json=payload, timeout=5)
                log.info(f"VPS: {match_name} → {r.status_code}")
            except Exception as ex:
                log.warning(f"VPS send failed: {ex}")

# ──────────────────────────────────────────────────────────────────────────
# COLLECTOR
# ──────────────────────────────────────────────────────────────────────────

class OddsCollector:
    def __init__(self, region=None, match="", is_live=False):
        self.capture = ScreenCapture(region)
        self.ocr = OCRLayer()
        self.parser = OddsParser()
        self.db = OddsDatabase()
        self.sender = VPSSender()
        self.match = match
        self.is_live = is_live

    def run(self):
        log.info("="*60)
        log.info("Soccabet OCR Collector Started")     # changed message
        log.info("="*60)
        while True:
            self._cycle()
            time.sleep(CAPTURE_INTERVAL)

    def _cycle(self):
        img = self.capture.capture()
        if img is None:
            return
        raw_lines = self.ocr.extract(img)
        if not raw_lines:
            return
        entries = self.parser.parse(raw_lines, self.match, self.is_live)
        if not entries:
            return
        self.db.save_many(entries)
        log.info(f"Observed {len(entries)} markets")
        for e in entries:
            log.info(f"{e.market:20} | {e.selection:10} | {e.odds:.2f} | conf={e.confidence:.2f}")
        threading.Thread(target=self.sender.send, args=(entries,), daemon=True).start()

# ──────────────────────────────────────────────────────────────────────────
# ENTRY
# ──────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", default="")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--region", default=None)
    ap.add_argument("--select", action="store_true")
    args = ap.parse_args()

    region = None
    if args.region:
        region = tuple(map(int, args.region.split(",")))

    collector = OddsCollector(region=region, match=args.match, is_live=args.live)
    if args.select:
        collector.capture.select_region()
    collector.run()
