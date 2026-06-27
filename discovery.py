"""
discovery.py – Multi-bookmaker match discovery and clustering.

Fetches match lists from multiple bookmakers, clusters them by real-world identity,
and returns a registry ready for pricing lookup.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from matcher import match_teams
from config import DISCOVERY_KICKOFF_PROXIMITY, BOOKMAKERS

log = logging.getLogger("discovery")


@asyncio.coroutine
async def fetch_all_bookmaker_events(
    scraper_map: Dict,  # {"sportybet": scraper_instance, "betway": scraper_instance, ...}
    sport_id: str = "soccer",
) -> Dict[str, List[dict]]:
    """
    Fetch upcoming event lists from all bookmakers in parallel.
    
    Returns:
    {
        "sportybet": [event1, event2, ...],
        "betway": [event3, event4, ...],
        "1win": [...],
    }
    """
    results = {}
    
    tasks = []
    for bookie in BOOKMAKERS:
        scraper = scraper_map.get(bookie)
        if not scraper:
            log.warning(f"No scraper for {bookie}")
            continue
        
        # Different bookmakers have different discovery patterns
        if bookie == "sportybet":
            # SportyBet uses web scraping + event ID extraction
            task = _fetch_sportybet_events(scraper)
        elif bookie == "betway":
            # Betway uses REST API discovery
            task = scraper.get_events(sport_id=sport_id)
        elif bookie == "1win":
            # 1win doesn't have direct event listing (we'll handle via match-by-match)
            task = None
        else:
            # Generic fallback
            task = None
        
        if task:
            tasks.append((bookie, task))
    
    # Run all discovery in parallel
    for bookie, task in tasks:
        try:
            events = await task
            results[bookie] = events or []
            log.info(f"{bookie}: discovered {len(results[bookie])} events")
        except Exception as e:
            log.error(f"Discovery error for {bookie}: {e}")
            results[bookie] = []
    
    return results


async def _fetch_sportybet_events(scraper) -> List[dict]:
    """SportyBet discovery — call the real scraper method."""
    try:
        return await scraper.get_events(sport="football")
    except Exception as e:
        log.error(f"SportyBet discovery error: {e}")
        return []


def cluster_matches(
    all_events: Dict[str, List[dict]],
) -> Dict[str, dict]:
    """
    Cluster events from different bookmakers into unified match registry.
    
    Matches two events if:
    1. Team names fuzzy-match (home vs home, away vs away)
    2. Kickoff times are within DISCOVERY_KICKOFF_PROXIMITY seconds
    
    Returns:
    {
        "match_1": {
            "name": "Arsenal vs Chelsea",
            "kickoff": 1782556200,
            "platforms": {
                "sportybet": "sr:match:12345",
                "betway": 66456908,
            }
        },
        ...
    }
    """
    registry = {}
    cluster_id = 0
    
    # Flatten all events with their source bookmaker
    flat = []
    for bookie, events in all_events.items():
        for event in events:
            flat.append({
                "bookmaker": bookie,
                "event": event,
                "home": event.get("homeTeam", event.get("team1", "")),
                "away": event.get("awayTeam", event.get("team2", "")),
                "kickoff": event.get("expectedStartEpoch", event.get("kickoffTime", 0)),
            })
    
    # Cluster by team names + kickoff proximity
    clustered = {}
    used = set()
    
    for i, ev_a in enumerate(flat):
        if i in used:
            continue
        
        cluster = [ev_a]
        used.add(i)
        
        for j, ev_b in enumerate(flat[i+1:], start=i+1):
            if j in used:
                continue
            
            # Check team match
            if not (match_teams(ev_a["home"], ev_b["home"]) and 
                    match_teams(ev_a["away"], ev_b["away"])):
                continue
            
            # Check kickoff proximity
            kickoff_a = ev_a.get("kickoff") or 0
            kickoff_b = ev_b.get("kickoff") or 0
            if abs(kickoff_a - kickoff_b) > DISCOVERY_KICKOFF_PROXIMITY:
                continue
            
            # Match found
            cluster.append(ev_b)
            used.add(j)
        
        # Build registry entry for this cluster
        match_name = f"{ev_a['home']} vs {ev_a['away']}"
        kickoff = ev_a.get("kickoff") or 0
        
        platforms = {}
        for ev in cluster:
            event_id = ev["event"].get("eventId", ev["event"].get("id"))
            platforms[ev["bookmaker"]] = event_id
        
        registry[f"match_{cluster_id}"] = {
            "name": match_name,
            "kickoff": kickoff,
            "platforms": platforms,
        }
        cluster_id += 1
    
    log.info(f"Clustered {len(flat)} events into {len(registry)} matches")
    return registry
