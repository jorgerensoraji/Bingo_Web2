import sqlite3
db = sqlite3.connect('/var/www/bingopro/bingo.db')
rows = db.execute("SELECT tipo, prize_amount, merged_o, merged_u FROM winners WHERE session_id IN (SELECT id FROM bingo_sessions WHERE datetime_iso LIKE '2026-04-19%')").fetchall()
for r in rows:
    print(r)
