"""
services/melhor_envio_service.py -- Cotacao de frete via API Melhor Envio v2.

Variaveis de ambiente necessarias:
  ME_TOKEN       -- token pessoal (Bearer) gerado no painel Melhor Envio
  ME_CEP_ORIGEM  -- CEP do remetente (ex: "01310100")
  ME_SANDBOX     -- "true" para usar sandbox (testes); omitir em producao
"""
from __future__ import annotations

import os
import requests
from dotenv import load_dotenv

load_dotenv()


class MelhorEnvioService:

    PROD_URL    = "https://melhorenvio.com.br/api/v2"
    SANDBOX_URL = "https://sandbox.melhorenvio.com.br/api/v2"

    def __init__(self):
        # Aceita tanto MELHOR_ENVIO_TOKEN (novo) quanto ME_TOKEN (legado)
        self.token = (
            os.getenv("MELHOR_ENVIO_TOKEN") or
            os.getenv("ME_TOKEN", "")
        ).strip()
        # Aceita CEP_ORIGEM (novo) ou ME_CEP_ORIGEM (legado)
        self.cep_origem = (
            os.getenv("CEP_ORIGEM") or
            os.getenv("ME_CEP_ORIGEM", "")
        ).replace("-", "").replace(" ", "").strip()
        sandbox = (
            os.getenv("MELHOR_ENVIO_SANDBOX") or
            os.getenv("ME_SANDBOX", "false")
        ).lower() in ("1", "true", "yes")
        self.base_url = self.SANDBOX_URL if sandbox else self.PROD_URL

        if not self.token:
            raise RuntimeError("Token Melhor Envio não configurado. Adicione MELHOR_ENVIO_TOKEN no .env")
        if not self.cep_origem:
            raise RuntimeError("CEP de origem não configurado. Adicione CEP_ORIGEM no .env")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "LHVMED WMS (workstationlhv@gmail.com)",
        }

    def cotar_frete(
        self,
        cep_destino: str,
        produtos: list[dict],
        *,
        timeout: int = 20,
    ) -> list[dict]:
        """
        Cota frete para uma lista de produtos.

        produtos: list de dicts com:
          { sku, nome, peso_kg, altura_cm, largura_cm, comprimento_cm, quantidade }

        Retorna lista ordenada por preco:
          [{ carrier_id, carrier_nome, servico, preco, prazo_dias, erro }]
        """
        cep_destino = str(cep_destino).replace("-", "").strip()
        if len(cep_destino) != 8 or not cep_destino.isdigit():
            raise ValueError(f"CEP destino invalido: {cep_destino!r}")

        items = []
        for p in produtos:
            peso   = float(p.get("peso_kg")        or 0.3)
            altura = float(p.get("altura_cm")      or 2.0)
            largura = float(p.get("largura_cm")    or 11.0)
            comp   = float(p.get("comprimento_cm") or 16.0)
            qtd    = int(p.get("quantidade")       or 1)

            # ME minimos: peso 0.1kg, dimensoes 1cm; max caixa 105cm cada lado
            peso   = max(0.1, peso)
            altura = max(1.0, altura)
            largura = max(1.0, largura)
            comp   = max(1.0, comp)

            items.append({
                "id":               str(p.get("sku") or "produto"),
                "width":            round(largura, 1),
                "height":           round(altura,  1),
                "length":           round(comp,    1),
                "weight":           round(peso,    3),
                "insurance_value":  0,
                "quantity":         qtd,
            })

        if not items:
            return []

        payload = {
            "from":     {"postal_code": self.cep_origem},
            "to":       {"postal_code": cep_destino},
            "products": items,
        }

        r = requests.post(
            f"{self.base_url}/me/shipment/calculate",
            headers=self._headers(),
            json=payload,
            timeout=timeout,
        )

        if r.status_code not in (200, 201):
            raise RuntimeError(f"Melhor Envio retornou {r.status_code}: {r.text[:400]}")

        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Resposta inesperada Melhor Envio: {str(data)[:200]}")

        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            company  = item.get("company") or {}
            erro     = item.get("error")
            result.append({
                "carrier_id":    item.get("id"),
                "carrier_nome":  company.get("name") or item.get("name") or "?",
                "servico":       item.get("name") or "",
                "preco":         float(item.get("price") or 0),
                "prazo_dias":    item.get("delivery_time"),
                "erro":          erro,
            })

        # ordena: sem erro primeiro, depois por preco
        result.sort(key=lambda x: (bool(x["erro"]), x["preco"]))
        return result
