"""
EstruturaService — obtém estrutura (componentes) de kits via API Bling v3.
Não usa Playwright. Cache local (lru_cache) por SKU.
"""
from functools import lru_cache

from services.bling_service import BlingService


class EstruturaService:

    @lru_cache(maxsize=500)
    def obter_estrutura(self, sku: str, nome: str = "") -> list:
        """
        Retorna componentes do kit: [{"sku": ..., "nome": ..., "quantidade": ...}]
        Retorna [] se produto não for kit ou não tiver estrutura cadastrada.

        nome: título do produto (passado como fallback quando sku = ID do ML)
        """
        try:
            bling = BlingService()
            return bling.obter_estrutura_produto_api(sku, nome=nome)
        except Exception as e:
            print(f"[EstruturaService] obter_estrutura sku={sku}: {e}", flush=True)
            return []
