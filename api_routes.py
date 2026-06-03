"""
api_routes.py -- Endpoints JSON para o frontend React (WMS por produto)

Todos os endpoints ficam sob /api/* para o nginx proxy separar
do Flask quando servido via Docker Compose.

Registrar em app.py:
    from api_routes import api_routes
    app.register_blueprint(api_routes)
"""
from __future__ import annotations
import traceback
from collections import defaultdict
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_login import login_required
from db import get_db
from services.auth_utils import require_role

api_routes = Blueprint("api", __name__, url_prefix="/api")


# ─── helpers ────────────────────────────────────────────────────────────────

def _tipo_envio_label(logistic_type: str) -> str:
    lt = (logistic_type or "").lower()
    if lt == "self_service":
        return "FLEX"
    if lt in ("cross_docking", "drop_off", "xd_drop_off"):
        return "COLETA/AGÊNCIA"
    return lt.upper() if lt else "PADRÃO"


# ─── GET /api/produtos-pendentes ────────────────────────────────────────────
@api_routes.get("/produtos-pendentes")
def produtos_pendentes():
    """
    Retorna lista agregada de produtos a separar, agrupada por SKU.
    Produtos com parent_sku são componentes de kit e ficam aninhados
    em "componentes" sob o registro do kit pai.

    Resposta:
    {
      "produtos": [
        {
          "sku": "PROD-001",
          "nome": "Produto Normal",
          "is_kit": false,
          "quantidade_total": 5,
          "pedidos_count": 3,
          "estoque_bling": 12
        },
        {
          "sku": "KIT-001",
          "nome": "Nome do Kit",
          "is_kit": true,
          "quantidade_total": 10,
          "pedidos_count": 2,
          "estoque_bling": null,
          "componentes": [
            {"sku": "COMP-A", "nome": "Componente A", "quantidade_total": 5, "estoque_bling": 8}
          ]
        }
      ]
    }
    """
    try:
        db = get_db()
        rows = db.execute("""
            SELECT
                pr.sku,
                pr.nome,
                pr.estoque_bling,
                pp.parent_sku,
                pp.parent_nome,
                SUM(pp.quantidade - pp.quantidade_bipada) AS qtd_faltando,
                COUNT(DISTINCT p.id)                      AS pedidos_count
            FROM pedido_produtos pp
            JOIN produtos pr ON pr.id = pp.produto_id
            JOIN pedidos   p  ON p.id  = pp.pedido_id
            WHERE p.status IN ('PENDENTE', 'PARCIAL')
              AND pp.quantidade_bipada < pp.quantidade
            GROUP BY pr.sku, pr.nome, pr.estoque_bling, pp.parent_sku, pp.parent_nome
            HAVING SUM(pp.quantidade - pp.quantidade_bipada) > 0
            ORDER BY pp.parent_sku NULLS LAST, pr.nome COLLATE NOCASE
        """).fetchall()
        db.close()

        # ── Separar normais de componentes de kit ──────────────────────────
        normais: list[dict] = []
        kits: dict[str, dict] = {}

        for r in rows:
            parent_sku = r["parent_sku"]
            if parent_sku is None:
                normais.append({
                    "sku":              r["sku"],
                    "nome":             r["nome"] or r["sku"],
                    "is_kit":           False,
                    "quantidade_total": int(r["qtd_faltando"]),
                    "pedidos_count":    int(r["pedidos_count"]),
                    "estoque_bling":    r["estoque_bling"],
                })
            else:
                kit_nome = r["parent_nome"] or ("KIT " + parent_sku)
                if parent_sku not in kits:
                    kits[parent_sku] = {
                        "sku":              parent_sku,
                        "nome":             kit_nome,
                        "is_kit":           True,
                        "quantidade_total": 0,
                        "pedidos_count":    int(r["pedidos_count"]),
                        "estoque_bling":    None,
                        "componentes":      [],
                    }
                kits[parent_sku]["componentes"].append({
                    "sku":              r["sku"],
                    "nome":             r["nome"] or r["sku"],
                    "quantidade_total": int(r["qtd_faltando"]),
                    "estoque_bling":    r["estoque_bling"],
                })
                kits[parent_sku]["quantidade_total"] += int(r["qtd_faltando"])
                kits[parent_sku]["pedidos_count"] = max(
                    kits[parent_sku]["pedidos_count"], int(r["pedidos_count"])
                )

        # Merge e ordena por nome case-insensitive
        todos = normais + list(kits.values())
        todos.sort(key=lambda x: (x["nome"] or "").lower())

        return jsonify({"produtos": todos})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── POST /api/bipar-produto ─────────────────────────────────────────────────
@api_routes.post("/bipar-produto")
def bipar_produto():
    """
    Bipa {quantidade} unidades de um SKU e distribui automaticamente
    entre os pedidos que precisam desse produto (FIFO: pedido mais antigo primeiro).

    Body JSON:
        { "sku": "ABC-123", "quantidade": 3 }

    Resposta:
    {
      "success": true,
      "distribuicao": [
        { "ml_id": "...", "quantidade_distribuida": 2 }
      ],
      "pedidos_concluidos": ["ml_id1"],
      "faltando": 0
    }
    """
    data = request.get_json(silent=True) or {}
    sku  = (data.get("sku") or "").strip()
    qtd  = int(data.get("quantidade") or 1)

    if not sku:
        return jsonify({"success": False, "error": "sku obrigatório"}), 400
    if qtd < 1:
        return jsonify({"success": False, "error": "quantidade deve ser >= 1"}), 400

    try:
        db  = get_db()
        cur = db.cursor()

        # linhas de pedido_produtos que precisam desse SKU, FIFO por criado_em
        # Aceita bipe pelo SKU do componente OU pelo SKU do kit (parent_sku)
        rows = cur.execute("""
            SELECT
                pp.id         AS pp_id,
                pp.pedido_id,
                p.ml_id,
                pp.quantidade          AS qtd_total,
                pp.quantidade_bipada   AS qtd_bip,
                (pp.quantidade - pp.quantidade_bipada) AS faltando
            FROM pedido_produtos pp
            JOIN produtos pr ON pr.id = pp.produto_id
            JOIN pedidos  p  ON p.id  = pp.pedido_id
            WHERE (UPPER(pr.sku) = UPPER(?) OR UPPER(pp.parent_sku) = UPPER(?))
              AND p.status IN ('PENDENTE', 'PARCIAL')
              AND pp.quantidade_bipada < pp.quantidade
            ORDER BY p.criado_em ASC, pp.pedido_id ASC
        """, (sku, sku)).fetchall()

        if not rows:
            db.close()
            return jsonify({
                "success": False,
                "error": f"SKU '{sku}' não encontrado em pedidos pendentes"
            }), 404

        restante     = qtd
        distribuicao = []
        concluidos   = []

        for row in rows:
            if restante <= 0:
                break

            dar      = min(restante, int(row["faltando"]))
            novo_bip = int(row["qtd_bip"]) + dar
            restante -= dar

            cur.execute(
                "UPDATE pedido_produtos SET quantidade_bipada = ? WHERE id = ?",
                (novo_bip, row["pp_id"])
            )

            distribuicao.append({
                "ml_id":                row["ml_id"],
                "quantidade_distribuida": dar,
            })

            # verifica se esse pedido ficou 100% bipado
            falt_restante = cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM pedido_produtos
                WHERE pedido_id = ?
                  AND quantidade_bipada < quantidade
            """, (row["pedido_id"],)).fetchone()["cnt"]

            if falt_restante == 0:
                # move para SEPARADO
                cur.execute("""
                    UPDATE pedidos
                       SET status = 'SEPARADO',
                           atualizado_em = CURRENT_TIMESTAMP
                     WHERE id = ?
                """, (row["pedido_id"],))
                cur.execute("""
                    UPDATE pedido_produtos
                       SET quantidade_separada = quantidade_bipada,
                           quantidade_bipada   = 0
                     WHERE pedido_id = ?
                """, (row["pedido_id"],))
                concluidos.append(row["ml_id"])

        db.commit()
        db.close()

        return jsonify({
            "success":           True,
            "distribuicao":      distribuicao,
            "pedidos_concluidos": concluidos,
            "faltando":          restante,
            "message":           f"Distribuídas {qtd - restante} unidade(s) de '{sku}'",
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET /api/pedidos-status ─────────────────────────────────────────────────
@api_routes.get("/pedidos-status")
def pedidos_status():
    """
    Painel geral: todos os pedidos (exceto ARQUIVADO), com itens.

    Resposta:
    {
      "pedidos": [
        {
          "ml_id": "...",
          "cliente_nome": "...",
          "status": "PENDENTE",
          "envio_label": "FLEX",
          "total_quantidade": 5,
          "total_bipado": 3,
          "itens": [{ "sku", "nome", "quantidade", "quantidade_bipada" }]
        }
      ]
    }
    """
    try:
        db   = get_db()
        rows = db.execute("""
            SELECT
                p.id            AS pedido_id,
                p.ml_id,
                p.cliente_nome,
                p.status,
                p.logistic_type,
                p.origem,
                p.criado_em,
                p.prazo_envio,
                pr.sku,
                pr.nome,
                pr.estoque_bling,
                pp.quantidade,
                pp.quantidade_bipada,
                pp.parent_sku,
                pp.parent_nome
            FROM pedidos p
            LEFT JOIN pedido_produtos pp ON pp.pedido_id = p.id
            LEFT JOIN produtos pr        ON pr.id = pp.produto_id
            WHERE p.status NOT IN ('ARQUIVADO')
            ORDER BY p.criado_em DESC, p.id DESC
        """).fetchall()
        db.close()

        pedidos: dict[int, dict] = {}
        for r in rows:
            pid = r["pedido_id"]
            if pid not in pedidos:
                pedidos[pid] = {
                    "ml_id":          r["ml_id"],
                    "cliente_nome":   r["cliente_nome"],
                    "status":         r["status"],
                    "envio_label":    _tipo_envio_label(r["logistic_type"]),
                    "origem":         r["origem"] if r["origem"] else "ML",
                    "criado_em":      str(r["criado_em"] or ""),
                    "prazo_envio":    r["prazo_envio"].isoformat() if r["prazo_envio"] else None,
                    "total_quantidade": 0,
                    "total_bipado":     0,
                    "itens":          [],
                }

            if r["sku"] is not None:
                pedidos[pid]["itens"].append({
                    "sku":               r["sku"],
                    "nome":              r["nome"] or r["sku"],
                    "quantidade":        int(r["quantidade"] or 0),
                    "quantidade_bipada": int(r["quantidade_bipada"] or 0),
                    "estoque_bling":     r["estoque_bling"],
                    "parent_sku":        r["parent_sku"],
                })
                pedidos[pid]["total_quantidade"] += int(r["quantidade"] or 0)
                pedidos[pid]["total_bipado"]     += int(r["quantidade_bipada"] or 0)

        return jsonify({"pedidos": list(pedidos.values())})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── GET /api/painel ────────────────────────────────────────────────────────
@api_routes.get("/painel")
def painel():
    """
    Painel TV de Expedição — dados completos para dashboard fullscreen.

    Resposta:
    {
      "timestamp": "10:45:23",
      "data": "Qui, 21 Mai",
      "canais": {
        "ml_flex":   { "hoje": 24, "amanha": 3 },
        "ml_coleta": { "hoje": 12, "amanha": 1 }
      },
      "fluxo": {
        "aguardando_separacao": 0,
        "em_separacao": 11,
        "concluidos_hoje": 112
      },
      "nfes_hoje": 108,
      "pendentes_total": 47,
      // legado (compatibilidade com componentes anteriores):
      "pedidos_hoje": { "total": 36, "flex": 24, "coleta": 12 },
      "em_separacao": 11,
      "pendentes": 0,
      "concluidos_hoje": 112,
      "gerado_em": "2026-05-21T10:45:23.000000"
    }
    """
    from datetime import timedelta

    # Nomes em português (sem depender do locale do SO)
    _DIAS  = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
    _MESES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
               'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

    now           = datetime.now()
    hoje_inicio   = datetime.combine(date.today(), datetime.min.time())
    amanha_inicio = hoje_inicio + timedelta(days=1)
    depois_amanha = amanha_inicio + timedelta(days=1)

    # Dia da semana em PT (0=Dom … 6=Sáb, igual ao JS getDay())
    dia_idx  = (now.weekday() + 1) % 7
    data_str = f"{_DIAS[dia_idx]}, {now.day:02d} {_MESES[now.month - 1]}"

    try:
        db = get_db()

        def _cnt(sql, params=()):
            return int(db.execute(sql, params).fetchone()["cnt"] or 0)

        # ── Canais: hoje ────────────────────────────────────────────
        flex_hoje = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos "
            "WHERE criado_em >= ? AND criado_em < ? AND logistic_type = 'self_service'",
            (hoje_inicio, amanha_inicio),
        )
        coleta_hoje = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos "
            "WHERE criado_em >= ? AND criado_em < ? "
            "  AND logistic_type IN ('cross_docking','drop_off','xd_drop_off')",
            (hoje_inicio, amanha_inicio),
        )

        # ── Canais: amanhã (pedidos já importados com data futura) ──
        flex_amanha = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos "
            "WHERE criado_em >= ? AND criado_em < ? AND logistic_type = 'self_service'",
            (amanha_inicio, depois_amanha),
        )
        coleta_amanha = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos "
            "WHERE criado_em >= ? AND criado_em < ? "
            "  AND logistic_type IN ('cross_docking','drop_off','xd_drop_off')",
            (amanha_inicio, depois_amanha),
        )

        # ── Fluxo ───────────────────────────────────────────────────
        aguardando = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos WHERE status IN ('PENDENTE','PARCIAL')"
        )
        em_sep = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos WHERE status = 'SEPARADO'"
        )
        concluidos = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos "
            "WHERE status = 'CONCLUIDO' AND atualizado_em >= ?",
            (hoje_inicio,),
        )

        # Todos os pedidos ativos (não concluídos e não arquivados)
        pendentes_tot = _cnt(
            "SELECT COUNT(*) AS cnt FROM pedidos "
            "WHERE status NOT IN ('CONCLUIDO','ARQUIVADO')"
        )

        db.close()

        return jsonify({
            # ── novo formato ───────────────────────────────────────
            "timestamp": now.strftime("%H:%M:%S"),
            "data":      data_str,
            "canais": {
                "ml_flex":   {"hoje": flex_hoje,   "amanha": flex_amanha},
                "ml_coleta": {"hoje": coleta_hoje, "amanha": coleta_amanha},
            },
            "fluxo": {
                "aguardando_separacao": aguardando,
                "em_separacao":         em_sep,
                "concluidos_hoje":      concluidos,
            },
            "nfes_hoje":       concluidos,       # CONCLUIDO ≡ NF-e autorizada
            "pendentes_total": pendentes_tot,
            # ── legado (mantém compatibilidade) ────────────────────
            "pedidos_hoje": {
                "total":  flex_hoje + coleta_hoje,
                "flex":   flex_hoje,
                "coleta": coleta_hoje,
            },
            "em_separacao":    em_sep,
            "pendentes":       aguardando,
            "concluidos_hoje": concluidos,
            "gerado_em":       now.isoformat(),
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── GET /api/pedido/<ml_id> ─────────────────────────────────────────────────
@api_routes.get("/pedido/<ml_id>")
def pedido_detalhe(ml_id: str):
    """
    Detalhe completo de um pedido.
    """
    try:
        db = get_db()

        p = db.execute(
            "SELECT * FROM pedidos WHERE ml_id = ? LIMIT 1", (str(ml_id),)
        ).fetchone()

        if not p:
            db.close()
            return jsonify({"error": "pedido não encontrado"}), 404

        itens = db.execute("""
            SELECT pr.sku, pr.nome, pr.estoque_bling,
                   pp.quantidade, pp.quantidade_bipada, pp.quantidade_separada,
                   pp.parent_sku, pp.parent_nome
            FROM pedido_produtos pp
            JOIN produtos pr ON pr.id = pp.produto_id
            WHERE pp.pedido_id = ?
            ORDER BY pr.nome
        """, (p["id"],)).fetchall()
        db.close()

        itens_list = [dict(i) for i in itens]
        total_qtd  = sum(int(i["quantidade"]       or 0) for i in itens_list)
        total_bip  = sum(int(i["quantidade_bipada"] or 0) for i in itens_list)

        return jsonify({
            "ml_id":           p["ml_id"],
            "cliente_nome":    p["cliente_nome"],
            "status":          p["status"],
            "logistic_type":   p["logistic_type"],
            "envio_label":     _tipo_envio_label(p["logistic_type"]),
            "criado_em":       str(p["criado_em"] or ""),
            "atualizado_em":   str(p["atualizado_em"] or ""),
            "total_quantidade": total_qtd,
            "total_bipado":    total_bip,
            "itens":           itens_list,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@api_routes.post("/sincronizar")
def api_sincronizar():
    from routes import sincronizar

    # força a rota antiga a responder JSON
    request.headers.environ["HTTP_X_REQUESTED_WITH"] = "fetch"

    return sincronizar()


# ─── DELETE /api/pedidos/limpar ──────────────────────────────────────────────
@api_routes.route("/pedidos/limpar", methods=["DELETE"])
def api_limpar_pendentes():
    """
    Apaga permanentemente todos os pedidos PENDENTE (e seus itens).
    Usado pelo botão "Limpar" na tela Separação.

    Resposta: { "success": true, "removidos": 7 }
    """
    try:
        db  = get_db()
        cur = db.cursor()

        pedidos = cur.execute(
            "SELECT id FROM pedidos WHERE status = 'PENDENTE'"
        ).fetchall()

        ids   = [p["id"] for p in pedidos]
        count = len(ids)

        if ids:
            ph = ",".join("?" * len(ids))
            cur.execute(f"DELETE FROM pedido_produtos WHERE pedido_id IN ({ph})", tuple(ids))
            cur.execute(f"DELETE FROM pedidos          WHERE id          IN ({ph})", tuple(ids))

        db.commit()
        db.close()

        return jsonify({"success": True, "removidos": count})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ─── POST /api/sync/shopee ───────────────────────────────────────────────────
@api_routes.post("/sync/shopee")
@require_role("admin", "supervisor", "operator")
def sync_shopee():
    """
    Importa pedidos READY_TO_SHIP da Shopee Open Platform.
    Expande kits via EstruturaService (mesmo comportamento de ML e Comercial).
    Resposta: { "success": true, "inseridos": N, "ja_existiam": M, "erros": [...] }
    """
    try:
        from services.shopee_service import sync_shopee_pedidos

        body      = request.get_json(silent=True) or {}
        days_back = int(body.get("days_back") or 2)

        result = sync_shopee_pedidos(days_back=days_back)
        return jsonify({"success": True, **result})

    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 503
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ─── POST /api/shopee/emitir-nfe/<order_sn> ──────────────────────────────────
@api_routes.post("/shopee/emitir-nfe/<order_sn>")
@require_role("admin", "supervisor", "operator")
def shopee_emitir_nfe(order_sn):
    """Emite NF-e + imprime etiqueta Shopee para um pedido. order_sn = shopee_order_sn."""
    try:
        from services.shopee_nfe_service import ShopeeNFeService
        svc    = ShopeeNFeService(headless=True)
        result = svc.fluxo_completo(order_sn)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "erro": str(e)}), 500


# ─── POST /api/shopee/etiqueta/<order_sn> ─────────────────────────────────────
@api_routes.post("/shopee/etiqueta/<order_sn>")
@require_role("admin", "supervisor", "operator")
def shopee_etiqueta(order_sn):
    """Só imprime etiqueta Shopee (quando NF-e já foi emitida)."""
    try:
        from services.shopee_nfe_service import ShopeeNFeService
        svc    = ShopeeNFeService(headless=True)
        result = svc.imprimir_etiqueta_shopee(order_sn)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "erro": str(e)}), 500


# ─── GET /api/pedidos-hoje ────────────────────────────────────────────────────
@api_routes.get("/pedidos-hoje")
def pedidos_hoje():
    """
    Pedidos que precisam sair hoje: PENDENTE ou SEPARADO das últimas 24h
    (mais qualquer PENDENTE independente da data).

    Resposta:
    {
      "total": 42,
      "ml_flex": 18, "ml_coleta": 15, "shopee": 9, "comercial": 0,
      "detalhes": [{ "pedido_id", "ml_id", "canal", "status", "criado_em", "itens": [] }]
    }
    """
    try:
        from datetime import timedelta

        db  = get_db()
        now = datetime.now()
        h24 = now - timedelta(hours=24)

        # Contagens por canal/logística via query dedicada
        counts = db.execute("""
            SELECT
                origem,
                logistic_type,
                COUNT(*) AS cnt
            FROM pedidos
            WHERE status IN ('PENDENTE', 'SEPARADO')
              AND (criado_em >= ? OR status = 'PENDENTE')
            GROUP BY origem, logistic_type
        """, (h24,)).fetchall()

        ml_flex = ml_coleta = shopee = comercial = 0
        for c in counts:
            orig = c["origem"] or "ML"
            lt   = (c["logistic_type"] or "").lower()
            n    = int(c["cnt"] or 0)
            if orig == "SHOPEE":
                shopee    += n
            elif orig == "COMERCIAL":
                comercial += n
            elif lt == "self_service":
                ml_flex   += n
            elif lt in ("cross_docking", "drop_off", "xd_drop_off"):
                ml_coleta += n
            else:
                ml_flex   += n  # ML sem logistic conhecido vai pra flex

        # Detalhes com itens
        rows = db.execute("""
            SELECT
                p.id            AS pedido_id,
                p.ml_id,
                p.origem        AS canal,
                p.status,
                p.criado_em,
                pr.sku,
                pr.nome,
                pp.quantidade,
                pp.quantidade_bipada
            FROM pedidos p
            LEFT JOIN pedido_produtos pp ON pp.pedido_id = p.id
            LEFT JOIN produtos pr        ON pr.id = pp.produto_id
            WHERE p.status IN ('PENDENTE', 'SEPARADO')
              AND (p.criado_em >= ? OR p.status = 'PENDENTE')
            ORDER BY p.criado_em DESC, p.id DESC
        """, (h24,)).fetchall()
        db.close()

        pedidos: dict[int, dict] = {}
        for r in rows:
            pid = r["pedido_id"]
            if pid not in pedidos:
                pedidos[pid] = {
                    "pedido_id": pid,
                    "ml_id":     r["ml_id"],
                    "canal":     r["canal"] or "ML",
                    "status":    r["status"],
                    "criado_em": str(r["criado_em"] or ""),
                    "itens":     [],
                }
            if r["sku"]:
                pedidos[pid]["itens"].append({
                    "sku":               r["sku"],
                    "nome":              r["nome"] or r["sku"],
                    "quantidade":        int(r["quantidade"]       or 0),
                    "quantidade_bipada": int(r["quantidade_bipada"] or 0),
                })

        lista = list(pedidos.values())
        return jsonify({
            "total":     len(lista),
            "ml_flex":   ml_flex,
            "ml_coleta": ml_coleta,
            "shopee":    shopee,
            "comercial": comercial,
            "detalhes":  lista,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── POST /api/sync/vendas-internas ──────────────────────────────────────────
@api_routes.post("/sync/vendas-internas")
@require_role("admin", "supervisor", "operator")
def sync_vendas_internas():
    """
    Importa Pedidos de Venda do Bling que nao sao do Mercado Livre.
    Insere/atualiza na tabela pedidos com origem='COMERCIAL'.

    Body (opcional): { "dias": 7 }
    Resposta: { "success": true, "importados": 3, "ignorados": 1 }
    """
    try:
        from services.bling_service import BlingService
        from services.estrutura_service import EstruturaService

        body = request.get_json(silent=True) or {}
        dias = int(body.get("dias") or 30)   # padrão 30 dias

        bling  = BlingService()
        vendas = bling.buscar_vendas_internas(dias=dias)

        if not vendas:
            return jsonify({"success": True, "importados": 0, "ignorados": 0,
                            "message": "Nenhuma venda interna encontrada."})

        estrutura_service = EstruturaService()
        db  = get_db()
        cur = db.cursor()

        # detecta colunas opcionais
        cols = {c["column_name"] for c in cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='pedidos'"
        ).fetchall()} if False else set()  # SQLite nao tem information_schema; usa PRAGMA

        importados = 0
        ignorados  = 0

        for venda in vendas:
            ml_id         = venda["ml_id"]
            bling_numero  = venda.get("bling_numero")
            produtos      = venda.get("produtos") or []

            if not produtos:
                ignorados += 1
                continue

            try:
                # Idempotência: pula se o número do PV Bling já foi importado
                if bling_numero:
                    dup = cur.execute(
                        "SELECT id FROM pedidos WHERE bling_numero = ?", (bling_numero,)
                    ).fetchone()
                    if dup:
                        ignorados += 1
                        continue

                cur.execute("""
                    INSERT INTO pedidos (
                        ml_id, cliente_nome, logistic_type, origem,
                        bling_pedido_venda_id, bling_numero, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDENTE')
                    ON CONFLICT(ml_id) DO UPDATE SET
                        cliente_nome          = excluded.cliente_nome,
                        logistic_type         = excluded.logistic_type,
                        origem                = excluded.origem,
                        bling_pedido_venda_id = excluded.bling_pedido_venda_id,
                        bling_numero          = excluded.bling_numero,
                        atualizado_em         = CURRENT_TIMESTAMP
                """, (
                    ml_id,
                    venda["cliente_nome"],
                    venda["logistic_type"],
                    venda["origem"],
                    venda["bling_pedido_venda_id"],
                    bling_numero,
                ))

                pedido_id = cur.execute(
                    "SELECT id FROM pedidos WHERE ml_id = ?", (ml_id,)
                ).fetchone()["id"]

                for prod in produtos:
                    sku  = prod["sku"]
                    nome = prod["nome"]
                    qtd  = prod["quantidade"]

                    # garante produto na tabela
                    cur.execute("""
                        INSERT INTO produtos (sku, nome, estoque, localizacao)
                        VALUES (?, ?, 0, 'Geral')
                        ON CONFLICT(sku) DO NOTHING
                    """, (sku, nome))

                    produto_id = cur.execute(
                        "SELECT id FROM produtos WHERE sku = ?", (sku,)
                    ).fetchone()["id"]

                    # expande kit se necessario
                    kit = estrutura_service.obter_estrutura(sku, nome)
                    if kit:
                        for comp in kit:
                            c_sku  = comp["sku"]
                            c_nome = comp["nome"]
                            c_qtd  = comp["quantidade"] * qtd
                            cur.execute("""
                                INSERT INTO produtos (sku, nome, estoque, localizacao)
                                VALUES (?, ?, 0, 'Geral')
                                ON CONFLICT(sku) DO NOTHING
                            """, (c_sku, c_nome))
                            c_id = cur.execute(
                                "SELECT id FROM produtos WHERE sku = ?", (c_sku,)
                            ).fetchone()["id"]
                            cur.execute("""
                                INSERT INTO pedido_produtos
                                    (pedido_id, produto_id, quantidade, parent_sku, parent_nome)
                                VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT DO NOTHING
                            """, (pedido_id, c_id, c_qtd, sku, nome))
                    else:
                        cur.execute("""
                            INSERT INTO pedido_produtos
                                (pedido_id, produto_id, quantidade)
                            VALUES (?, ?, ?)
                            ON CONFLICT DO NOTHING
                        """, (pedido_id, produto_id, qtd))

                importados += 1

            except Exception as e:
                traceback.print_exc()
                ignorados += 1
                print(f"[COMERCIAL] Erro ao importar {ml_id}: {e}", flush=True)

        db.commit()
        db.close()

        return jsonify({"success": True, "importados": importados, "ignorados": ignorados})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ─── POST /api/cotar-frete ───────────────────────────────────────────────────
@api_routes.post("/cotar-frete")
@require_role("admin", "supervisor", "operator")
def cotar_frete():
    """
    Cota frete via Melhor Envio para uma lista de SKUs e um CEP destino.

    Body: { "cep_destino": "30140071", "itens": [{ "sku": "ABC", "quantidade": 1 }] }
    Resposta: { "cotacoes": [{ carrier_nome, servico, preco, prazo_dias, erro }] }
    """
    try:
        from services.melhor_envio_service import MelhorEnvioService

        body        = request.get_json(silent=True) or {}
        cep_destino = str(body.get("cep_destino") or "").replace("-", "").strip()
        itens_req   = body.get("itens") or []

        if not cep_destino or len(cep_destino) != 8:
            return jsonify({"error": "cep_destino invalido (8 digitos)"}), 400
        if not itens_req:
            return jsonify({"error": "itens obrigatorio"}), 400

        db   = get_db()
        skus = [str(i.get("sku") or "").strip() for i in itens_req if i.get("sku")]
        qtds = {str(i.get("sku") or "").strip(): int(i.get("quantidade") or 1) for i in itens_req}

        if not skus:
            return jsonify({"error": "Nenhum SKU informado"}), 400

        ph   = ",".join("?" * len(skus))
        rows = db.execute(
            f"SELECT sku, nome, peso_kg, altura_cm, largura_cm, comprimento_cm FROM produtos WHERE sku IN ({ph})",
            tuple(skus),
        ).fetchall()
        db.close()

        # Valida que todos os SKUs foram encontrados e têm dimensões
        found_skus = {r["sku"] for r in rows}
        missing = [s for s in skus if s not in found_skus]
        if missing:
            db.close()
            return jsonify({
                "error": f"SKU(s) não encontrado(s) no banco: {', '.join(missing)}. "
                         "Rode 'Sync Dimensões' na tela de Cotação."
            }), 400

        sem_dims = [
            r["sku"] for r in rows
            if not any([r["peso_kg"], r["altura_cm"], r["largura_cm"], r["comprimento_cm"]])
        ]
        if sem_dims:
            db.close()
            return jsonify({
                "error": f"SKU(s) sem dimensões preenchidas: {', '.join(sem_dims)}. "
                         "Rode 'Sync Dimensões' na tela de Cotação."
            }), 400

        produtos = []
        for r in rows:
            sku = r["sku"]
            produtos.append({
                "sku":            sku,
                "nome":           r["nome"],
                "peso_kg":        r["peso_kg"],
                "altura_cm":      r["altura_cm"],
                "largura_cm":     r["largura_cm"],
                "comprimento_cm": r["comprimento_cm"],
                "quantidade":     qtds.get(sku, 1),
            })

        me       = MelhorEnvioService()
        cotacoes = me.cotar_frete(cep_destino, produtos)

        return jsonify({"cotacoes": cotacoes})

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── POST /api/sync/produtos ─────────────────────────────────────────────────
@api_routes.post("/sync/produtos")
@require_role("admin", "supervisor")
def sync_produtos_dimensoes():
    """
    Busca dimensoes de produto no Bling e atualiza a tabela local.

    Body (opcional): { "skus": ["SKU1", "SKU2"], "limit": 50 }
    Resposta: { "atualizados": N, "sem_dimensoes": M, "erros": K }
    """
    try:
        from services.bling_service import BlingService

        body  = request.get_json(silent=True) or {}
        skus  = body.get("skus") or None
        limit = int(body.get("limit") or 50)

        bling  = BlingService()
        result = bling.sync_produtos_dimensoes(skus=skus, limit=limit)

        return jsonify({"success": True, **result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET /api/metricas ───────────────────────────────────────────────────────
@api_routes.get("/metricas")
@require_role("admin", "supervisor")
def metricas():
    """
    Painel de metricas operacionais.

    Resposta:
    {
      "resumo": {
        "hoje":    { "pedidos": N, "itens_bipados": N, "ml": N, "comercial": N },
        "semana":  { "pedidos": N, "itens_bipados": N },
        "pendentes": N
      },
      "por_hora":   [{ "hora": "09", "pedidos": N, "itens": N }],
      "por_status": [{ "status": "CONCLUIDO", "total": N }],
      "por_origem": [{ "origem": "ML", "total": N }],
      "audit_recente": [{ "action", "success", "created_at", "duration_ms" }]
    }
    """
    try:
        db = get_db()

        # ── Resumo hoje ──────────────────────────────────────────────────────
        hoje = db.execute("""
            SELECT
                COUNT(*) FILTER (WHERE DATE(atualizado_em) = CURRENT_DATE
                    AND status IN ('CONCLUIDO','SEPARADO','ARQUIVADO'))  AS pedidos_hoje,
                COUNT(*) FILTER (WHERE DATE(atualizado_em) = CURRENT_DATE
                    AND status IN ('CONCLUIDO','SEPARADO','ARQUIVADO')
                    AND origem = 'ML')                                   AS ml_hoje,
                COUNT(*) FILTER (WHERE DATE(atualizado_em) = CURRENT_DATE
                    AND status IN ('CONCLUIDO','SEPARADO','ARQUIVADO')
                    AND origem = 'COMERCIAL')                            AS comercial_hoje,
                COUNT(*) FILTER (WHERE status = 'PENDENTE')              AS pendentes
            FROM pedidos
        """).fetchone()

        itens_hoje = db.execute("""
            SELECT COALESCE(SUM(pp.quantidade_bipada), 0) AS total
            FROM pedido_produtos pp
            JOIN pedidos p ON p.id = pp.pedido_id
            WHERE DATE(p.atualizado_em) = CURRENT_DATE
              AND p.status IN ('CONCLUIDO','SEPARADO','ARQUIVADO')
        """).fetchone()

        # ── Resumo semana (7 dias) ───────────────────────────────────────────
        semana = db.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status IN ('CONCLUIDO','SEPARADO','ARQUIVADO')) AS pedidos,
                (SELECT COALESCE(SUM(pp2.quantidade_bipada), 0)
                 FROM pedido_produtos pp2
                 JOIN pedidos p2 ON p2.id = pp2.pedido_id
                 WHERE p2.atualizado_em >= NOW() - INTERVAL '7 days'
                   AND p2.status IN ('CONCLUIDO','SEPARADO','ARQUIVADO'))               AS itens
            FROM pedidos
            WHERE atualizado_em >= NOW() - INTERVAL '7 days'
        """).fetchone()

        # ── Por hora (hoje) ──────────────────────────────────────────────────
        por_hora_rows = db.execute("""
            SELECT
                LPAD(EXTRACT(HOUR FROM atualizado_em)::TEXT, 2, '0') AS hora,
                COUNT(*)                                               AS pedidos,
                COALESCE(SUM(sub.bips), 0)                            AS itens
            FROM pedidos p
            LEFT JOIN (
                SELECT pedido_id, SUM(quantidade_bipada) AS bips
                FROM pedido_produtos
                GROUP BY pedido_id
            ) sub ON sub.pedido_id = p.id
            WHERE DATE(p.atualizado_em) = CURRENT_DATE
              AND p.status IN ('CONCLUIDO','SEPARADO','ARQUIVADO')
            GROUP BY 1
            ORDER BY 1
        """).fetchall()

        # ── Por status ───────────────────────────────────────────────────────
        por_status_rows = db.execute("""
            SELECT status, COUNT(*) AS total
            FROM pedidos
            GROUP BY status
            ORDER BY total DESC
        """).fetchall()

        # ── Por origem ───────────────────────────────────────────────────────
        por_origem_rows = db.execute("""
            SELECT origem, COUNT(*) AS total
            FROM pedidos
            GROUP BY origem
            ORDER BY total DESC
        """).fetchall()

        # ── Audit log recente ────────────────────────────────────────────────
        audit_rows = db.execute("""
            SELECT a.action, a.success, a.created_at, a.duration_ms,
                   u.username
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.created_at DESC
            LIMIT 20
        """).fetchall()

        db.close()

        return jsonify({
            "resumo": {
                "hoje": {
                    "pedidos":      int(hoje["pedidos_hoje"] or 0),
                    "itens_bipados": int(itens_hoje["total"] or 0),
                    "ml":           int(hoje["ml_hoje"] or 0),
                    "comercial":    int(hoje["comercial_hoje"] or 0),
                },
                "semana": {
                    "pedidos":      int(semana["pedidos"] or 0),
                    "itens_bipados": int(semana["itens"] or 0),
                },
                "pendentes": int(hoje["pendentes"] or 0),
            },
            "por_hora":   [
                {"hora": r["hora"], "pedidos": int(r["pedidos"]), "itens": int(r["itens"])}
                for r in por_hora_rows
            ],
            "por_status": [
                {"status": r["status"], "total": int(r["total"])}
                for r in por_status_rows
            ],
            "por_origem": [
                {"origem": r["origem"] or "ML", "total": int(r["total"])}
                for r in por_origem_rows
            ],
            "audit_recente": [
                {
                    "action":      r["action"],
                    "success":     bool(r["success"]),
                    "created_at":  str(r["created_at"] or ""),
                    "duration_ms": r["duration_ms"],
                    "username":    r["username"] or "—",
                }
                for r in audit_rows
            ],
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── GET /api/painel-tv ──────────────────────────────────────────────────────
@api_routes.get("/painel-tv")
def painel_tv():
    """
    Dashboard operacional consolidado para TV de expedição.
    Retorna operação + ranking da equipe (sessionização) + progresso do dia.
    """
    import os
    from flask_login import current_user
    internal_token = request.headers.get("X-Internal-Token", "")
    expected       = os.getenv("INTERNAL_API_TOKEN", "")
    if not (internal_token and expected and internal_token == expected):
        if not current_user.is_authenticated or current_user.role not in ("admin", "supervisor"):
            return jsonify({"error": "unauthorized"}), 401
    from collections import defaultdict
    from services.metrics_service import calcular_sessoes, status_usuario, minutos_atras

    def _fh(ts):
        if ts is None:
            return "--:--"
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            from datetime import timezone as _tz
            ts = ts.astimezone(_tz.utc).replace(tzinfo=None)
        return ts.strftime("%H:%M")

    try:
        db  = get_db()
        now = datetime.now()

        # ── OPERAÇÃO ──────────────────────────────────────────────────────────
        op = db.execute("""
            SELECT
              COUNT(*) FILTER (WHERE status IN ('PENDENTE','PARCIAL'))                           AS pendentes,
              COUNT(*) FILTER (WHERE status IN ('PENDENTE','PARCIAL') AND origem = 'ML')         AS pend_ml,
              COUNT(*) FILTER (WHERE status IN ('PENDENTE','PARCIAL') AND origem = 'COMERCIAL')  AS pend_com,
              COUNT(*) FILTER (WHERE status IN ('PENDENTE','PARCIAL') AND origem = 'SHOPEE')     AS pend_shopee,
              COUNT(*) FILTER (WHERE status = 'SEPARADO')                                        AS separados,
              COUNT(*) FILTER (WHERE status = 'CONCLUIDO'
                               AND DATE(atualizado_em) = CURRENT_DATE)                           AS concluidos
            FROM pedidos
        """).fetchone()

        itens_pend = db.execute("""
            SELECT COALESCE(SUM(pp.quantidade - pp.quantidade_bipada), 0) AS cnt
            FROM pedido_produtos pp
            JOIN pedidos p ON p.id = pp.pedido_id
            WHERE p.status IN ('PENDENTE','PARCIAL')
        """).fetchone()

        # COMERCIAL: horas desde o pedido mais antigo ainda pendente
        com_pz = db.execute("""
            SELECT EXTRACT(EPOCH FROM (NOW() - MIN(criado_em)))/3600 AS horas
            FROM pedidos
            WHERE LOWER(origem) = 'comercial'
              AND status IN ('PENDENTE','SEPARADO')
        """).fetchone()

        # SHOPEE: prazo mais próximo (ship_by_date) entre pedidos pendentes
        shopee_pz = db.execute("""
            SELECT MIN(ship_by_date)                                    AS proximo_prazo,
                   EXTRACT(EPOCH FROM (MIN(ship_by_date) - NOW()))/60  AS minutos
            FROM pedidos
            WHERE LOWER(origem) = 'shopee'
              AND status IN ('PENDENTE','SEPARADO')
              AND ship_by_date IS NOT NULL
        """).fetchone()

        # ML: prazo de envio mais próximo (lead_time do shipment)
        ml_pz = db.execute("""
            SELECT MIN(prazo_envio)                                    AS proximo_prazo,
                   EXTRACT(EPOCH FROM (MIN(prazo_envio) - NOW()))/60  AS minutos
            FROM pedidos
            WHERE LOWER(origem) = 'ml'
              AND status IN ('PENDENTE','SEPARADO')
              AND prazo_envio IS NOT NULL
        """).fetchone()

        pendentes    = int(op["pendentes"]    or 0)
        pend_ml      = int(op["pend_ml"]      or 0)
        pend_com     = int(op["pend_com"]     or 0)
        pend_shopee  = int(op["pend_shopee"]  or 0)
        separados    = int(op["separados"]    or 0)
        concluidos   = int(op["concluidos"]   or 0)
        itens_tot    = int(itens_pend["cnt"]  or 0)

        # ── EQUIPE — audit_log de hoje ────────────────────────────────────────
        rows = db.execute("""
            SELECT u.id, u.full_name, u.username, a.created_at
            FROM audit_log a
            JOIN users u ON u.id = a.user_id
            WHERE a.action IN ('bipar', 'login')
              AND a.success = true
              AND DATE(a.created_at) = CURRENT_DATE
            ORDER BY u.id, a.created_at
        """).fetchall()

        bip_rows = db.execute("""
            SELECT user_id, COUNT(*) AS cnt
            FROM audit_log
            WHERE action = 'bipar' AND success = true
              AND DATE(created_at) = CURRENT_DATE
            GROUP BY user_id
        """).fetchall()
        bip_map = {r["user_id"]: int(r["cnt"]) for r in bip_rows}

        por_user:  defaultdict = defaultdict(list)
        nomes:     dict        = {}
        usernames: dict        = {}
        for r in rows:
            por_user[r["id"]].append(r["created_at"])
            nomes[r["id"]]     = r["full_name"] or r["username"]
            usernames[r["id"]] = r["username"]

        equipe = []
        for uid, eventos in por_user.items():
            sessoes     = calcular_sessoes(eventos)
            tempo_total = round(sum(s["duracao_min"] for s in sessoes), 1)
            ultima      = max(eventos)
            bips        = bip_map.get(uid, 0)
            ritmo       = round(bips / tempo_total, 2) if tempo_total > 0 else 0
            equipe.append({
                "usuario":              nomes[uid],
                "username":             usernames[uid],
                "status":               status_usuario(ultima),
                "bipagens":             bips,
                "tempo_trabalhado_min": tempo_total,
                "ritmo":                ritmo,
                "primeira_atividade":   _fh(min(eventos)),
                "ultima_atividade":     _fh(ultima),
                "minutos_parado":       round(minutos_atras(ultima), 1),
            })

        equipe.sort(key=lambda x: x["ritmo"], reverse=True)

        # ── PROGRESSO ─────────────────────────────────────────────────────────
        total_dia = pendentes + separados + concluidos
        pct       = round(concluidos / total_dia * 100) if total_dia > 0 else 0

        def _iso(row, col):
            v = row[col] if row else None
            return v.isoformat() if v and hasattr(v, "isoformat") else None

        def _float(row, col):
            v = row[col] if row else None
            return round(float(v), 1) if v is not None else None

        # ── POR CONTA — ML (LIKE usa %% para escapar % no psycopg2) ──────────
        ml_por_conta = db.execute("""
            SELECT
                COALESCE(conta_nome,'MEDICALD 1')                               AS nome,
                COUNT(*)                                                       AS total,
                COUNT(*) FILTER (WHERE UPPER(logistic_type) LIKE '%%FLEX%%'
                                    OR UPPER(logistic_type) LIKE '%%SELF%%')  AS flex,
                COUNT(*) FILTER (WHERE UPPER(logistic_type) LIKE '%%COLETA%%'
                                    OR UPPER(logistic_type) LIKE '%%AGENCIA%%') AS coleta,
                MIN(prazo_envio)                                               AS proximo_prazo,
                EXTRACT(EPOCH FROM (MIN(prazo_envio) - NOW()))/60             AS minutos
            FROM pedidos
            WHERE LOWER(origem) = 'ml'
              AND status IN ('PENDENTE','SEPARADO')
            GROUP BY COALESCE(conta_nome,'MEDICALD 1')
            ORDER BY nome
        """).fetchall()

        # ── POR CONTA — SHOPEE ────────────────────────────────────────────────
        shopee_por_conta = db.execute("""
            SELECT
                COALESCE(conta_nome,'MEDICALD 1')                              AS nome,
                COUNT(*)                                                      AS total,
                MIN(ship_by_date)                                             AS proximo_prazo,
                EXTRACT(EPOCH FROM (MIN(ship_by_date) - NOW()))/60           AS minutos
            FROM pedidos
            WHERE LOWER(origem) = 'shopee'
              AND status IN ('PENDENTE','SEPARADO')
            GROUP BY COALESCE(conta_nome,'MEDICALD 1')
            ORDER BY nome
        """).fetchall()

        db.close()

        def _conta_ml(row):
            return {
                "nome":           row["nome"],
                "total":          int(row["total"]  or 0),
                "flex":           int(row["flex"]   or 0),
                "coleta":         int(row["coleta"] or 0),
                "prazo":          _iso(row, "proximo_prazo"),
                "prazo_minutos":  _float(row, "minutos"),
                "alerta":         (row["minutos"] is not None and float(row["minutos"]) < 60),
            }

        def _conta_shopee(row):
            return {
                "nome":              row["nome"],
                "total":             int(row["total"] or 0),
                "proximo_prazo":     _iso(row, "proximo_prazo"),
                "minutos_restantes": _float(row, "minutos"),
            }

        return jsonify({
            # Totais globais (retrocompat)
            "operacao": {
                "pendentes":           pendentes,
                "pendentes_ml":        pend_ml,
                "pendentes_comercial": pend_com,
                "pendentes_shopee":    pend_shopee,
                "separados":           separados,
                "concluidos_hoje":     concluidos,
                "itens_pendentes":     itens_tot,
                "comercial_mais_antigo_horas": _float(com_pz, "horas"),
                "shopee_ship_by":              _iso(shopee_pz, "proximo_prazo"),
                "shopee_minutos":              _float(shopee_pz, "minutos"),
                "ml_prazo":                    _iso(ml_pz, "proximo_prazo"),
                "ml_prazo_minutos":            _float(ml_pz, "minutos"),
            },
            # Dados multi-conta
            "totais": {
                "pendentes":      pendentes,
                "separados":      separados,
                "concluidos_hoje": concluidos,
                "itens_pendentes": itens_tot,
            },
            "contas_ml":     [_conta_ml(r)     for r in ml_por_conta],
            "contas_shopee": [_conta_shopee(r) for r in shopee_por_conta],
            "comercial": {
                "total":              pend_com,
                "mais_antigo_horas":  _float(com_pz, "horas"),
            },
            "equipe":    equipe,
            "progresso": {"concluidos": concluidos, "total": total_dia, "pct": pct},
            "gerado_em": now.isoformat(),
        })

    except Exception:
        traceback.print_exc()
        # Retorna zeros em vez de 500 — painel TV nunca deve travar por falha pontual
        return jsonify({
            "operacao": {
                "pendentes": 0, "pendentes_ml": 0, "pendentes_comercial": 0,
                "pendentes_shopee": 0, "separados": 0, "concluidos_hoje": 0,
                "itens_pendentes": 0,
            },
            "equipe":    [],
            "progresso": {"concluidos": 0, "total": 0, "pct": 0},
            "gerado_em": datetime.now().isoformat(),
        }), 200


# ─── GET /api/admin/usuarios ─────────────────────────────────────────────────
@api_routes.get("/admin/usuarios")
@require_role("admin")
def admin_lista_usuarios():
    from services.user_service import list_users
    users = list_users()
    safe  = [{k: v for k, v in u.items() if k != "password_hash"} for u in users]
    return jsonify({"users": safe})


# ─── PUT /api/admin/usuarios/<id>/role ───────────────────────────────────────
@api_routes.put("/admin/usuarios/<int:user_id>/role")
@require_role("admin")
def admin_change_role(user_id: int):
    from flask_login import current_user
    from services.user_service import update_user

    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip()
    if role not in ("operator", "supervisor", "admin"):
        return jsonify({"error": "role deve ser operator, supervisor ou admin"}), 400

    if user_id == current_user.id and role != "admin":
        return jsonify({"error": "Não é possível rebaixar sua própria conta"}), 400

    ok = update_user(user_id, role=role)
    if not ok:
        return jsonify({"error": "Usuário não encontrado"}), 404
    return jsonify({"ok": True})


# ─── PUT /api/admin/usuarios/<id>/status ─────────────────────────────────────
@api_routes.put("/admin/usuarios/<int:user_id>/status")
@require_role("admin")
def admin_change_status(user_id: int):
    from flask_login import current_user
    from services.user_service import update_user

    data  = request.get_json(silent=True) or {}
    ativo = data.get("ativo")
    if ativo is None:
        return jsonify({"error": "campo 'ativo' obrigatório"}), 400

    if user_id == current_user.id and not ativo:
        return jsonify({"error": "Não é possível desativar sua própria conta"}), 400

    ok = update_user(user_id, active=bool(ativo))
    if not ok:
        return jsonify({"error": "Usuário não encontrado"}), 404
    return jsonify({"ok": True})


# ─── Helpers de rastreabilidade ──────────────────────────────────────────────

def _parse_meta(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        import json as _json
        return _json.loads(raw)
    except Exception:
        return {}


def _fmt_ts(ts):
    """Retorna (data_str, horario_str) no horário local (sem converter para UTC)."""
    if ts is None:
        return "", ""
    if isinstance(ts, str):
        s = ts.replace("T", " ")
        return s[:10], (s[11:19] if len(s) > 10 else "")
    try:
        # Strip tzinfo sem converter — mantém o horário local como armazenado
        ts_naive = ts.replace(tzinfo=None)
        return ts_naive.strftime("%Y-%m-%d"), ts_naive.strftime("%H:%M:%S")
    except Exception:
        s = str(ts)
        return s[:10], s[11:19]


def _date_range(args):
    """Retorna (from_dt, to_dt) dos query params ?from= e ?to=."""
    from datetime import timedelta
    hoje = date.today()
    fs, ts_ = args.get('from', ''), args.get('to', '')
    try:
        from_dt = datetime.strptime(fs, '%Y-%m-%d') if fs else datetime.combine(hoje, datetime.min.time())
    except ValueError:
        from_dt = datetime.combine(hoje, datetime.min.time())
    try:
        to_dt = datetime.strptime(ts_, '%Y-%m-%d') + timedelta(days=1) if ts_ else datetime.combine(hoje + timedelta(days=1), datetime.min.time())
    except ValueError:
        to_dt = datetime.combine(hoje + timedelta(days=1), datetime.min.time())
    return from_dt, to_dt


# ─── GET /api/rastreabilidade/pedido/<ml_id> ─────────────────────────────────
@api_routes.get("/rastreabilidade/pedido/<ml_id>")
@require_role("admin", "supervisor")
def rastr_pedido(ml_id: str):
    """Todas as bipagens de um pedido específico."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT a.id, u.full_name, u.username, a.metadata, a.created_at, a.success
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.action = 'bipar' AND a.entity_id = ?
            ORDER BY a.created_at
        """, (str(ml_id),)).fetchall()

        bipagens = []
        for r in rows:
            meta = _parse_meta(r["metadata"])
            d, h = _fmt_ts(r["created_at"])
            bipagens.append({
                "horario":     h,
                "data":        d,
                "created_at":  str(r["created_at"] or ""),
                "usuario":     r["full_name"] or r["username"] or "—",
                "username":    r["username"] or "—",
                "ml_id":       ml_id,
                "sku":         meta.get("sku", "—"),
                "nome":        meta.get("nome", "—"),
                "parent_sku":  meta.get("parent_sku"),
                "parent_nome": meta.get("parent_nome"),
                "quantidade":  meta.get("quantidade", 1),
                "success":     bool(r["success"]),
            })

        return jsonify({"ml_id": ml_id, "bipagens": bipagens, "total": len(bipagens)})

    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Erro na consulta"}), 500
    finally:
        db.close()


# ─── GET /api/rastreabilidade/usuario/<username> ─────────────────────────────
@api_routes.get("/rastreabilidade/usuario/<username>")
@require_role("admin", "supervisor")
def rastr_usuario(username: str):
    """Tudo que um operador bipou num período."""
    db = get_db()
    try:
        user_row = db.execute(
            "SELECT id, full_name, username FROM users WHERE LOWER(username) = LOWER(?)",
            (username,)
        ).fetchone()
        if not user_row:
            return jsonify({"error": f"Usuário '{username}' não encontrado"}), 404

        from_dt, to_dt = _date_range(request.args)
        rows = db.execute("""
            SELECT a.id, a.entity_id AS ml_id, a.metadata, a.created_at, a.success
            FROM audit_log a
            WHERE a.action = 'bipar' AND a.user_id = ?
              AND a.created_at >= ? AND a.created_at < ?
            ORDER BY a.created_at DESC
            LIMIT 500
        """, (user_row["id"], from_dt, to_dt)).fetchall()

        bipagens = []
        for r in rows:
            meta = _parse_meta(r["metadata"])
            d, h = _fmt_ts(r["created_at"])
            bipagens.append({
                "horario":     h,
                "data":        d,
                "created_at":  str(r["created_at"] or ""),
                "usuario":     user_row["full_name"] or user_row["username"],
                "username":    user_row["username"],
                "ml_id":       r["ml_id"] or "—",
                "sku":         meta.get("sku", "—"),
                "nome":        meta.get("nome", "—"),
                "parent_sku":  meta.get("parent_sku"),
                "parent_nome": meta.get("parent_nome"),
                "success":     bool(r["success"]),
            })

        return jsonify({
            "usuario":  user_row["full_name"] or user_row["username"],
            "username": username,
            "bipagens": bipagens,
            "total":    len(bipagens),
        })

    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Erro na consulta"}), 500
    finally:
        db.close()


# ─── GET /api/rastreabilidade/sku/<sku> ──────────────────────────────────────
@api_routes.get("/rastreabilidade/sku/<sku>")
@require_role("admin", "supervisor")
def rastr_sku(sku: str):
    """Todas as bipagens de um SKU específico num período."""
    db = get_db()
    try:
        from_dt, to_dt = _date_range(request.args)
        rows = db.execute("""
            SELECT a.id, a.entity_id AS ml_id,
                   u.full_name, u.username,
                   a.metadata, a.created_at, a.success
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.action = 'bipar'
              AND UPPER(a.metadata::jsonb->>'sku') = UPPER(?)
              AND a.created_at >= ? AND a.created_at < ?
            ORDER BY a.created_at DESC
            LIMIT 500
        """, (sku, from_dt, to_dt)).fetchall()

        bipagens = []
        for r in rows:
            meta = _parse_meta(r["metadata"])
            d, h = _fmt_ts(r["created_at"])
            bipagens.append({
                "horario":     h,
                "data":        d,
                "created_at":  str(r["created_at"] or ""),
                "usuario":     r["full_name"] or r["username"] or "—",
                "username":    r["username"] or "—",
                "ml_id":       r["ml_id"] or "—",
                "sku":         meta.get("sku", sku),
                "nome":        meta.get("nome", "—"),
                "parent_sku":  meta.get("parent_sku"),
                "parent_nome": meta.get("parent_nome"),
                "success":     bool(r["success"]),
            })

        return jsonify({"sku": sku, "bipagens": bipagens, "total": len(bipagens)})

    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Erro na consulta"}), 500
    finally:
        db.close()