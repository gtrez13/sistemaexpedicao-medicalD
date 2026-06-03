import sqlite3
import os

db_path = next(
    (
        f for f in [
            'wms.db',
            'database.db',
            'app.db',
            'db/wms.db',
            'db/database.db'
        ]
        if os.path.exists(f)
    ),
    None
)

if not db_path:
    for root, dirs, files in os.walk('.'):
        for f in files:
            if f.endswith('.db'):
                db_path = os.path.join(root, f)
                break
        if db_path:
            break

print('DB:', db_path)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("""
SELECT sql
FROM sqlite_master
WHERE type='table'
ORDER BY name
""")

for row in cur.fetchall():
    if row[0]:
        print(row[0])
        print()

conn.close()