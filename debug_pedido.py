import sqlite3

db = sqlite3.connect("db/wms.db")
db.row_factory = sqlite3.Row

ml_id = "2000015463905344"

cur = db.cursor()

rows = cur.execute("""
SELECT
    pr.sku,
    pp.quantidade,
    pp.quantidade_bipada,
    p.ml_id,
    p.status
FROM pedido_produtos pp
JOIN produtos pr ON pr.id = pp.produto_id
JOIN pedidos p ON p.id = pp.pedido_id
WHERE p.ml_id = ?
""", (ml_id,)).fetchall()

print("\n===== ITENS DO PEDIDO =====")

for r in rows:
    print(
        "SKU:", r["sku"],
        "| QTD:", r["quantidade"],
        "| BIPADO:", r["quantidade_bipada"],
        "| STATUS:", r["status"]
    )

print("===========================\n")

db.close()