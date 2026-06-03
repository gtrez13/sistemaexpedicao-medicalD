"""
services/shopee_service.py — Shopee Open Platform API v2
Implementa ShopeeService (auth + GET de pedidos) e sync_shopee_pedidos().
"""
import os
import re
import time
import hmac
import json
import hashlib
import logging
import pathlib
import traceback
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://partner.shopeemobile.com"
API_BASE = f"{BASE_URL}/api/v2"


def _load_shopee_tokens() -> dict:
    """
    Carrega tokens Shopee na ordem: banco → shopee_tokens.json → .env.
    Retorna dict com access_token, refresh_token, shop_id, expires_at.
    """
    # 1. Banco
    try:
        from db import get_db
        db = get_db()
        row = db.execute(
            "SELECT shop_id, access_token, refresh_token, expires_at "
            "FROM shopee_tokens ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        db.close()
        if row and row["access_token"]:
            expires_at = row["expires_at"]
            if hasattr(expires_at, "timestamp"):
                expires_at = expires_at.timestamp()
            else:
                expires_at = time.time() + 14400
            return {
                "shop_id":       int(row["shop_id"]),
                "access_token":  row["access_token"],
                "refresh_token": row["refresh_token"],
                "expires_at":    float(expires_at),
            }
    except Exception as e:
        logger.debug("[ShopeeService] DB token load falhou: %s", e)

    # 2. shopee_tokens.json
    token_file = pathlib.Path(__file__).parent.parent / "shopee_tokens.json"
    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
        if data.get("access_token") and data.get("shop_id"):
            return data
    except Exception:
        pass

    # 3. .env
    at  = os.getenv("SHOPEE_ACCESS_TOKEN", "")
    rt  = os.getenv("SHOPEE_REFRESH_TOKEN", "")
    sid = os.getenv("SHOPEE_SHOP_ID", "")
    if at and sid:
        return {
            "shop_id":       int(sid),
            "access_token":  at,
            "refresh_token": rt,
            "expires_at":    time.time() + 14400,
        }

    return {}


class ShopeeService:
    def __init__(self, conta: dict | None = None):
        self.partner_id  = int(os.environ["SHOPEE_PARTNER_ID"])
        self.partner_key = os.environ["SHOPEE_PARTNER_KEY"]

        if conta and conta.get("access_token") and conta.get("shop_id"):
            # Credenciais passadas diretamente (multi-conta)
            self.shop_id       = int(conta["shop_id"])
            self.access_token  = conta["access_token"]
            self.refresh_token = conta.get("refresh_token", "")
        else:
            tokens = _load_shopee_tokens()
            if not tokens:
                raise RuntimeError(
                    "Tokens Shopee não encontrados. "
                    "Execute scripts/authorize_shopee_conta.py para autorizar."
                )
            self.shop_id       = int(tokens["shop_id"])
            self.access_token  = tokens["access_token"]
            self.refresh_token = tokens.get("refresh_token", "")

    # ── assinaturas HMAC ──────────────────────────────────────────────────────

    def _sign(self, path: str, timestamp: int) -> str:
        """Assinatura para endpoints autenticados (inclui access_token e shop_id)."""
        full_path = f"/api/v2{path}"
        base = f"{self.partner_id}{full_path}{timestamp}{self.access_token}{self.shop_id}"
        return hmac.new(
            self.partner_key.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _sign_public(self, path: str, timestamp: int) -> str:
        """Assinatura para endpoints públicos (sem access_token nem shop_id)."""
        full_path = f"/api/v2{path}"
        base = f"{self.partner_id}{full_path}{timestamp}"
        return hmac.new(
            self.partner_key.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _do_get(self, path: str, params: dict | None = None) -> requests.Response:
        ts   = int(time.time())
        sign = self._sign(path, ts)
        url  = f"{API_BASE}{path}"
        base_params = {
            "partner_id":   self.partner_id,
            "shop_id":      self.shop_id,
            "access_token": self.access_token,
            "timestamp":    ts,
            "sign":         sign,
        }
        if params:
            base_params.update(params)
        return requests.get(url, params=base_params, timeout=15)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._do_get(path, params)
        if resp.status_code == 403:
            logger.info("[ShopeeService] 403 recebido — tentando refresh de token.")
            if self._refresh_token():
                resp = self._do_get(path, params)
        resp.raise_for_status()
        return resp.json()

    # ── refresh de token ──────────────────────────────────────────────────────

    def _refresh_token(self) -> bool:
        """Renova o access_token usando o refresh_token."""
        path   = "/auth/access_token/get"
        ts     = int(time.time())
        sign   = self._sign_public(path, ts)

        url    = f"{API_BASE}{path}"
        body   = {
            "shop_id":       self.shop_id,
            "refresh_token": self.refresh_token,
            "partner_id":    self.partner_id,
        }
        params = {"partner_id": self.partner_id, "timestamp": ts, "sign": sign}

        try:
            resp = requests.post(url, json=body, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            logger.warning("[ShopeeService] Erro ao chamar refresh endpoint: %s", e)
            return False

        if data.get("access_token"):
            self.access_token  = data["access_token"]
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            self._save_tokens(self.access_token, self.refresh_token)
            logger.info("[ShopeeService] Token renovado com sucesso.")
            return True

        logger.warning("[ShopeeService] Falha ao renovar token: %s", data)
        return False

    def _save_tokens(self, access_token: str, refresh_token: str) -> None:
        """Persiste novos tokens no .env, banco e shopee_tokens.json."""
        env_path = pathlib.Path(__file__).parent.parent / ".env"
        try:
            env_text = env_path.read_text(encoding="utf-8")
            for key, val in [
                ("SHOPEE_ACCESS_TOKEN",  access_token),
                ("SHOPEE_REFRESH_TOKEN", refresh_token),
            ]:
                if re.search(rf"^{key}\s*=", env_text, flags=re.MULTILINE):
                    env_text = re.sub(
                        rf"^{key}\s*=.*$", f"{key}={val}",
                        env_text, flags=re.MULTILINE,
                    )
                else:
                    env_text += f"\n{key}={val}"
            env_path.write_text(env_text, encoding="utf-8")
        except Exception as e:
            logger.warning("[ShopeeService] Falha ao salvar token no .env: %s", e)

        try:
            from datetime import datetime, timezone, timedelta
            import psycopg2
            db_url = os.getenv("DATABASE_URL")
            if db_url:
                conn = psycopg2.connect(db_url)
                conn.autocommit = True
                cur = conn.cursor()
                cur.execute("""
                    UPDATE shopee_tokens
                    SET access_token = %s, refresh_token = %s,
                        expires_at = %s, updated_at = NOW()
                    WHERE shop_id = %s
                """, (access_token, refresh_token,
                      datetime.now(timezone.utc) + timedelta(hours=4),
                      self.shop_id))
                conn.close()
        except Exception as e:
            logger.warning("[ShopeeService] Falha ao salvar token no banco: %s", e)

        token_file = pathlib.Path(__file__).parent.parent / "shopee_tokens.json"
        try:
            existing: dict = {}
            if token_file.exists():
                try:
                    existing = json.loads(token_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            existing.update({
                "access_token":  access_token,
                "refresh_token": refresh_token,
                "expires_at":    time.time() + 14400,
            })
            token_file.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[ShopeeService] Falha ao salvar shopee_tokens.json: %s", e)

    # ── endpoints ─────────────────────────────────────────────────────────────

    def get_order_list(
        self,
        time_range_field: str = "create_time",
        days_back: int = 2,
        order_status: str = "READY_TO_SHIP",
        page_size: int = 50,
    ) -> list[dict]:
        now       = int(time.time())
        time_from = now - (days_back * 86_400)
        path      = "/order/get_order_list"
        all_orders: list[dict] = []
        cursor = ""

        while True:
            params: dict = {
                "time_range_field": time_range_field,
                "time_from":        time_from,
                "time_to":          now,
                "page_size":        page_size,
                "order_status":     order_status,
            }
            if cursor:
                params["cursor"] = cursor

            data     = self._get(path, params)
            response = data.get("response") or {}
            all_orders.extend(response.get("order_list") or [])

            if not response.get("more"):
                break
            cursor = response.get("next_cursor") or ""

        return all_orders

    def get_order_detail(self, order_sn_list: list[str]) -> list[dict]:
        path   = "/order/get_order_detail"
        result = []

        for i in range(0, len(order_sn_list), 50):
            chunk  = order_sn_list[i : i + 50]
            params = {
                "order_sn_list":            ",".join(chunk),
                "response_optional_fields": (
                    "item_list,recipient_address,buyer_username,pay_time"
                ),
            }
            data = self._get(path, params)
            result.extend((data.get("response") or {}).get("order_list") or [])

        return result


# ─── Sync principal ───────────────────────────────────────────────────────────

def _get_shopee_contas() -> list[dict]:
    """Retorna contas Shopee ativas da tabela contas_marketplace."""
    try:
        from db import get_db
        db = get_db()
        rows = db.execute(
            "SELECT * FROM contas_marketplace WHERE plataforma='SHOPEE' AND ativo=true ORDER BY nome"
        ).fetchall()
        db.close()
        if rows:
            return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("[ShopeeService] Erro ao buscar contas: %s", e)
    return []


def sync_shopee_pedidos(days_back: int = 2) -> dict:
    """
    1. Puxa pedidos READY_TO_SHIP de todas as contas Shopee ativas.
    2. Busca detalhes (itens, comprador).
    3. Para cada item expande kit via EstruturaService (igual ML e Comercial).
    4. Insere em pedidos + pedido_produtos com origem='SHOPEE' e conta_nome.
    5. Retorna {"inseridos": N, "ja_existiam": M, "erros": [...]}.
    """
    from db import get_db
    from services.estrutura_service import EstruturaService

    contas = _get_shopee_contas()
    if not contas:
        # Fallback para conta única do .env
        try:
            svc = ShopeeService()
            contas = [{"id": None, "nome": "LHVMED 1",
                       "shopee_shop_id":      svc.shop_id,
                       "shopee_access_token":  svc.access_token,
                       "shopee_refresh_token": svc.refresh_token}]
        except Exception as e:
            raise RuntimeError(
                f"Credencial Shopee não configurada: {e}. "
                "Execute scripts/authorize_shopee_conta.py para autorizar."
            )

    from datetime import datetime, timezone as _tz

    estrutura_service = EstruturaService()
    db  = get_db()
    cur = db.cursor()

    inseridos   = 0
    ja_existiam = 0
    erros: list[dict] = []

    for conta in contas:
        conta_nome = conta.get("nome", "LHVMED 1")
        conta_id   = conta.get("id")

        # Instancia o service com as credenciais desta conta
        try:
            svc = ShopeeService(conta={
                "shop_id":       conta.get("shopee_shop_id"),
                "access_token":  conta.get("shopee_access_token"),
                "refresh_token": conta.get("shopee_refresh_token"),
            })
        except Exception as e:
            logger.error("[SHOPEE SYNC] Conta '%s' sem tokens válidos: %s", conta_nome, e)
            erros.append({"conta": conta_nome, "erro": str(e)})
            continue

        logger.info("[SHOPEE SYNC] Iniciando conta '%s' (shop_id=%s)", conta_nome, svc.shop_id)

        orders = svc.get_order_list(order_status="READY_TO_SHIP", days_back=days_back)
        if not orders:
            logger.info("[SHOPEE SYNC] Conta '%s': nenhum pedido.", conta_nome)
            continue

        order_sns = [o["order_sn"] for o in orders]
        details   = svc.get_order_detail(order_sns)

        for order in details:
            order_sn = order.get("order_sn") or ""
            if not order_sn:
                continue

            ml_id = f"SHOPEE_{order_sn}"

            try:
                existing = cur.execute(
                    "SELECT id FROM pedidos WHERE ml_id = ?", (ml_id,)
                ).fetchone()

                if existing:
                    ja_existiam += 1
                    continue

                cliente_nome = (order.get("buyer_username") or "Cliente Shopee").strip()
                ship_by_raw  = order.get("ship_by_date")
                ship_by_dt   = datetime.fromtimestamp(ship_by_raw, tz=_tz.utc) if ship_by_raw else None

                cur.execute("""
                    INSERT INTO pedidos
                        (ml_id, cliente_nome, logistic_type, origem,
                         shopee_order_sn, ship_by_date, conta_nome, conta_id, status)
                    VALUES (?, ?, 'SHOPEE_STANDARD', 'SHOPEE', ?, ?, ?, ?, 'PENDENTE')
                    ON CONFLICT (ml_id) DO NOTHING
                """, (ml_id, cliente_nome, order_sn, ship_by_dt, conta_nome, conta_id))

                pedido_row = cur.execute(
                    "SELECT id FROM pedidos WHERE ml_id = ?", (ml_id,)
                ).fetchone()
                if not pedido_row:
                    continue
                pedido_id = pedido_row["id"]

                for item in order.get("item_list") or []:
                    sku  = (item.get("item_sku") or item.get("variation_sku") or "").strip()
                    nome = (item.get("item_name") or "Produto Shopee").strip()
                    qtd  = int(item.get("model_quantity_purchased") or 1)

                    if not sku:
                        sku = f"SHOPEE_ITEM_{item.get('item_id', 'SEM_SKU')}"

                    kit = estrutura_service.obter_estrutura(sku, nome)
                    if kit:
                        for comp in kit:
                            c_sku, c_nome, c_qtd = comp["sku"], comp["nome"], comp["quantidade"] * qtd
                            cur.execute("INSERT INTO produtos (sku,nome,estoque,localizacao) VALUES (?,?,0,'Geral') ON CONFLICT (sku) DO NOTHING", (c_sku, c_nome))
                            c_id = cur.execute("SELECT id FROM produtos WHERE sku=?", (c_sku,)).fetchone()["id"]
                            cur.execute("INSERT INTO pedido_produtos (pedido_id,produto_id,quantidade,parent_sku,parent_nome) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING", (pedido_id, c_id, c_qtd, sku, nome))
                    else:
                        cur.execute("INSERT INTO produtos (sku,nome,estoque,localizacao) VALUES (?,?,0,'Geral') ON CONFLICT (sku) DO NOTHING", (sku, nome))
                        prod_id = cur.execute("SELECT id FROM produtos WHERE sku=?", (sku,)).fetchone()["id"]
                        cur.execute("INSERT INTO pedido_produtos (pedido_id,produto_id,quantidade) VALUES (?,?,?) ON CONFLICT DO NOTHING", (pedido_id, prod_id, qtd))

                inseridos += 1

            except Exception as e:
                logger.error("[SHOPEE SYNC] Erro no pedido %s (conta=%s): %s", order_sn, conta_nome, e)
                traceback.print_exc()
                erros.append({"order_sn": order_sn, "conta": conta_nome, "erro": str(e)})

        logger.info("[SHOPEE SYNC] Conta '%s': %d novos pedidos.", conta_nome, inseridos)

    db.commit()
    db.close()

    return {"inseridos": inseridos, "ja_existiam": ja_existiam, "erros": erros}
