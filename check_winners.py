import sqlite3
db = sqlite3.connect('/var/www/bingopro/bingo.db')
print("=== RECENT SESSIONS ===")
for r in db.execute("SELECT id, datetime_iso, status FROM sessions ORDER BY rowid DESC LIMIT 5").fetchall():
    print(r)
print("\n=== WINNERS WITH SESSION ===")
for r in db.execute("SELECT tipo, prize_amount, merged_o, merged_u, session_id, nombre FROM winners ORDER BY rowid DESC LIMIT 10").fetchall():
    print(r)
