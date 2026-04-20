import sqlite3, os, glob
# Find all db files
dbs = glob.glob('/var/www/bingopro/*.db') + glob.glob('/var/www/bingopro/**/*.db')
print("DB files found:", dbs)
# Try each one
for dbpath in dbs:
    db = sqlite3.connect(dbpath)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"\n{dbpath}: {tables}")
    if 'winners' in tables:
        print("=== WINNERS ===")
        for r in db.execute("SELECT tipo, prize_amount, merged_o, merged_u FROM winners ORDER BY rowid DESC LIMIT 10").fetchall():
            print(r)
