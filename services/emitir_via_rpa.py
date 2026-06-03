# services/emitir_via_rpa.py
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from services.bling_nfe_rpa import BlingRPA
from services.bling_service import BlingService
from services.ml_service import MercadoLivreService

def _timeout(fn, s, label):
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try: return fut.result(timeout=s)
        except FutureTimeout: raise Exception(f"Timeout {label} ({s}s)")

def _retry(fn, tries=3, sleep=1.5, label="op"):
    last = None
    for i in range(1, tries+1):
        try:
            if i > 1: print(f"Retry {i}/{tries} {label}...", flush=True)
            return fn()
        except Exception as e:
            last = e; time.sleep(sleep)
    raise last

def emitir_nfe_por_ml_id(
    ml_any_id: str,
    sku=None, headless=True,
    debug_skip_vendas_gerar=False,
    debug_numero_nf_manual=None,
    max_rpa_tries=3,
    printer_name=None,
    imprimir=True,
):
    t0 = time.time()
    ml    = MercadoLivreService()
    bling = BlingService()

    print("Resolvendo ML -> ORDER...", flush=True)
    order_id = ml.resolver_ml_para_numero_loja(ml_any_id)
    if not order_id: raise Exception("Nao consegui converter ML ID")
    print(f"numeroLoja = {order_id}", flush=True)

    print("Buscando PV...", flush=True)
    pv_id = _retry(
        lambda: _timeout(lambda: bling.buscar_pedido_venda_id_por_numero_loja(order_id, somente_em_aberto=False), 90, "buscar PV"),
        tries=3, sleep=2.0, label="buscar PV"
    )
    if not pv_id:
        pv_id = _retry(
            lambda: _timeout(lambda: bling.resolver_pv_id_por_ml_id(ml, order_id), 90, "fallback PV"),
            tries=3, sleep=2.0, label="fallback PV"
        )
    if not pv_id: raise Exception("PV nao encontrado")
    pv_id = int(pv_id)
    print(f"PV_ID = {pv_id}", flush=True)

    det = bling.get_pedido_venda_detalhe(pv_id)
    d   = det.get("data") or det
    numero_bling = str(d.get("numero") or d.get("numeroPedido") or pv_id).strip()
    print(f"numero_pedido Bling = {numero_bling}", flush=True)

    rpa = BlingRPA(headless=headless)
    last_err = None

    for tentativa in range(1, max_rpa_tries+1):
        try:
            if tentativa > 1:
                print(f"Retry RPA {tentativa}/{max_rpa_tries}...", flush=True)
                time.sleep(3.0*(tentativa-1))
            resultado = rpa.emitir(numero_bling, pv_id=pv_id, numero_loja=str(order_id))
            print(f"✅ RPA ok em {time.time()-t0:.1f}s", flush=True)
            return resultado
        except Exception as e:
            last_err = e
            print(f"❌ RPA falhou tentativa {tentativa}: {e}", flush=True)

    raise Exception(f"RPA falhou após {max_rpa_tries} tentativas. Ultimo erro: {last_err}")