import sqlite3
db = sqlite3.connect('/var/www/bingopro/bingo.db')
print("=== SESSIONS ===")
for r in db.execute("SELECT id, datetime_iso, status FROM bingo_sessions ORDER BY rowid DESC LIMIT 5").fetchall():
    print(r)
print("=== WINNERS ===")
for r in db.execute("SELECT tipo, prize_amount, merged_o, merged_u, session_id FROM winners ORDER BY rowid DESC LIMIT 10").fetchall():
    print(r)
