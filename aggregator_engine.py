"""
aggregator_engine.py – Engine 2: OddsPortal aggregator scanner.

Runs in parallel with Engine 1 (direct SportyBet API).
Scrapes OddsPortal for free via OddsHarvester, finds arb opportunities
across any bookmakers, and sends Telegram alerts tagged [AGG].

Key differences from Engine 1:
  - Source: OddsPortal (multi-bookmaker) instead of SportyBet direct
  - Min profit: 3% (higher bar since aggregator data has some delay)
  - Alert tag: [AGG] to distinguish from Engine 1 alerts
  - Poll interval: every 10 minutes (OddsPortal is slow to scrape)
  - Sport rotation: scans one sport per cycle to avoid bans
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from alerter import is_active_hours, is_cool_off_day
from balancer import Balancer
from matcher import normalise_market

log = logging.getLogger("aggregator_engine")

# ── Config ─────────────────────────────────────────────────────────────────

AGG_POLL_INTERVAL = 600        # seconds between full scans
AGG_MIN_PROFIT    = 3.0        # minimum profit % to alert
AGG_MAX_ALERTS    = 5          # max alerts per scan cycle

# Rotate sports each cycle — football is highest volume
SPORT_ROTATION = ["football", "basketball", "tennis"]
AGG_MARKETS    = ["1x2", "over_under"]


# ── OddsHarvester subprocess runner ────────────────────────────────────────

async def run_oddsharvester(sport: str, market: str, out_base: str) -> bool:
    """
    Runs oddsharvester CLI as a subprocess for today's upcoming matches.
    Returns True if output file was written.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    cmd = [
        "oddsharvester", "upcoming",
        "-s", sport,
        "-m", market,
        "-d", today,
        "-f", "json",
        "-o", out_base,
        "--headless",
        "--request-delay", "2.0",
        "--concurrency", "2",
    ]
    log.info(f"[AGG] Scraping OddsPortal: sport={sport} market={market}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            log.warning(f"[AGG] oddsharvester exited {proc.returncode}: {stderr.decode()[:300]}")
            return False
        # OddsHarvester appends .json automatically
        return os.path.exists(out_base + ".json")
    except asyncio.TimeoutError:
        log.warning(f"[AGG] oddsharvester timed out ({sport}/{market})")
        try:
            proc.kill()
        except Exception:
            pass
        return False
    except FileNotFoundError:
        log.error("[AGG] 'oddsharvester' command not found — run: pip install oddsharvester")
        return False
    except Exception as e:
        log.error(f"[AGG] subprocess error: {e}")
        return False


# ── Parser ─────────────────────────────────────────────────────────────────

def parse_output(filepath: str) -> list[dict]:
    """
    Parse OddsHarvester JSON output and return a list of confirmed arb
    candidates sorted by profit % descending.

    OddsHarvester output (per match):
    {
      "home_team": "...", "away_team": "...",
      "commence_time": "2026-05-13T18:00:00Z",
      "sport": "football",
      "odds": {
        "1x2": {
          "Bet365":    {"home": 2.10, "draw": 3.40, "away": 3.20},
          "Pinnacle":  {"home": 2.18, "draw": 3.35, "away": 3.10},
          ...
        }
      }
    }
    """
    try:
        with open(filepath) as f:
            raw = json.load(f)
    except Exception as e:
        log.error(f"[AGG] Cannot read {filepath}: {e}")
        return []

    matches = raw if isinstance(raw, list) else list(raw.values())
    candidates = []

    for match in matches:
        if not isinstance(match, dict):
            continue

        home  = match.get("home_team", match.get("home", "?"))
        away  = match.get("away_team", match.get("away", "?"))
        sport = match.get("sport", "football")
        commence = match.get("commence_time", match.get("date", ""))
        match_name = f"{home} vs {away}"

        for raw_market, bookie_odds in match.get("odds", {}).items():
            if not isinstance(bookie_odds, dict):
                continue
            market_key = normalise_market(raw_market)

            # Find the single best odds for each outcome side across all bookies
            best: dict[str, tuple[str, float]] = {}  # side → (bookmaker, odds)
            for bookie, sides in bookie_odds.items():
                if not isinstance(sides, dict):
                    continue
                for side, odd in sides.items():
                    try:
                        odd_f = float(odd)
                    except (TypeError, ValueError):
                        continue
                    if odd_f <= 1.0:
                        continue
                    s = side.lower().strip()
                    if s not in best or odd_f > best[s][1]:
                        best[s] = (bookie, odd_f)

            if len(best) < 2:
                continue

            # Arb check: sum of reciprocals < 1.0
            implied = sum(1.0 / v for _, v in best.values())
            if implied >= 1.0:
                continue

            profit = round((1.0 / implied - 1.0) * 100.0, 2)
            if profit < AGG_MIN_PROFIT:
                continue

            candidates.append({
                "match_name":  match_name,
                "sport":       sport,
                "commence":    commence,
                "market_key":  market_key,
                "market_raw":  raw_market,
                "profit_pct":  profit,
                "best_odds":   best,
            })

    candidates.sort(key=lambda x: x["profit_pct"], reverse=True)
    return candidates


# ── Alert text builder ──────────────────────────────────────────────────────

def build_alert_text(c: dict) -> str:
    lines = [
        "🔍 *[AGG] Aggregator Arb Found!*",
        "",
        f"⚽ *{c['match_name']}*",
        f"📊 Market: `{c['market_raw']}`",
        f"💰 Profit: *{c['profit_pct']:.2f}%*",
        "",
        "*Best odds per side:*",
    ]
    for side, (bookie, odd) in c["best_odds"].items():
        lines.append(f"  • {side.capitalize()}: `{odd}` @ {bookie}")
    lines += [
        "",
        f"⏰ Kickoff: `{c['commence']}`",
        "",
        "⚠️ _Verify odds are still live before placing bets_",
    ]
    return "\n".join(lines)


# ── Main loop ───────────────────────────────────────────────────────────────

async def aggregator_loop(
    balancer: Balancer,
    alert_callback,     # async fn(text: str) → None  (sends Telegram message)
    seen_arbs: set,     # shared with Engine 1 to avoid duplicate alerts
):
    """
    Background task — runs forever alongside Engine 1's scan_cycle loop.
    Rotates through sports, scrapes OddsPortal, finds and alerts on arbs.
    """
    log.info("[AGG] Engine 2 started (OddsPortal aggregator)")
    sport_idx = 0

    while True:
        try:
            if is_cool_off_day() or not is_active_hours():
                await asyncio.sleep(AGG_POLL_INTERVAL)
                continue

            sport = SPORT_ROTATION[sport_idx % len(SPORT_ROTATION)]
            sport_idx += 1
            all_candidates = []

            for market in AGG_MARKETS:
                # Write to a temp file, OddsHarvester appends .json
                tf = tempfile.NamedTemporaryFile(
                    suffix="", prefix=f"agg_{sport}_{market}_",
                    dir="/tmp", delete=False
                )
                out_base = tf.name
                tf.close()

                ok = await run_oddsharvester(sport, market, out_base)
                json_path = out_base + ".json"

                if ok and os.path.exists(json_path):
                    all_candidates.extend(parse_output(json_path))
                    try:
                        os.unlink(json_path)
                    except Exception:
                        pass

                await asyncio.sleep(5)  # pause between market requests

            alerted = 0
            for c in all_candidates:
                if alerted >= AGG_MAX_ALERTS:
                    break
                key = f"agg|{c['match_name']}|{c['market_key']}"
                if key in seen_arbs:
                    continue
                seen_arbs.add(key)
                await alert_callback(build_alert_text(c))
                alerted += 1
                await asyncio.sleep(2)

            log.info(
                f"[AGG] Cycle done | sport={sport} | "
                f"candidates={len(all_candidates)} | alerted={alerted}"
            )

        except Exception as e:
            log.error(f"[AGG] Loop error: {e}", exc_info=True)

        await asyncio.sleep(AGG_POLL_INTERVAL)
