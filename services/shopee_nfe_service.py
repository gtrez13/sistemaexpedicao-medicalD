"""
services/shopee_nfe_service.py — NF-e e etiqueta para pedidos Shopee

Fluxo:
  1. Acha o PV no Bling pelo shopee_order_sn (usado como numeroLoja)
  2. Emite NF-e via BlingService (API — respeita EMITIR_VIA_API=true)
  3. Aguarda autorização SEFAZ
  4. Baixa DANFE e imprime
  5. Solicita e imprime etiqueta via Shopee API
"""
from __future__ import annotations

import os
import time
import hmac
import hashlib
import logging
import pathlib
import requests

log = logging.getLogger(__name__)

EMITIR_VIA_API = os.getenv("EMITIR_VIA_API", "true").strip().lower() in ("1", "true", "yes")


class ShopeeNFeService:

    def __init__(self, headless: bool = True):
        from services.bling_service import BlingService
        self.bling    = BlingService()
        self.headless = headless

    # ── NF-e ──────────────────────────────────────────────────────────────────

    def emitir_nfe_pedido(
        self,
        shopee_order_sn: str,
        pv_id: int | None = None,
    ) -> dict:
        """
        Emite NF-e para um pedido Shopee.
        Retorna: {"ok": bool, "nfe_id": int|None, "pdf": str|None,
                  "ja_existia": bool, "erro": str|None}
        """
        t0 = time.time()

        # 1. Resolve PV no Bling (shopee_order_sn == numeroLoja no Bling)
        if not pv_id:
            log.info("[SHOPEE NFE] Buscando PV no Bling: %s", shopee_order_sn)
            pv_id = self.bling.buscar_pedido_venda_id_por_numero_loja(shopee_order_sn)

        if not pv_id:
            return {
                "ok":    False,
                "erro":  (
                    f"Pedido {shopee_order_sn} não encontrado no Bling. "
                    "Verifique se a integração Shopee→Bling está ativa "
                    "em Configurações → Integrações no Bling."
                ),
            }

        log.info("[SHOPEE NFE] PV encontrado: %s", pv_id)

        # 2. Verifica NF-e existente
        nfe_id = self.bling.extrair_nfe_id_do_pedido_venda(pv_id)
        if nfe_id and self.bling.nfe_esta_autorizada(nfe_id):
            log.info("[SHOPEE NFE] NF-e já autorizada: %s", nfe_id)
            pdf = self._baixar_danfe(pv_id, shopee_order_sn)
            return {"ok": True, "nfe_id": nfe_id, "pdf": pdf, "ja_existia": True}

        # 3. Emite
        if EMITIR_VIA_API:
            try:
                nfe_id = self.bling.emitir_nfe_do_pv(pv_id, timeout_s=120)
                log.info("[SHOPEE NFE] NF-e emitida (API): %s", nfe_id)
            except Exception as e:
                log.error("[SHOPEE NFE] Falha na emissão API: %s", e)
                return {"ok": False, "erro": f"Emissão NF-e falhou: {e}"}
        else:
            # Fallback: Playwright RPA
            from services.bling_nfe_rpa import BlingRPA
            try:
                rpa    = BlingRPA(headless=self.headless)
                result = rpa.emitir(
                    numero_pedido=shopee_order_sn,
                    pv_id=pv_id,
                    numero_loja=shopee_order_sn,
                )
                nfe_id = result.get("nfe_id")
            except Exception as e:
                log.error("[SHOPEE NFE] Falha no RPA: %s", e)
                return {"ok": False, "erro": f"Emissão RPA falhou: {e}"}

        # 4. Aguarda autorização SEFAZ
        if nfe_id:
            try:
                self.bling.aguardar_nfe_autorizada(nfe_id, timeout_s=120)
            except Exception as e:
                log.warning("[SHOPEE NFE] Timeout aguardando SEFAZ: %s", e)

        # 5. Baixa DANFE
        pdf = self._baixar_danfe(pv_id, shopee_order_sn)

        log.info("[SHOPEE NFE] Concluído em %.1fs", time.time() - t0)
        return {"ok": True, "nfe_id": nfe_id, "pdf": pdf, "ja_existia": False}

    def _baixar_danfe(self, pv_id: int, numero_loja: str) -> str | None:
        """Baixa e salva o DANFE como PDF. Retorna caminho relativo ou None."""
        try:
            pdf_bytes, filename = self.bling.baixar_danfe_por_pedido_venda(
                pv_id, numero_loja=numero_loja
            )
            downloads = pathlib.Path(__file__).parent.parent / "downloads"
            downloads.mkdir(exist_ok=True)
            path = downloads / filename
            path.write_bytes(pdf_bytes)
            log.info("[SHOPEE NFE] DANFE salvo: %s", path)
            return str(path)
        except Exception as e:
            log.warning("[SHOPEE NFE] Falha ao baixar DANFE: %s", e)
            return None

    # ── Etiqueta Shopee ───────────────────────────────────────────────────────

    def imprimir_etiqueta_shopee(self, shopee_order_sn: str) -> dict:
        """
        Gera e imprime a etiqueta de envio via Shopee Open Platform.
        POST /api/v2/logistics/create_shipping_document  →
        GET  /api/v2/logistics/get_shipping_document     →  URL do PDF
        """
        from services.shopee_service import ShopeeService

        try:
            svc = ShopeeService()
        except Exception as e:
            return {"ok": False, "erro": f"Shopee não configurada: {e}"}

        BASE = "https://partner.shopeemobile.com"

        def _sign(path: str, ts: int) -> str:
            full = f"/api/v2{path}"
            base = f"{svc.partner_id}{full}{ts}{svc.access_token}{svc.shop_id}"
            return hmac.new(
                svc.partner_key.encode(), base.encode(), hashlib.sha256
            ).hexdigest()

        def _post(path: str, body: dict) -> dict:
            ts  = int(time.time())
            url = f"{BASE}/api/v2{path}"
            params = {
                "partner_id":   svc.partner_id,
                "shop_id":      svc.shop_id,
                "access_token": svc.access_token,
                "timestamp":    ts,
                "sign":         _sign(path, ts),
            }
            r = requests.post(url, params=params, json=body, timeout=30)
            r.raise_for_status()
            return r.json()

        def _get(path: str, extra: dict) -> dict:
            ts  = int(time.time())
            url = f"{BASE}/api/v2{path}"
            params = {
                "partner_id":   svc.partner_id,
                "shop_id":      svc.shop_id,
                "access_token": svc.access_token,
                "timestamp":    ts,
                "sign":         _sign(path, ts),
                **extra,
            }
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()

        # Passo 1: solicita geração do documento
        try:
            resp1 = _post("/logistics/create_shipping_document", {
                "order_list": [{"order_sn": shopee_order_sn}]
            })
            log.info("[SHOPEE ETIQUETA] create_shipping_document: %s", resp1)
        except Exception as e:
            return {"ok": False, "erro": f"create_shipping_document falhou: {e}"}

        # Aguarda processamento
        time.sleep(2)

        # Passo 2: baixa o documento
        try:
            resp2 = _get("/logistics/get_shipping_document", {
                "order_list": f'[{{"order_sn":"{shopee_order_sn}"}}]'
            })
            log.info("[SHOPEE ETIQUETA] get_shipping_document: %s", str(resp2)[:200])
        except Exception as e:
            return {"ok": False, "erro": f"get_shipping_document falhou: {e}"}

        # Extrai URL do PDF
        response    = resp2.get("response") or {}
        result_list = response.get("result_list") or []
        url_doc     = result_list[0].get("url") if result_list else None

        if not url_doc:
            return {
                "ok":  False,
                "erro": f"Shopee não retornou URL da etiqueta. Resposta: {resp2}",
            }

        # Baixa o PDF
        try:
            pdf_r = requests.get(url_doc, timeout=30)
            pdf_r.raise_for_status()
        except Exception as e:
            return {"ok": False, "erro": f"Download etiqueta falhou: {e}"}

        # Salva
        downloads = pathlib.Path(__file__).parent.parent / "downloads"
        downloads.mkdir(exist_ok=True)
        pdf_path = downloads / f"shopee_etiqueta_{shopee_order_sn}.pdf"
        pdf_path.write_bytes(pdf_r.content)
        log.info("[SHOPEE ETIQUETA] PDF salvo: %s", pdf_path)

        # Imprime
        printer = os.getenv("PRINTER_DANFE") or os.getenv("PRINTER_NAME", "")
        if printer:
            try:
                from services.windows_print_service import WindowsPrintService
                WindowsPrintService.print_pdf(str(pdf_path), printer, verify_queue=True)
                log.info("[SHOPEE ETIQUETA] Impressa em %s", printer)
            except Exception as e:
                log.warning("[SHOPEE ETIQUETA] Impressão falhou: %s", e)
                return {"ok": True, "pdf": str(pdf_path), "aviso": f"PDF gerado mas impressão falhou: {e}"}

        return {"ok": True, "pdf": str(pdf_path)}

    # ── Fluxo completo ────────────────────────────────────────────────────────

    def fluxo_completo(self, shopee_order_sn: str) -> dict:
        """NF-e + etiqueta Shopee. Retorna resultado de ambas as etapas."""
        nfe = self.emitir_nfe_pedido(shopee_order_sn)
        if not nfe["ok"]:
            return nfe

        etiqueta = self.imprimir_etiqueta_shopee(shopee_order_sn)
        return {
            "ok":      True,
            "nfe":     nfe,
            "etiqueta": etiqueta,
        }
