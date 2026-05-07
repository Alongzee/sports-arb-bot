"""SQLite setup and opportunity logging."""

import os
import sqlite3
from datetime import datetime
from config import DB_PATH

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            match_name TEXT,
            market TEXT,
            best_over_platform TEXT,
            best_over_odds REAL,
            best_under_platform TEXT,
            best_under_odds REAL,
            combined_imp REAL,
            margin_pct REAL,
            stake_over REAL,
            stake_under REAL,
            total_stake REAL,
            payout REAL,
            profit REAL,
            mode TEXT
        )
    """)
    conn.commit()
    return conn

def log_opp(conn, data: dict):
    conn.execute("""
        INSERT INTO opportunities VALUES (
            NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        datetime.utcnow().isoformat(),
        data["match"],
        data["market"],
        data["best_over_platform"],
        data["best_over_odds"],
        data["best_under_platform"],
        data["best_under_odds"],
        data["combined_imp"],
        data["margin_pct"],
        data["stake_over"],
        data["stake_under"],
        data["total_stake"],
        data["payout"],
        data["profit"],
        data["mode"],
    ))
    conn.commit()
