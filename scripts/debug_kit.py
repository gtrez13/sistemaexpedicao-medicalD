"""
Diagnóstico de kit: ML item → seller_sku → Bling produto → estrutura

Uso:
    venv\Scripts\python scripts\debug_kit.py <ML_ITEM_ID>

Exemplo:
    venv\Scripts\python scripts\debug_kit.py 175661522246
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import json
from services.ml_service import MercadoLivreService
from services.bling_service import BlingService

def run(item_id: str):
    ml = MercadoLivreService()
    bling = BlingService()

    print(f"\n{'='*60}")
    print(f"  ML ITEM: {item_id}")
    print(f"{'='*60}")

    # 1) busca item no ML
    r = ml._request("GET", f"/items/{item_id}")
    if not r or r.status_code != 200:
        print(f"❌ ML /items/{item_id} falhou: {r.status_code if r else 'sem resposta'}")
        return

    body = r.json()
    print(f"\n[ML] title             : {body.get('title')}")
    print(f"[ML] seller_sku        : {body.get('seller_sku')!r}")
    print(f"[ML] seller_custom_field: {body.get('seller_custom_field')!r}")

    # atributos
    attrs = body.get("attributes") or []
    sku_attr = None
    for a in attrs:
        if (a.get("id") or "").upper() in ("SELLER_SKU", "SELLER_CUSTOM_FIELD", "SKU"):
            sku_attr = a.get("value_name") or a.get("value_id")
            print(f"[ML] atributo {a.get('id')}: {sku_attr!r}")

    seller_sku = body.get("seller_custom_field") or body.get("seller_sku") or sku_attr
    print(f"\n[ML] seller_sku FINAL  : {seller_sku!r}")

    title = body.get("title") or ""

    # 2) busca no Bling por código
    print(f"\n{'─'*40}")
    print(f"[BLING] Buscando por código: {seller_sku or item_id}")
    r_cod = bling._get_json("/produtos", params={"codigo": seller_sku or item_id})
    itens_cod = r_cod.get("data") or []
    print(f"[BLING] resultados por código: {len(itens_cod)}")
    for p in itens_cod[:3]:
        print(f"  → id={p.get('id')} codigo={p.get('codigo')!r} tipo={p.get('tipo')!r} nome={p.get('nome', '')[:50]!r}")

    # 3) busca no Bling por nome (primeiras 5 palavras)
    criterio = " ".join(title.split()[:5])
    print(f"\n[BLING] Buscando por criterio: {criterio!r}")
    r_crit = bling._get_json("/produtos", params={"criterio": criterio, "limite": 10})
    itens_crit = r_crit.get("data") or []
    print(f"[BLING] resultados por criterio: {len(itens_crit)}")
    for p in itens_crit[:5]:
        print(f"  → id={p.get('id')} codigo={p.get('codigo')!r} tipo={p.get('tipo')!r} nome={p.get('nome', '')[:60]!r}")

    # 4) se achou produto, pega detalhe com estrutura
    produto_alvo = None
    for lista in [itens_cod, itens_crit]:
        for p in lista:
            if str(p.get("tipo") or "").upper() == "K":
                produto_alvo = p
                break
        if produto_alvo:
            break
    if not produto_alvo and (itens_cod or itens_crit):
        produto_alvo = (itens_cod or itens_crit)[0]

    if not produto_alvo:
        print("\n❌ Produto NÃO encontrado no Bling por código nem por nome.")
        print("   → Solução: cadastrar seller_sku no anúncio do ML com o código do Bling.")
        return

    pid = produto_alvo.get("id")
    print(f"\n[BLING] Produto encontrado: id={pid} tipo={produto_alvo.get('tipo')!r} codigo={produto_alvo.get('codigo')!r}")

    det = bling._get_json(f"/produtos/{pid}")
    obj = det.get("data") or det
    print(f"[BLING] tipo no detalhe: {obj.get('tipo')!r}")

    estrutura = obj.get("estrutura") or {}
    if isinstance(estrutura, dict):
        componentes = estrutura.get("componentes") or []
    else:
        componentes = estrutura if isinstance(estrutura, list) else []

    print(f"[BLING] componentes na estrutura: {len(componentes)}")
    for c in componentes:
        prod_c = c.get("produto") or {}
        print(f"  → sku={prod_c.get('codigo')!r} nome={prod_c.get('nome', '')[:40]!r} qtd={c.get('quantidade')}")

    if not componentes:
        print("\n⚠️  Produto encontrado mas SEM componentes na estrutura.")
        print("   → Verifique se o kit está cadastrado com estrutura no Bling (aba Estrutura do produto).")
        print(f"\nDetalhe completo do produto:")
        print(json.dumps(obj, indent=2, ensure_ascii=False)[:2000])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    run(sys.argv[1])
