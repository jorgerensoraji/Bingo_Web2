"""
backup.py — Automatic SQLite backup for Bingo Pro
--------------------------------------------------
- Saves timestamped copies to  <project>/backups/
- Keeps last 48 hourly  backups  (2 days)
- Keeps last 30 daily   backups  (1 month)
- Uses sqlite3.backup() — safe while the app is running
"""

import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PERU = ZoneInfo("America/Lima")

# Resolved at import time from the sibling bingo.db location
_BASE = Path(__file__).parent
DB_PATH     = _BASE / "bingo.db"
BACKUP_DIR  = _BASE / "backups"

KEEP_HOURLY = 48   # 2 days of hourly snapshots
KEEP_DAILY  = 30   # 1 month of daily snapshots


def _now_str(fmt: str) -> str:
    return datetime.now(PERU).strftime(fmt)


def run_backup() -> Path:
    """Create a timestamped backup of bingo.db.  Returns the backup path."""
    BACKUP_DIR.mkdir(exist_ok=True)

    ts   = _now_str("%Y-%m-%d_%H-%M-%S")
    dest = BACKUP_DIR / f"bingo_{ts}.db"

    # sqlite3.backup() gives a consistent snapshot even under concurrent writes
    src  = sqlite3.connect(str(DB_PATH))
    dst  = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    _prune()
    print(f"[BACKUP] Saved → {dest.name}  ({dest.stat().st_size // 1024} KB)")
    return dest


def _prune():
    """Remove old backups, keeping KEEP_HOURLY hourly and KEEP_DAILY daily files."""
    files = sorted(BACKUP_DIR.glob("bingo_*.db"), reverse=True)  # newest first

    # Hourly: keep the N most recent files
    for f in files[KEEP_HOURLY:]:
        # Spare files that are the sole representative of their date (daily)
        date_tag = f.stem.split("_")[1]   # e.g. "2026-04-25"
        siblings = [x for x in files[:KEEP_HOURLY + KEEP_DAILY]
                    if x.stem.split("_")[1] == date_tag]
        if not siblings:
            f.unlink()

    # Rebuild list after hourly prune
    files = sorted(BACKUP_DIR.glob("bingo_*.db"), reverse=True)

    # Daily: for each date keep only the latest file; delete older same-day files
    seen_dates: dict[str, int] = {}  # date -> count kept
    for f in files:
        date_tag = f.stem.split("_")[1]
        seen_dates[date_tag] = seen_dates.get(date_tag, 0) + 1
        if seen_dates[date_tag] > 1:
            f.unlink()

    # Hard cap: never keep more than KEEP_HOURLY + KEEP_DAILY files total
    files = sorted(BACKUP_DIR.glob("bingo_*.db"), reverse=True)
    for f in files[KEEP_HOURLY + KEEP_DAILY:]:
        f.unlink()


def list_backups() -> list[dict]:
    """Return metadata for all backup files, newest first."""
    BACKUP_DIR.mkdir(exist_ok=True)
    result = []
    for f in sorted(BACKUP_DIR.glob("bingo_*.db"), reverse=True):
        stat = f.stat()
        result.append({
            "name":     f.name,
            "size_kb":  round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=PERU).strftime("%Y-%m-%d %H:%M"),
        })
    return result


def latest_backup() -> Path | None:
    """Return the most recent backup file path, or None if no backups exist."""
    files = sorted(BACKUP_DIR.glob("bingo_*.db"), reverse=True)
    return files[0] if files else None
