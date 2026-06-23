import sqlite3, json
db = sqlite3.connect("/etc/x-ui/x-ui.db")
db.row_factory = sqlite3.Row
c = db.cursor()
print("=== clients table schema ===")
for row in c.execute("PRAGMA table_info(clients)"):
    print(f"  {row['name']} {row['type']} default={row['dflt_value']}")
c.execute("SELECT * FROM clients LIMIT 1")
r = c.fetchone()
if r:
    print("\n=== sample client row ===")
    print(json.dumps({k: r[k] for k in r.keys()}, indent=2, default=str))
print("\n=== client_inbounds schema ===")
for row in c.execute("PRAGMA table_info(client_inbounds)"):
    print(f"  {row['name']} {row['type']} default={row['dflt_value']}")
c.execute("SELECT count(*) FROM client_inbounds")
print("\nclient_inbounds count:", c.fetchone()[0])
c.execute("SELECT * FROM client_inbounds LIMIT 3")
for r in c.fetchall():
    print(json.dumps({k: r[k] for k in r.keys()}, default=str))
print("\n=== clients per inbound ===")
for r in c.execute("SELECT inbound_id, count(*) as cnt FROM client_inbounds GROUP BY inbound_id"):
    print(f"  inbound {r['inbound_id']}: {r['cnt']} clients")
# Check if there's a client_traffics table too
print("\n=== all tables ===")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    c2 = db.cursor()
    c2.execute(f"SELECT count(*) FROM \"{r['name']}\"")
    print(f"  {r['name']}: {c2.fetchone()[0]} rows")
db.close()
