"""
Teste direto do novo BlingNFeApiService (sem RPA, sem bipar).

Uso:
    venv\Scripts\python scripts\test_nfe_api.py <ML_ORDER_ID>

Exemplos:
    venv\Scripts\python scripts\test_nfe_api.py 2000123456789012
    venv\Scripts\python scripts\test_nfe_api.py 2000123456789012 --apenas-danfe 98765432

Flags:
    --apenas-danfe <nfe_id>   Só baixa DANFE de uma NF-e já existente (sem emitir)
    --dry-run                  Só resolve PV + NF existente, não emite
"""
import sys
import os
import time
import logging

# garante imports do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

from dotenv import load_dotenv
load_dotenv()

from services.bling_nfe_api_service import BlingNFeApiService
from services.bling_service import BlingService
from services.ml_service import MercadoLivreService


def testar_emissao(ml_id: str):
    print(f"\n{'='*60}")
    print(f"  TESTE NF-e API — ml_id: {ml_id}")
    print(f"{'='*60}\n")

    t0 = time.time()
    try:
        resultado = BlingNFeApiService().emitir_nfe(ml_id)
        elapsed = time.time() - t0

        print(f"\n✅ SUCESSO em {elapsed:.1f}s")
        print(f"   nfe_id   : {resultado.get('nfe_id')}")
        print(f"   numero_nf: {resultado.get('numero_nf')}")
        print(f"   pv_id    : {resultado.get('pv_id')}")
        print(f"   pdf      : {resultado.get('pdf')}")

        pdf_url = resultado.get("pdf") or ""
        if pdf_url.startswith("/print/"):
            filename = pdf_url.split("/print/")[-1]
            caminho = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "downloads", filename
            )
            existe = os.path.exists(caminho)
            print(f"   PDF existe em disco: {'SIM' if existe else 'NÃO'} ({caminho})")

    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n❌ FALHOU em {elapsed:.1f}s: {e}")
        raise


def testar_apenas_danfe(nfe_id: int):
    print(f"\n{'='*60}")
    print(f"  TESTE DANFE — nfe_id: {nfe_id}")
    print(f"{'='*60}\n")

    svc = BlingNFeApiService()
    pdf = svc.obter_pdf_danfe(nfe_id)
    if pdf and pdf[:4] == b"%PDF":
        print(f"✅ DANFE baixado com sucesso ({len(pdf)} bytes)")
    else:
        print(f"❌ Não veio PDF ({len(pdf or b'')} bytes, início={pdf[:20] if pdf else b''})")


def dry_run(ml_id: str):
    print(f"\n{'='*60}")
    print(f"  DRY RUN — ml_id: {ml_id}")
    print(f"{'='*60}\n")

    ml = MercadoLivreService()
    bling = BlingService()

    print("1) ML ID → order_id ...")
    order_id = ml.resolver_ml_para_numero_loja(ml_id)
    print(f"   order_id = {order_id}")
    if not order_id:
        print("❌ Não resolveu order_id. Verifique ML_ACCESS_TOKEN no .env.")
        return

    print("2) order_id → PV no Bling ...")
    pv_id = bling.buscar_pedido_venda_id_por_numero_loja(order_id, somente_em_aberto=False)
    print(f"   pv_id = {pv_id}")
    if not pv_id:
        print("❌ PV não encontrado. Pedido pode ainda não ter sincronizado no Bling.")
        return

    print("3) PV → NF-e existente ...")
    nfe_id = bling.extrair_nfe_id_do_pedido_venda(int(pv_id))
    print(f"   nfe_id (do PV) = {nfe_id}")
    if not nfe_id:
        nfe_id = bling.encontrar_nfe_id_por_pedido_venda(int(pv_id), numero_loja=str(order_id))
        print(f"   nfe_id (por score) = {nfe_id}")

    if nfe_id:
        det = bling.obter_nfe_detalhe(int(nfe_id))
        status = det.get("situacao") or det.get("status")
        numero = det.get("numero") or det.get("numeroNota")
        print(f"   status = {status}  |  numero = {numero}")
        autorizada = bling.nfe_esta_autorizada(int(nfe_id))
        print(f"   autorizada = {autorizada}")
    else:
        print("   (nenhuma NF-e vinculada ainda — será criada ao emitir)")

    print("\n✅ Dry run concluído — use sem --dry-run para emitir de verdade.")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    if "--apenas-danfe" in args:
        idx = args.index("--apenas-danfe")
        try:
            nfe_id = int(args[idx + 1])
        except (IndexError, ValueError):
            print("❌ Informe o nfe_id após --apenas-danfe")
            sys.exit(1)
        testar_apenas_danfe(nfe_id)

    elif "--dry-run" in args:
        ml_id = args[0]
        dry_run(ml_id)

    else:
        ml_id = args[0]
        testar_emissao(ml_id)
