"""database.py – SQLite logging, weekly summaries, Telegram backup."""

import os
import sqlite3
import time
from datetime import datetime, timezone
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
            mode TEXT,
            executed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn

def log_opp(conn, data: dict):
    conn.execute("""
        INSERT INTO opportunities VALUES (
            NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        data.get("timestamp", datetime.now(timezone.utc).isoformat()),
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
        data.get("mode", "active"),
        data.get("executed", 0),
    ))
    conn.commit()

def generate_weekly_summary(conn) -> str:
    """Return a formatted summary string of the last 7 days."""
    cursor = conn.execute("""
        SELECT 
            COUNT(*) as total_arbs,
            SUM(CASE WHEN margin_pct > 0 THEN 1 ELSE 0 END) as profitable,
            AVG(margin_pct) as avg_margin,
            SUM(profit) as total_profit,
            MAX(margin_pct) as best_margin,
            MAX(match_name) as best_match
        FROM opportunities
        WHERE timestamp >= datetime('now', '-7 days')
    """)
    row = cursor.fetchone()
    if not row or row[0] == 0:
        return "No data for the last 7 days."

    return (
        f"📊 *Weekly Summary*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Arbs found: {row[0]}\n"
        f"Profitable: {row[1]}\n"
        f"Avg margin: {row[2]:.2f}%\n"
        f"Total profit: GHS {row[3]:.2f}\n"
        f"Best margin: {row[4]:.2f}%\n"
        f"Best match: {row[5]}\n"
    )

def backup_to_telegram(conn) -> tuple[bool, str]:
    """Generate a summary and return it for Telegram sending."""
    summary = generate_weekly_summary(conn)
    # Save a lightweight backup row (optional)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            summary TEXT
        )
    """)
    conn.execute("INSERT INTO backups VALUES (NULL, ?, ?)",
                 (datetime.now(timezone.utc).isoformat(), summary))
    conn.commit()
    return True, summary 
