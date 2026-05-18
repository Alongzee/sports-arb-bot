"""
ocr_collector.py — Bet105 Visual Odds Recorder
==============================================
Bet105 drives discovery. SportyBet reacts.

Run on your PC while browsing Bet105.
Extracts visible odds → sends to VPS → VPS queries SportyBet → arb alert.

Install:
  pip install mss easyocr opencv-python pillow requests numpy

Usage:
  python ocr_collector.py --select
  python ocr_collector.py --region 0,100,450,800
"""

import cv2
import time
import re
import sqlite3
import threading
import logging
import requests
import numpy as np

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List, Tuple
from pathlib import Path


# ── CONFIG ────────────────────────────────────────────────────────────────

VPS_URL          = "http://45.151.152.197:5555/odds"
DB_PATH          = "bet105_odds.db"

CAPTURE_INTERVAL = 3
LOG_PATH         = "ocr.log"

TTL_PREMATCH     = 60
TTL_LIVE         = 8

MIN_CONFIDENCE   = 0.60
MIN_ODDS         = 1.05
MAX_ODDS         = 30.0

BROWSER_ZOOM     = 100
MONITOR_SCALE    = 1.0


# ── LOGGING ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ]
)

log = logging.getLogger("ocr")


# ── DATA MODEL ────────────────────────────────────────────────────────────

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

    def age(self) -> float:
        return round(time.time() - self.unix_ts, 1)

    def ttl(self) -> float:
        return TTL_LIVE if self.is_live else TTL_PREMATCH

    def is_fresh(self) -> bool:
        return self.age() < self.ttl()


# ── DATABASE ──────────────────────────────────────────────────────────────

class OddsDatabase:

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._init()

    def _conn(self):
        return sqlite3.connect(self.path)

    def _init(self):

        c = self._conn()

        c.execute("""
            CREATE TABLE IF NOT EXISTS latest_odds (
                bookmaker  TEXT NOT NULL DEFAULT 'bet105',
                match      TEXT NOT NULL,
                market     TEXT NOT NULL,
                selection  TEXT NOT NULL,
                odds       REAL NOT NULL,
                timestamp  TEXT NOT NULL,
                unix_ts    REAL NOT NULL,
                is_live    INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0.0,
                PRIMARY KEY (
                    bookmaker,
                    match,
                    market,
                    selection
                )
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS odds_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                bookmaker  TEXT    NOT NULL DEFAULT 'bet105',
                match      TEXT    NOT NULL,
                market     TEXT    NOT NULL,
                selection  TEXT    NOT NULL,
                odds       REAL    NOT NULL,
                prev_odds  REAL,
                timestamp  TEXT    NOT NULL,
                unix_ts    REAL    NOT NULL,
                is_live    INTEGER DEFAULT 0,
                confidence REAL    DEFAULT 0.0,
                raw_text   TEXT    DEFAULT ''
            )
        """)

        c.commit()
        c.close()

        log.info(f"DB initialized: {self.path}")

    def upsert(self, e: OddsEntry):

        c = self._conn()

        try:

            row = c.execute("""
                SELECT odds
                FROM latest_odds
                WHERE bookmaker=?
                AND match=?
                AND market=?
                AND selection=?
            """, (
                e.bookmaker,
                e.match,
                e.market,
                e.selection,
            )).fetchone()

            prev_odds = row[0] if row else None

            changed = (
                prev_odds is None
                or abs(prev_odds - e.odds) > 0.001
            )

            c.execute("""
                INSERT INTO latest_odds (
                    bookmaker,
                    match,
                    market,
                    selection,
                    odds,
                    timestamp,
                    unix_ts,
                    is_live,
                    confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT (
                    bookmaker,
                    match,
                    market,
                    selection
                )

                DO UPDATE SET
                    odds=excluded.odds,
                    timestamp=excluded.timestamp,
                    unix_ts=excluded.unix_ts,
                    is_live=excluded.is_live,
                    confidence=excluded.confidence
            """, (
                e.bookmaker,
                e.match,
                e.market,
                e.selection,
                e.odds,
                e.timestamp,
                e.unix_ts,
                int(e.is_live),
                e.confidence,
            ))

            if changed:

                c.execute("""
                    INSERT INTO odds_history (
                        bookmaker,
                        match,
                        market,
                        selection,
                        odds,
                        prev_odds,
                        timestamp,
                        unix_ts,
                        is_live,
                        confidence,
                        raw_text
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    e.bookmaker,
                    e.match,
                    e.market,
                    e.selection,
                    e.odds,
                    prev_odds,
                    e.timestamp,
                    e.unix_ts,
                    int(e.is_live),
                    e.confidence,
                    e.raw_text,
                ))

            c.commit()

        except Exception as ex:
            log.error(f"DB error: {ex}")

        finally:
            c.close()

    def save_many(self, entries: List[OddsEntry]):

        for e in entries:
            self.upsert(e)


# ── SCREEN CAPTURE ────────────────────────────────────────────────────────

class ScreenCapture:

    def __init__(self, region=None):
        self.region = region

    def select_region(self):

        try:
            import mss

            with mss.mss() as sct:

                img = np.array(
                    sct.grab(sct.monitors[1])
                )

                full = cv2.cvtColor(
                    img,
                    cv2.COLOR_BGRA2BGR
                )

            region = [0, 0, 0, 0]
            drawing = [False]
            start = [0, 0]

            def cb(event, x, y, flags, param):

                if event == cv2.EVENT_LBUTTONDOWN:
                    drawing[0] = True
                    start[0], start[1] = x, y

                elif event == cv2.EVENT_MOUSEMOVE and drawing[0]:

                    tmp = full.copy()

                    cv2.rectangle(
                        tmp,
                        tuple(start),
                        (x, y),
                        (0, 255, 0),
                        2
                    )

                    cv2.imshow(
                        "Draw Bet105 region",
                        tmp
                    )

                elif event == cv2.EVENT_LBUTTONUP:

                    drawing[0] = False

                    region[0] = min(start[0], x)
                    region[1] = min(start[1], y)
                    region[2] = abs(x - start[0])
                    region[3] = abs(y - start[1])

            cv2.namedWindow(
                "Draw Bet105 region",
                cv2.WINDOW_NORMAL
            )

            cv2.setMouseCallback(
                "Draw Bet105 region",
                cb
            )

            cv2.imshow(
                "Draw Bet105 region",
                full
            )

            cv2.waitKey(0)

            cv2.destroyAllWindows()

            if region[2] > 50 and region[3] > 50:

                self.region = tuple(region)

                log.info(
                    f"Region selected: {self.region}"
                )

            else:
                log.warning("Region too small")

        except Exception as e:
            log.error(f"Region select error: {e}")

    def capture(self) -> Optional[np.ndarray]:

        try:
            import mss

            with mss.mss() as sct:

                if self.region:

                    x, y, w, h = self.region

                    mon = {
                        "top": y,
                        "left": x,
                        "width": w,
                        "height": h,
                    }

                else:
                    mon = sct.monitors[1]

                img = np.array(
                    sct.grab(mon)
                )

                return cv2.cvtColor(
                    img,
                    cv2.COLOR_BGRA2BGR
                )

        except Exception as e:
            log.error(f"Capture error: {e}")
            return None


# ── TEMPLATE MATCHING ─────────────────────────────────────────────────────

class TemplateMatcher:

    def __init__(
        self,
        templates_dir: str = "templates"
    ):

        self.templates_dir = Path(templates_dir)
        self.templates = {}

        self._load_templates()

    def _load_templates(self):

        if not self.templates_dir.exists():

            self.templates_dir.mkdir()

            log.info(
                f"Created templates dir: "
                f"{self.templates_dir}"
            )

            return

        for f in self.templates_dir.glob("*.png"):

            tmpl = cv2.imread(
                str(f),
                cv2.IMREAD_GRAYSCALE
            )

            if tmpl is not None:

                self.templates[f.stem] = tmpl

                log.info(
                    f"Loaded template: {f.stem}"
                )

    def find_odds_regions(
        self,
        img: np.ndarray,
        threshold: float = 0.75,
    ) -> List[Tuple[int, int, int, int]]:

        if not self.templates:
            return []

        gray = cv2.cvtColor(
            img,
            cv2.COLOR_BGR2GRAY
        )

        found = []

        for _, tmpl in self.templates.items():

            h, w = tmpl.shape

            result = cv2.matchTemplate(
                gray,
                tmpl,
                cv2.TM_CCOEFF_NORMED
            )

            locations = np.where(
                result >= threshold
            )

            for pt in zip(*locations[::-1]):

                x, y = pt

                is_dup = any(
                    abs(x - fx) < w // 2
                    and abs(y - fy) < h // 2
                    for fx, fy, _, _ in found
                )

                if not is_dup:
                    found.append((x, y, w, h))

        return found

    def detect_suspended(
        self,
        img: np.ndarray,
        region: Tuple
    ) -> bool:

        x, y, w, h = region

        crop = img[y:y+h, x:x+w]

        gray = cv2.cvtColor(
            crop,
            cv2.COLOR_BGR2GRAY
        )

        mean_brightness = np.mean(gray)

        return mean_brightness < 120

    def detect_changed(
        self,
        prev_frame: Optional[np.ndarray],
        curr_frame: np.ndarray,
        threshold: float = 0.02
    ) -> bool:

        if prev_frame is None:
            return True

        if prev_frame.shape != curr_frame.shape:
            return True

        diff = cv2.absdiff(
            prev_frame,
            curr_frame
        )

        change_ratio = np.mean(diff) / 255.0

        return change_ratio > threshold


# ── OCR LAYER ─────────────────────────────────────────────────────────────

class OCRLayer:

    def __init__(self, engine="easyocr"):

        self.engine = engine
        self._reader = None

    def _init_reader(self):

        if self._reader is None:

            import easyocr

            self._reader = easyocr.Reader(
                ['en'],
                gpu=False
            )

            log.info("EasyOCR initialized")

        return self._reader

    def extract(
        self,
        img: np.ndarray
    ) -> List[Tuple[str, float]]:

        if self.engine == "easyocr":
            return self._easyocr(img)

        return self._tesseract(img)

    def extract_crops(
        self,
        img: np.ndarray,
        regions: List[Tuple]
    ) -> List[Tuple[str, float]]:

        results = []

        for region in regions:

            x, y, w, h = region

            crop = img[y:y+h, x:x+w]

            results.extend(
                self.extract(crop)
            )

        return results

    def _easyocr(
        self,
        img: np.ndarray
    ) -> List[Tuple[str, float]]:

        try:

            res = self._init_reader().readtext(img)

            return [
                (t.strip(), c)
                for (_, t, c) in res
                if t.strip()
            ]

        except Exception as e:

            log.error(f"EasyOCR error: {e}")

            return []

    def _tesseract(
        self,
        img: np.ndarray
    ) -> List[Tuple[str, float]]:

        try:

            import pytesseract

            gray = cv2.cvtColor(
                img,
                cv2.COLOR_BGR2GRAY
            )

            scaled = cv2.resize(
                gray,
                None,
                fx=2,
                fy=2
            )

            _, thresh = cv2.threshold(
                scaled,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

            text = pytesseract.image_to_string(
                thresh,
                config='--psm 6'
            )

            return [
                (l.strip(), 0.75)
                for l in text.split('\n')
                if l.strip()
            ]

        except Exception as e:

            log.error(f"Tesseract error: {e}")

            return []


# ── CLEANER / PARSER ──────────────────────────────────────────────────────

class OddsParser:

    MARKET_KEYWORDS = {

        "over_under_0.5": [
            "over 0.5",
            "under 0.5",
            "o0.5",
            "u0.5",
        ],

        "over_under_1.5": [
            "over 1.5",
            "under 1.5",
            "o1.5",
            "u1.5",
        ],

        "over_under_2.5": [
            "over 2.5",
            "under 2.5",
            "o2.5",
            "u2.5",
            "total 2.5",
        ],

        "over_under_3.5": [
            "over 3.5",
            "under 3.5",
            "o3.5",
            "u3.5",
        ],

        "over_under_4.5": [
            "over 4.5",
            "under 4.5",
            "o4.5",
            "u4.5",
        ],

        "winner": [
            "1x2",
            "match result",
            "moneyline",
            "match winner",
        ],

        "both_to_score": [
            "btts",
            "both teams to score",
            "gg/ng",
            "bts",
        ],
    }

    SELECTION_MAP = {

        "over": "over",
        "o": "over",

        "under": "under",
        "u": "under",

        "home": "home",
        "1": "home",

        "away": "away",
        "2": "away",

        "draw": "draw",
        "x": "draw",

        "yes": "yes",
        "no": "no",
    }

    def parse(
        self,
        raw_lines,
        match,
        is_live
    ) -> List[OddsEntry]:

        now = datetime.now(timezone.utc)

        ts = now.isoformat()
        unix_now = now.timestamp()

        texts = [t for t, _ in raw_lines]
        confs = [c for _, c in raw_lines]

        entries = []

        current_market = "unknown"

        i = 0

        while i < len(texts):

            line = texts[i]

            conf = (
                confs[i]
                if i < len(confs)
                else 0.5
            )

            if len(line) < 2:
                i += 1
                continue

            mk = self._detect_market(line)

            if mk:
                current_market = mk

            odds_val, selection, parse_conf = (
                self._parse_odds(
                    line,
                    texts,
                    i
                )
            )

            if (
                odds_val is not None
                and MIN_ODDS <= odds_val <= MAX_ODDS
            ):

                combined = round(
                    min(conf, parse_conf),
                    3
                )

                if combined >= MIN_CONFIDENCE:

                    entries.append(
                        OddsEntry(
                            bookmaker="bet105",
                            match=match,
                            market=current_market,
                            selection=self._normalize(selection),
                            odds=odds_val,
                            timestamp=ts,
                            unix_ts=unix_now,
                            is_live=is_live,
                            confidence=combined,
                            raw_text=line,
                        )
                    )

            i += 1

        return self._deduplicate(entries)

    def _detect_market(
        self,
        line: str
    ) -> Optional[str]:

        ll = line.lower()

        for mkt, kws in self.MARKET_KEYWORDS.items():

            if any(kw in ll for kw in kws):
                return mkt

        return None

    def _parse_odds(
        self,
        line,
        all_lines,
        idx
    ):

        m = re.search(
            r'\b(\d+\.\d{2})\b',
            line
        )

        if m:

            val = float(m.group(1))

            sel = (
                line[:m.start()].strip()
                or (
                    all_lines[idx - 1].strip()
                    if idx > 0 else ""
                )
            )

            return val, sel, 0.92

        m = re.search(
            r'([+-]\d{3,4})\b',
            line
        )

        if m:

            am = int(m.group(1))

            val = (
                round(am / 100 + 1, 3)
                if am > 0
                else round(
                    100 / abs(am) + 1,
                    3
                )
            )

            sel = (
                line[:m.start()].strip()
                or (
                    all_lines[idx - 1].strip()
                    if idx > 0 else ""
                )
            )

            return val, sel, 0.82

        return None, None, 0.0

    def _normalize(self, s: str) -> str:

        sl = s.lower().strip()

        return self.SELECTION_MAP.get(
            sl,
            s.strip() or "unknown"
        )

    def _deduplicate(
        self,
        entries: List[OddsEntry]
    ) -> List[OddsEntry]:

        seen = {}

        for e in entries:

            k = (
                f"{e.match}|"
                f"{e.market}|"
                f"{e.selection}"
            )

            if (
                k not in seen
                or e.confidence > seen[k].confidence
            ):
                seen[k] = e

        return list(seen.values())


# ── VPS SENDER ────────────────────────────────────────────────────────────

class VPSSender:

    def send(self, entries: List[OddsEntry]):

        if not entries or not VPS_URL:
            return

        payload_by_match = {}

        for e in entries:

            if e.match not in payload_by_match:

                payload_by_match[e.match] = {
                    "bookmaker": "bet105",
                    "match": e.match,
                    "is_live": e.is_live,
                    "timestamp": e.timestamp,
                    "source": "ocr",
                    "odds": {},
                }

            if (
                e.market
                not in payload_by_match[e.match]["odds"]
            ):
                payload_by_match[e.match]["odds"][e.market] = {}

            payload_by_match[e.match]["odds"][e.market][e.selection] = {
                "odds": e.odds,
                "confidence": e.confidence,
                "age": e.age(),
            }

        for match_name, payload in payload_by_match.items():

            try:

                r = requests.post(
                    VPS_URL,
                    json=payload,
                    timeout=3
                )

                log.info(
                    f"VPS: {match_name} → {r.status_code}"
                )

            except Exception as ex:

                log.warning(
                    f"VPS send failed: {ex}"
                )


# ── MAIN COLLECTOR ────────────────────────────────────────────────────────

class OddsCollector:

    def __init__(
        self,
        region=None,
        engine="easyocr",
        match="",
        is_live=False,
        use_templates=True
    ):

        self.capture = ScreenCapture(region)

        self.templates = TemplateMatcher()

        self.ocr = OCRLayer(engine)

        self.parser = OddsParser()

        self.db = OddsDatabase()

        self.sender = VPSSender()

        self.match = match

        self.is_live = is_live

        self.use_templates = use_templates

        self._prev_frame: Optional[np.ndarray] = None

        self.paused = False

    def pause(self):

        self.paused = True

        log.info("Collector paused")

    def resume(self):

        self.paused = False

        log.info("Collector resumed")

    def toggle_pause(self):

        self.paused = not self.paused

        if self.paused:
            log.info("Collector paused")
        else:
            log.info("Collector resumed")

    def run(self):

        log.info("=" * 50)
        log.info("Bet105 OCR Collector started")
        log.info(f"Match: {self.match or '(auto-detect)'}")
        log.info(f"Live: {self.is_live}")
        log.info(f"Interval: {CAPTURE_INTERVAL}s")

        log.info(
            f"Templates: "
            f"{'yes' if self.use_templates and self.templates.templates else 'no'}"
        )

        log.info("Press Ctrl+C to stop")

        log.info("=" * 50)

        try:

            while True:

                self._cycle()

                time.sleep(CAPTURE_INTERVAL)

        except KeyboardInterrupt:

            log.info("Stopped.")

    def _cycle(self):

        if self.paused:
            return

        img = self.capture.capture()

        if img is None:
            return

        if not self.templates.detect_changed(
            self._prev_frame,
            img
        ):

            log.debug(
                "No screen change — skipping OCR"
            )

            self._prev_frame = img.copy()

            return

        self._prev_frame = img.copy()

        if (
            self.use_templates
            and self.templates.templates
        ):

            regions = self.templates.find_odds_regions(img)

            if regions:

                active_regions = [

                    r for r in regions

                    if not self.templates.detect_suspended(
                        img,
                        r
                    )
                ]

                log.debug(
                    f"Template: "
                    f"{len(regions)} found, "
                    f"{len(active_regions)} active"
                )

                raw_lines = self.ocr.extract_crops(
                    img,
                    active_regions
                )

            else:

                log.debug(
                    "No odds regions detected"
                )

                return

        else:

            raw_lines = self.ocr.extract(img)

        if not raw_lines:

            log.debug("No text detected")

            return

        entries = self.parser.parse(
            raw_lines,
            self.match,
            self.is_live
        )

        if not entries:

            log.debug(
                f"No valid odds from "
                f"{len(raw_lines)} OCR lines"
            )

            return

        self.db.save_many(entries)

        log.info(
            f"Observed {len(entries)} markets "
            f"| '{self.match}' "
            f"| {'LIVE' if self.is_live else 'prematch'}"
        )

        for e in entries:

            log.info(
                f"{e.market:20} | "
                f"{e.selection:6} | "
                f"{e.odds:.2f} | "
                f"conf={e.confidence:.2f} | "
                f"age={e.age()}s"
            )

        threading.Thread(
            target=self.sender.send,
            args=(entries,),
            daemon=True
        ).start()


# ── ENTRY POINT ───────────────────────────────────────────────────────────

if __name__ == "__main__":

    import argparse

    ap = argparse.ArgumentParser(
        description=(
            "Bet105 OCR Odds Collector"
        )
    )

    ap.add_argument(
        "--match",
        default="",
        help="Match name"
    )

    ap.add_argument(
        "--live",
        action="store_true",
        help="Live odds mode"
    )

    ap.add_argument(
        "--region",
        default=None,
        help="x,y,w,h"
    )

    ap.add_argument(
        "--select",
        action="store_true",
        help="Interactively select region"
    )

    ap.add_argument(
        "--tesseract",
        action="store_true",
        help="Use tesseract OCR"
    )

    ap.add_argument(
        "--no-templates",
        action="store_true",
        help="Disable template matching"
    )

    args = ap.parse_args()

    region = (
        tuple(map(int, args.region.split(",")))
        if args.region
        else None
    )

    engine = (
        "tesseract"
        if args.tesseract
        else "easyocr"
    )

    collector = OddsCollector(
        region=region,
        engine=engine,
        match=args.match,
        is_live=args.live,
        use_templates=not args.no_templates,
    )

    if args.select:
        collector.capture.select_region()

    collector.run()
