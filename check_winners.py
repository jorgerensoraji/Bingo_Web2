import sqlite3, json
db = sqlite3.connect('/var/www/bingopro/bingo.db')

print("=== SESSIONS TABLE COLUMNS ===")
for r in db.execute("PRAGMA table_info(sessions)").fetchall():
    print(f"  {r[1]} ({r[2]})")

print("\n=== SESSION 613AFE9E ALL DATA ===")
cols = [r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
for r in db.execute("SELECT * FROM sessions WHERE id='613AFE9E'").fetchall():
    for col, val in zip(cols, r):
        if val and col not in ('cartillas_json',):
            print(f"  {col}: {str(val)[:200]}")
