import sqlite3, json
db = sqlite3.connect('/var/www/bingopro/bingo.db')
print("=== SESSION 613AFE9E prize_info ===")
for r in db.execute("SELECT prize_info FROM sessions WHERE id='613AFE9E'").fetchall():
    info = json.loads(r[0]) if r[0] else {}
    for k,v in info.items():
        print(f"  {k}: {v}")
print("\n=== GAME STATE IN DB ===")
for r in db.execute("SELECT data FROM sessions WHERE id='613AFE9E'").fetchall():
    d = json.loads(r[0]) if r[0] else {}
    print(f"  prize_pool: {d.get('prize_pool')}")
    print(f"  o_pool: {d.get('o_pool')}")
    print(f"  u_pool: {d.get('u_pool')}")
    print(f"  o_claimed: {d.get('o_claimed')}")
    print(f"  u_claimed: {d.get('u_claimed')}")
