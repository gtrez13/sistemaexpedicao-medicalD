import os
import re
import time
import traceback
import unicodedata
from datetime import datetime
from services.estrutura_service import EstruturaService
from services.serial_store import SerialStore

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, send_from_directory, abort, send_file

from db import get_db
from services.bling_service import BlingService
from services.ml_service import MercadoLivreService
from services.document_service import DocumentService

main_routes = Blueprint("main", __name__)

# Cache global de estruturas de kit — persiste entre chamadas de sincronizar()
# chave: "{sku}||{nome[:40]}", valor: list[dict] | None
_kit_estrutura_cache: dict[str, list | None] = {}

# ============================================================
# Helpers
# ============================================================

def _is_fetch(req):
    return req.headers.get("X-Requested-With") == "fetch"


def _tipo_envio_label(logistic_type: str) -> str:
    lt = (logistic_type or "").lower()
    if lt == "self_service":
        return "FLEX"
    if lt in ("cross_docking", "drop_off", "xd_drop_off"):
        return "COLETA/AGÊNCIA"
    return lt.upper() if lt else "PADRÃO"


def _norm_txt(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def _group_key(nome: str) -> str:
    s = _norm_txt(nome)
    s = re.sub(r"\b(tamanho|tam|nº|n\.)\b", " ", s)
    s = re.sub(r"\b\d+\b", " ", s)
    s = re.sub(r"[|:/\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = s.split()
    return " ".join(tokens[:4]) if tokens else s


def _attrs_text_ml(order_item: dict) -> str:
    item = (order_item or {}).get("item") or {}
    attrs = []
    for src in (
        order_item.get("variation_attributes"),
        item.get("variation_attributes"),
        order_item.get("attributes"),
        item.get("attributes"),
    ):
        if isinstance(src, list):
            for a in src:
                if not isinstance(a, dict):
                    continue
                name = (a.get("name") or a.get("id") or "").strip()
                val = (a.get("value_name") or a.get("value_id") or "").strip()
                if name and val:
                    attrs.append(f"{name}: {val}")

    uniq = []
    for x in attrs:
        if x not in uniq:
            uniq.append(x)

    return " | " + " / ".join(uniq[:3]) if uniq else ""


def _extrair_sku_ml(order_item: dict) -> str | None:
    if not isinstance(order_item, dict):
        return None

    v = order_item.get("seller_sku")
    if v:
        return str(v).strip()

    info = order_item.get("item") or {}
    v = info.get("seller_sku")
    if v:
        return str(v).strip()

    def pick_from_attrs(attrs):
        if not isinstance(attrs, list):
            return None
        for a in attrs:
            if not isinstance(a, dict):
                continue
            aid = (a.get("id") or "").upper()
            if aid in ("SELLER_SKU", "SELLER_CUSTOM_FIELD", "SKU"):
                vv = a.get("value_name") or a.get("value_id") or a.get("value") or a.get("name")
                if vv:
                    return str(vv).strip()
        return None

    v = pick_from_attrs(info.get("attributes")) or pick_from_attrs(info.get("variation_attributes"))
    if v:
        return v

    v = pick_from_attrs(order_item.get("variation_attributes")) or pick_from_attrs(order_item.get("attributes"))
    return v


def _sku_final_para_db(order_item: dict) -> str:
    info = order_item.get("item") or {}
    sku_ml = _extrair_sku_ml(order_item)
    if sku_ml:
        return sku_ml
    return str(info.get("id") or "SEM_SKU").strip()


def _pedido_dt(criado_em):
    if isinstance(criado_em, str):
        try:
            return datetime.strptime(criado_em.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now()
    return criado_em or datetime.now()


def _detect_unique_pp(cur) -> bool:
    try:
        idxs = cur.execute("PRAGMA index_list('pedido_produtos')").fetchall()
        for idx in idxs:
            idx_name = idx[1]
            is_unique = int(idx[2] or 0) == 1
            if not is_unique:
                continue
            cols = cur.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
            col_names = [c[2] for c in cols]
            if col_names == ["pedido_id", "produto_id"] or col_names == ["produto_id", "pedido_id"]:
                return True
    except Exception:
        pass
    return False


def _upsert_pedido_produto(cur, has_unique_composto: bool, pedido_id: int, produto_id: int,
                          quantidade: int, parent_sku=None, parent_nome=None):
    q = int(quantidade or 0)

    if has_unique_composto:
        cur.execute("""
            INSERT INTO pedido_produtos (
                pedido_id, produto_id, quantidade, quantidade_bipada, parent_sku, parent_nome
            ) VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(pedido_id, produto_id) DO UPDATE SET
                quantidade = excluded.quantidade,
                parent_sku = COALESCE(excluded.parent_sku, pedido_produtos.parent_sku),
                parent_nome = COALESCE(excluded.parent_nome, pedido_produtos.parent_nome)
        """, (pedido_id, produto_id, q, parent_sku, parent_nome))
        return

    row = cur.execute(
        "SELECT id FROM pedido_produtos WHERE pedido_id=? AND produto_id=?",
        (pedido_id, produto_id)
    ).fetchone()

    if row:
        cur.execute("""
            UPDATE pedido_produtos
               SET quantidade = ?,
                   parent_sku = COALESCE(?, parent_sku),
                   parent_nome = COALESCE(?, parent_nome)
             WHERE pedido_id = ?
               AND produto_id = ?
        """, (q, parent_sku, parent_nome, pedido_id, produto_id))
    else:
        cur.execute("""
            INSERT INTO pedido_produtos (
                pedido_id, produto_id, quantidade, quantidade_bipada,
                parent_sku, parent_nome
            ) VALUES (?, ?, ?, 0, ?, ?)
        """, (pedido_id, produto_id, q, parent_sku, parent_nome))


# ============================================================
# SKU chave (kit check otimizado)
# ============================================================

# Palavras/padrões que indicam que um SKU pode ser kit
_KIT_PATTERNS = re.compile(
    r"(kit|\bx\b|\d+x\d+|\bcombo\b|\bpack\b|\bconjunto\b|\bcombinado\b)",
    re.IGNORECASE
)

def _sku_pode_ser_kit(sku: str, nome: str) -> bool:
    """
    Retorna True se o SKU/nome indicar kit OU se o SKU for um ID numérico do ML
    (seller_sku não configurado no anúncio — tipo desconhecido, verificar sempre).
    """
    # ID numérico longo = ML item_id usado como fallback; tipo do produto desconhecido
    if sku.isdigit() and len(sku) >= 10:
        return True
    texto = f"{sku} {nome}"
    return bool(_KIT_PATTERNS.search(texto))


# ============================================================
# _load_pedidos
# ============================================================

def _load_pedidos(status: str):
    db = get_db()
    rows = db.execute("""
        SELECT
        p.id as pedido_id, p.ml_id, p.cliente_nome, p.status, p.logistic_type,
        p.criado_em, p.ml_ship_substatus, p.ml_task,
        p.observacao,
        pr.sku as sku, pr.nome as nome, pr.estoque_bling as estoque_bling,
        pp.quantidade as quantidade, pp.quantidade_bipada as quantidade_bipada,
        pp.parent_sku as parent_sku, pp.parent_nome as parent_nome
        FROM pedidos p
        LEFT JOIN pedido_produtos pp ON pp.pedido_id = p.id
        LEFT JOIN produtos pr ON pr.id = pp.produto_id
        WHERE p.status = ?
        ORDER BY p.criado_em DESC, p.id DESC
    """, (status,)).fetchall()

    pedidos = {}
    for r in rows:
        pid = r["pedido_id"]

        if pid not in pedidos:
            pedidos[pid] = {
                "id": pid,
                "ml_id": r["ml_id"],
                "cliente_nome": r["cliente_nome"],
                "status": r["status"],
                "logistic_type": r["logistic_type"],
                "criado_em": _pedido_dt(r["criado_em"]),
                "ml_ship_substatus": r["ml_ship_substatus"],
                "ml_task": r["ml_task"],
                "observacao": r["observacao"] or "",
                "envio_label": _tipo_envio_label(r["logistic_type"]),
                "itens": [],
                "total_quantidade": 0,
                "total_bipado": 0,
            }

        if r["sku"] is not None:
            it = {
                "sku": r["sku"],
                "nome": r["nome"] or "Produto",
                "estoque_bling": r["estoque_bling"],
                "quantidade": int(r["quantidade"] or 0),
                "quantidade_bipada": int(r["quantidade_bipada"] or 0),
                "parent_sku": r["parent_sku"],
                "parent_nome": r["parent_nome"],
            }
            it["saldo"] = it["estoque_bling"]
            it["estoque"] = it["estoque_bling"]
            it["estoque_atual"] = it["estoque_bling"]
            it["bling_estoque"] = it["estoque_bling"]
            it["estoqueAtual"] = it["estoque_bling"]

            pedidos[pid]["itens"].append(it)
            pedidos[pid]["total_quantidade"] += it["quantidade"]
            pedidos[pid]["total_bipado"] += it["quantidade_bipada"]

    out = list(pedidos.values())
    db.close()
    return out


def _calc_stats(pedidos):
    total_itens = sum(int(p.get("total_quantidade") or 0) for p in pedidos)
    total_bip = sum(int(p.get("total_bipado") or 0) for p in pedidos)
    return {
        "pendentes": len(pedidos),
        "total_itens": total_itens,
        "total_bipado": total_bip,
        "progresso_geral_pct": int((total_bip / total_itens) * 100) if total_itens else 0
    }


# ============================================================
# TELA 1 - EXPEDIÇÃO
# ============================================================

#@main_routes.route("/")
#def index():
    pedidos = _load_pedidos("PENDENTE")
    for p in pedidos:
        tq = int(p.get("total_quantidade") or 0)
        tb = int(p.get("total_bipado") or 0)
        prog = int((tb / tq) * 100) if tq else 0
        p["progresso_pct"] = prog
        p["progresso_bar"] = f"width: {prog}%"

    for p in pedidos:
        if p["itens"]:
            p["sort_key"] = _group_key(p["itens"][0]["nome"])
        else:
            p["sort_key"] = "zzz"

    pedidos.sort(key=lambda x: x["sort_key"])

    stats = _calc_stats(pedidos)
    stats["concluidos"] = sum(1 for p in pedidos if int(p.get("progresso_pct") or 0) >= 100)
    return render_template("index.html", pedidos=pedidos, stats=stats)


@main_routes.route("/pedido/<ml_id>/json")
def pedido_json(ml_id):
    db = get_db()
    p = db.execute("SELECT id, ml_id FROM pedidos WHERE ml_id=? LIMIT 1", (str(ml_id),)).fetchone()
    if not p:
        db.close()
        return jsonify({"success": False}), 404

    itens = db.execute("""
      SELECT pr.sku, pr.nome, pr.estoque_bling,
             pp.quantidade, pp.quantidade_bipada,
             pp.parent_sku, pp.parent_nome
      FROM pedido_produtos pp
      JOIN produtos pr ON pr.id = pp.produto_id
      WHERE pp.pedido_id = ?
    """, (p["id"],)).fetchall()

    itens_list = [dict(i) for i in itens]
    tot = sum(int(i.get("quantidade") or 0) for i in itens_list)
    bip = sum(int(i.get("quantidade_bipada") or 0) for i in itens_list)
    db.close()

    return jsonify({
        "success": True,
        "ml_id": p["ml_id"],
        "total_quantidade": tot,
        "total_bipado": bip,
        "itens": itens_list
    })


# ============================================================
# ARQUIVAR / DESARQUIVAR / OBSERVAÇÃO
# ============================================================

@main_routes.route("/pedido/<ml_id>/arquivar", methods=["POST"])
def arquivar_pedido(ml_id):
    data = request.get_json(silent=True) or {}
    observacao = (data.get("observacao") or "").strip()

    db = get_db()
    cur = db.execute("""
        UPDATE pedidos
        SET status='ARQUIVADO',
            observacao=?,
            arquivado_em=CURRENT_TIMESTAMP,
            atualizado_em=CURRENT_TIMESTAMP
        WHERE ml_id=?
          AND status IN ('PENDENTE','PARCIAL')
    """, (observacao or None, ml_id))
    db.commit()
    db.close()

    if cur.rowcount == 0:
        return jsonify({"success": False, "error": "Pedido não encontrado ou status inválido"}), 404
    return jsonify({"success": True, "ml_id": ml_id})


@main_routes.route("/pedido/<ml_id>/desarquivar", methods=["POST"])
def desarquivar_pedido(ml_id):
    db = get_db()
    cur = db.execute("""
        UPDATE pedidos
        SET status='PENDENTE',
            arquivado_em=NULL,
            atualizado_em=CURRENT_TIMESTAMP
        WHERE ml_id=?
          AND status = 'ARQUIVADO'
    """, (ml_id,))
    db.commit()
    db.close()

    if cur.rowcount == 0:
        return jsonify({"success": False, "error": "Pedido não encontrado ou não está arquivado"}), 404
    return jsonify({"success": True, "ml_id": ml_id})


@main_routes.route("/pedido/<ml_id>/observacao", methods=["POST"])
def salvar_observacao(ml_id):
    data = request.get_json(silent=True) or {}
    observacao = (data.get("observacao") or "").strip()

    db = get_db()
    cur = db.execute("""
        UPDATE pedidos
        SET observacao=?,
            atualizado_em=CURRENT_TIMESTAMP
        WHERE ml_id=?
    """, (observacao or None, ml_id))
    db.commit()
    db.close()

    if cur.rowcount == 0:
        return jsonify({"success": False, "error": "Pedido não encontrado"}), 404
    return jsonify({"success": True, "ml_id": ml_id})


@main_routes.route("/arquivados")
def arquivados():
    db = get_db()
    rows = db.execute("""
        SELECT
          p.id as pedido_id, p.ml_id, p.cliente_nome, p.status, p.logistic_type,
          p.criado_em, p.arquivado_em, p.observacao,
          pr.sku as sku, pr.nome as nome,
          pp.quantidade as quantidade
        FROM pedidos p
        LEFT JOIN pedido_produtos pp ON pp.pedido_id = p.id
        LEFT JOIN produtos pr ON pr.id = pp.produto_id
        WHERE p.status = 'ARQUIVADO'
        ORDER BY p.arquivado_em DESC, p.id DESC
    """).fetchall()
    db.close()

    pedidos = {}
    for r in rows:
        pid = r["pedido_id"]
        if pid not in pedidos:
            pedidos[pid] = {
                "id": pid,
                "ml_id": r["ml_id"],
                "cliente_nome": r["cliente_nome"],
                "logistic_type": r["logistic_type"],
                "envio_label": _tipo_envio_label(r["logistic_type"]),
                "criado_em": _pedido_dt(r["criado_em"]),
                "arquivado_em": r["arquivado_em"] or "",
                "observacao": r["observacao"] or "",
                "itens": [],
            }
        if r["sku"]:
            pedidos[pid]["itens"].append({
                "sku": r["sku"],
                "nome": r["nome"] or "Produto",
                "quantidade": int(r["quantidade"] or 0),
            })

    return render_template("archived.html", pedidos=list(pedidos.values()))


# ============================================================
# SYNC (otimizado — kit só para SKUs chave)
# ============================================================


@main_routes.route("/sincronizar", methods=["POST"])
def sincronizar():
    try:
        bling = BlingService()
        _estoque_cache: dict[str, int | None] = {}

        # ── Busca contas ML ativas ───────────────────────────────────────────
        _cdb = get_db()
        try:
            ml_contas = _cdb.execute(
                "SELECT * FROM contas_marketplace WHERE plataforma='ML' AND ativo=true ORDER BY nome"
            ).fetchall()
        except Exception:
            ml_contas = []
        finally:
            _cdb.close()

        if not ml_contas:
            ml_contas = [{"id": None, "nome": "LHVMED 1",
                          "ml_access_token": None, "ml_refresh_token": None, "ml_user_id": None}]

        results = []
        for _conta in ml_contas:
            _c = dict(_conta)
            ml = MercadoLivreService(conta=_c)
            cards = ml.buscar_cards(tz_offset_hours=-3)
            flex   = cards.get("flex_ready_to_print",               []) or []
            coleta = cards.get("coleta_invoices_to_be_managed",     []) or []
            for ped in flex + coleta:
                ped["_conta_nome"] = _c["nome"]
                ped["_conta_id"]   = _c["id"]
                ped["_ml_inst"]    = ml   # instância vinculada a ESTA conta
            results.extend(flex + coleta)

        # (ml aponta para a última instância — mas usamos ped["_ml_inst"] no loop)
        if not results:
            msg = "Nenhum pedido (Imprimir etiqueta / Emitir NF-e) encontrado agora."
            if _is_fetch(request):
                return jsonify({"success": False, "message": msg}), 200
            flash("⚠️ " + msg, "warning")
            return redirect(url_for("main.index"))

        db = get_db()
        cur = db.cursor()


        cur.execute("BEGIN;")

        # ============================================================
        # Detecta colunas existentes no PostgreSQL
        # ============================================================

        cols = cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'pedidos'
        """).fetchall()

        col_names = {c["column_name"] for c in cols}

        has_ml_task     = "ml_task"              in col_names
        has_bling_pv    = "bling_pedido_venda_id" in col_names
        has_prazo_envio = "prazo_envio"            in col_names
        has_conta       = "conta_nome"             in col_names

        # ── LIMPEZA ANTI-ZUMBI ───────────────────────────────────────────────
        # Apaga todos os pedidos PENDENTE antes de importar os novos do ML.
        # Garante que pedidos cancelados/expirados no ML não fiquem eternamente
        # na fila de separação. Pedidos SEPARADO, CONCLUIDO e ARQUIVADO são
        # preservados — só PENDENTE é varrido.
        cur.execute("""
            DELETE FROM pedido_produtos
            WHERE pedido_id IN (SELECT id FROM pedidos WHERE status = 'PENDENTE')
        """)
        cur.execute("DELETE FROM pedidos WHERE status = 'PENDENTE'")

        count_pedidos = 0
        count_itens = 0

        produto_id_cache = {}
        pedido_id_cache = {}

        estrutura_service = EstruturaService()

        for ped in results:
            ml_id = str(ped.get("id") or "").strip()
            if not ml_id:
                continue

            cliente_nome = (ped.get("buyer") or {}).get("nickname", "CLIENTE")

            shipping = ped.get("shipping") or {}
            logistic_type = shipping.get("logistic_type") or "Padrão"

            ml_shipping_id    = shipping.get("ml_shipping_id") or shipping.get("id")
            ml_ship_status    = shipping.get("ml_ship_status")
            ml_ship_substatus = shipping.get("ml_ship_substatus")
            ml_task           = ped.get("ml_task")
            _conta_nome       = ped.get("_conta_nome", "LHVMED 1")
            _conta_id         = ped.get("_conta_id")

            pv_value = None

            _cnt_col = ", conta_nome, conta_id" if has_conta else ""
            _cnt_val = ", ?, ?"               if has_conta else ""
            _cnt_upd = ", conta_nome=excluded.conta_nome, conta_id=excluded.conta_id" if has_conta else ""
            _cnt_dat = [_conta_nome, _conta_id] if has_conta else []

            if has_ml_task:
                cur.execute("""
                    INSERT INTO pedidos (
                        ml_id, cliente_nome, logistic_type,
                        ml_shipping_id, ml_ship_status, ml_ship_substatus,
                        ml_task, status
                        {pv_col}{cnt_col}
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDENTE'
                        {pv_val}{cnt_val}
                    )
                    ON CONFLICT(ml_id) DO UPDATE SET
                        cliente_nome=excluded.cliente_nome,
                        logistic_type=excluded.logistic_type,
                        ml_shipping_id=excluded.ml_shipping_id,
                        ml_ship_status=excluded.ml_ship_status,
                        ml_ship_substatus=excluded.ml_ship_substatus,
                        ml_task=excluded.ml_task,
                        atualizado_em=CURRENT_TIMESTAMP
                        {pv_upd}{cnt_upd}
                """.format(
                    pv_col=", bling_pedido_venda_id" if has_bling_pv else "",
                    pv_val=", ?" if has_bling_pv else "",
                    pv_upd=", bling_pedido_venda_id=excluded.bling_pedido_venda_id" if has_bling_pv else "",
                    cnt_col=_cnt_col, cnt_val=_cnt_val, cnt_upd=_cnt_upd,
                ), tuple(
                    [ml_id, cliente_nome, logistic_type,
                     str(ml_shipping_id) if ml_shipping_id else None,
                     ml_ship_status, ml_ship_substatus, ml_task]
                    + ([pv_value] if has_bling_pv else [])
                    + _cnt_dat
                ))
            else:
                cur.execute("""
                    INSERT INTO pedidos (
                        ml_id, cliente_nome, logistic_type,
                        ml_shipping_id, ml_ship_status, ml_ship_substatus,
                        status
                        {pv_col}{cnt_col}
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDENTE'
                        {pv_val}{cnt_val}
                    )
                    ON CONFLICT(ml_id) DO UPDATE SET
                        cliente_nome=excluded.cliente_nome,
                        logistic_type=excluded.logistic_type,
                        ml_shipping_id=excluded.ml_shipping_id,
                        ml_ship_status=excluded.ml_ship_status,
                        ml_ship_substatus=excluded.ml_ship_substatus,
                        atualizado_em=CURRENT_TIMESTAMP
                        {pv_upd}{cnt_upd}
                """.format(
                    pv_col=", bling_pedido_venda_id" if has_bling_pv else "",
                    pv_val=", ?" if has_bling_pv else "",
                    pv_upd=", bling_pedido_venda_id=excluded.bling_pedido_venda_id" if has_bling_pv else ""
                ), tuple(
                    [ml_id, cliente_nome, logistic_type,
                     str(ml_shipping_id) if ml_shipping_id else None,
                     ml_ship_status, ml_ship_substatus]
                    + ([pv_value] if has_bling_pv else [])
                    + _cnt_dat
                ))

            if ml_id in pedido_id_cache:
                pedido_id = pedido_id_cache[ml_id]
            else:
                row = cur.execute("SELECT id FROM pedidos WHERE ml_id=?", (ml_id,)).fetchone()
                if not row:
                    continue
                pedido_id = int(row[0])
                pedido_id_cache[ml_id] = pedido_id

            # ── PRAZO DE ENVIO — usa a instância da conta dona do pedido
            _ml_inst = ped.get("_ml_inst") or ml
            if has_prazo_envio and ml_shipping_id:
                try:
                    sh = _ml_inst._get_shipment(str(ml_shipping_id))
                    if sh:
                        lt = sh.get("lead_time") or {}
                        prazo_raw = (
                            (lt.get("estimated_schedule_limit") or {}).get("date") or
                            (lt.get("buffering") or {}).get("date")
                        )
                        if prazo_raw:
                            cur.execute(
                                "UPDATE pedidos SET prazo_envio = ? WHERE ml_id = ?",
                                (prazo_raw, ml_id),
                            )
                except Exception:
                    pass

            # ped já vem de buscar_cards() com seller_sku enriquecido via GET /items
            # NÃO chamar ml.obter_pedido(ml_id) aqui — jogaria fora o seller_sku já resolvido
            order_items = ped.get("order_items") or []

            # ── EXPANSÃO DOS ITENS ──
            # O ML às vezes retorna o mesmo SKU em linhas separadas com quantity=1
            # em vez de uma linha com quantity=2. Por isso consolidamos ANTES de salvar.
            produtos_expandidos = []
            for oi in order_items:
                info = oi.get("item") or {}
                nome = (info.get("title") or "Produto").strip()
                nome = nome + _attrs_text_ml(oi)

                sku_db = _sku_final_para_db(oi)
                qtd = int(oi.get("quantity") or 1)

                if not sku_db:
                    continue

                sku_str = str(sku_db).strip()

                # Verifica estrutura de kit no Bling para TODOS os produtos.
                # Usa cache global para não repetir chamadas à API entre sincronizações.
                cache_key = f"{sku_str}||{nome[:40]}"
                if cache_key not in _kit_estrutura_cache:
                    try:
                        _kit_estrutura_cache[cache_key] = estrutura_service.obter_estrutura(sku_str, nome)
                    except Exception as _e:
                        print(f"[sync] erro ao obter estrutura sku={sku_str!r}: {_e}", flush=True)
                        _kit_estrutura_cache[cache_key] = None
                estrutura = _kit_estrutura_cache[cache_key]

                if not estrutura:
                    produtos_expandidos.append({
                        "sku": sku_str,
                        "qtd": qtd,
                        "nome": nome,
                        "parent_sku": None,
                        "parent_nome": None,
                    })
                else:
                    for comp in estrutura:
                        comp_sku = str(comp.get("sku")).strip()
                        comp_qtd = int(comp.get("quantidade", 1)) * qtd
                        produtos_expandidos.append({
                            "sku": comp_sku,
                            "qtd": comp_qtd,
                            "nome": comp.get("nome") or nome,
                            "parent_sku": sku_db,
                            "parent_nome": nome,
                        })

            # ── CONSOLIDA duplicatas (mesmo SKU + mesmo parent_sku) ──
            # Garante que se o ML mandou 2x o mesmo item com quantity=1,
            # o banco vai receber quantity=2 corretamente.
            _consolidado: dict[tuple, dict] = {}
            for prod in produtos_expandidos:
                chave = (str(prod["sku"]), prod.get("parent_sku"))
                if chave in _consolidado:
                    _consolidado[chave]["qtd"] += int(prod["qtd"] or 1)
                else:
                    _consolidado[chave] = dict(prod)
            produtos_expandidos = list(_consolidado.values())

            # ── SALVA NO BANCO ──
            for prod in produtos_expandidos:
                sku = str(prod.get("sku") or "").strip()
                qtd = int(prod.get("qtd") or 1)
                nome = str(prod.get("nome") or "Produto")
                parent_sku = prod.get("parent_sku")
                parent_nome = prod.get("parent_nome")

                if not sku:
                    continue

                # produto_id (com cache)
                produto_id = produto_id_cache.get(sku)
                if not produto_id:
                    cur.execute("""
                        INSERT INTO produtos (sku, nome)
                        VALUES (?, ?)
                        ON CONFLICT(sku) DO UPDATE SET nome=excluded.nome
                    """, (sku, nome))
                    prow = cur.execute("SELECT id FROM produtos WHERE sku=?", (sku,)).fetchone()
                    if not prow:
                        continue
                    produto_id = int(prow[0])
                    produto_id_cache[sku] = produto_id

                # estoque Bling (cache por SKU)
                try:
                    if sku not in _estoque_cache:
                        _estoque_cache[sku] = None
                        try:
                            _estoque_cache[sku] = int(bling.obter_estoque(sku))
                        except Exception:
                            _estoque_cache[sku] = None
                    if _estoque_cache.get(sku) is not None:
                        cur.execute("UPDATE produtos SET estoque_bling=? WHERE sku=?",
                                    (int(_estoque_cache[sku]), sku))
                except Exception:
                    pass

                # ── UPSERT pedido_produtos ──
                # Distingue por parent_sku para que dois kits diferentes com o mesmo
                # componente filho não se sobrescrevam.
                if parent_sku is not None:
                    pp = cur.execute("""
                        SELECT id FROM pedido_produtos
                        WHERE pedido_id=? AND produto_id=? AND parent_sku=?
                    """, (pedido_id, produto_id, parent_sku)).fetchone()

                    if pp:
                        cur.execute("""
                            UPDATE pedido_produtos
                            SET quantidade=?,
                                parent_nome=COALESCE(?, parent_nome)
                            WHERE pedido_id=? AND produto_id=? AND parent_sku=?
                        """, (qtd, parent_nome, pedido_id, produto_id, parent_sku))
                    else:
                        cur.execute("""
                            INSERT INTO pedido_produtos (
                                pedido_id, produto_id, quantidade, quantidade_bipada,
                                parent_sku, parent_nome
                            ) VALUES (?, ?, ?, 0, ?, ?)
                        """, (pedido_id, produto_id, qtd, parent_sku, parent_nome))
                else:
                    pp = cur.execute("""
                        SELECT id FROM pedido_produtos
                        WHERE pedido_id=? AND produto_id=? AND parent_sku IS NULL
                    """, (pedido_id, produto_id)).fetchone()

                    if pp:
                        cur.execute("""
                            UPDATE pedido_produtos
                            SET quantidade=?
                            WHERE pedido_id=? AND produto_id=? AND parent_sku IS NULL
                        """, (qtd, pedido_id, produto_id))
                    else:
                        cur.execute("""
                            INSERT INTO pedido_produtos (
                                pedido_id, produto_id, quantidade, quantidade_bipada,
                                parent_sku, parent_nome
                            ) VALUES (?, ?, ?, 0, NULL, NULL)
                        """, (pedido_id, produto_id, qtd))

                count_itens += 1

            count_pedidos += 1

        db.commit()
        db.close()

        msg = f"Sync concluída! {count_pedidos} pedidos / {count_itens} itens."
        if _is_fetch(request):
            return jsonify({"success": True, "message": msg, "pedidos": count_pedidos, "itens": count_itens}), 200

        flash("✅ " + msg, "success")
        return redirect(url_for("main.index"))

    except Exception as e:
        traceback.print_exc()
        err = str(e)
        if _is_fetch(request):
            return jsonify({"success": False, "message": err}), 500
        flash(f"❌ Erro ao sincronizar: {err}", "danger")
        return redirect(url_for("main.index"))


# ============================================================
# BIPAR (tela 1 -> separação)
# ============================================================

@main_routes.route("/verificar_e_bipar", methods=["POST"])
def verificar_e_bipar():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip()
    ml_id = (data.get("ml_id") or "").strip()

    if not sku:
        return jsonify({"success": False, "error": "SKU vazio"}), 400
    if not ml_id:
        return jsonify({"success": False, "error": "ml_id obrigatório (pedido selecionado)"}), 400

    db = get_db()
    cur = db.cursor()

    row = cur.execute("""
        SELECT
            pp.id        AS pp_id,
            p.id         AS pedido_id,
            p.ml_id,
            pp.quantidade,
            pp.quantidade_bipada,
            pr.nome      AS nome,
            pp.parent_sku,
            pp.parent_nome
        FROM pedido_produtos pp
        JOIN produtos pr ON pr.id = pp.produto_id
        JOIN pedidos p ON p.id = pp.pedido_id
        WHERE UPPER(pr.sku) = UPPER(?)
          AND p.ml_id = ?
          AND p.status = 'PENDENTE'
          AND pp.quantidade_bipada < pp.quantidade
        LIMIT 1
    """, (sku, ml_id)).fetchone()

    if not row:
        db.close()
        return jsonify({"success": False, "error": "SKU não encontrado nesse pedido ou já bipado"}), 404

    saldo_int = None
    try:
        bling = BlingService()
        saldo_int = int(bling.obter_estoque(sku))
        cur.execute("UPDATE produtos SET estoque_bling=? WHERE sku=?", (saldo_int, sku))
        db.commit()
    except Exception as e:
        print(f"⚠️ Bling falhou ao obter estoque do SKU {sku}: {e}", flush=True)

    cur.execute("""
        UPDATE pedido_produtos
           SET quantidade_bipada = quantidade_bipada + 1
         WHERE id = ?
    """, (row["pp_id"],))
    db.commit()

    totals = cur.execute("""
        SELECT
          SUM(pp.quantidade) as total_qtd,
          SUM(pp.quantidade_bipada) as total_bip
        FROM pedido_produtos pp
        WHERE pp.pedido_id = ?
    """, (row["pedido_id"],)).fetchone()

    total_qtd = int((totals["total_qtd"] if totals else 0) or 0)
    total_bip = int((totals["total_bip"] if totals else 0) or 0)

    faltas = cur.execute("""
        SELECT COUNT(*) as faltando
        FROM pedido_produtos
        WHERE pedido_id = ?
          AND quantidade_bipada < quantidade
    """, (row["pedido_id"],)).fetchone()

    faltando_itens = int((faltas["faltando"] if faltas else 0) or 0)

    # retorna quantidade do item para prompt de multiplas unidades
    quantidade_item = int(row["quantidade"] or 1)

    if faltando_itens == 0:
        cur.execute("""
            UPDATE pedidos
               SET status='SEPARADO',
                   atualizado_em=CURRENT_TIMESTAMP
             WHERE id=?
        """, (row["pedido_id"],))

        cur.execute("""
            UPDATE pedido_produtos
               SET quantidade_separada = quantidade_bipada,
                   quantidade_bipada = 0
             WHERE pedido_id = ?
        """, (row["pedido_id"],))

        db.commit()
        total_bip = 0

    # Registra no audit_log para rastreabilidade e Painel TV
    try:
        from flask_login import current_user
        from services.audit_service import log_action
        uid = current_user.id if current_user.is_authenticated else None
        log_action(
            user_id=uid, action="bipar",
            entity_type="pedido", entity_id=str(row["ml_id"]),
            metadata={
                "sku":         sku,
                "nome":        row["nome"] or "",
                "parent_sku":  row["parent_sku"],
                "parent_nome": row["parent_nome"],
                "quantidade":  1,
            },
            ip_address=request.remote_addr,
        )
    except Exception:
        pass

    db.close()
    return jsonify({
        "success": True,
        "message": "Bipado com sucesso!",
        "saldo": saldo_int,
        "ml_id": row["ml_id"],
        "total_quantidade": total_qtd,
        "total_bipado": total_bip,
        "faltando_itens": faltando_itens,
        "pedido_fechado": (faltando_itens == 0),
        "quantidade_item": quantidade_item,
    })


@main_routes.route("/scanner/descobrir_pedido", methods=["POST"])
def scanner_descobrir_pedido():
    data = request.get_json(silent=True) or {}
    sku = str(data.get("sku") or "").strip()

    if not sku:
        return jsonify({"success": False}), 400

    db = get_db()

    rows = db.execute("""
        SELECT DISTINCT
            p.ml_id,
            p.cliente_nome,
            p.criado_em
        FROM pedido_produtos pp
        JOIN produtos pr ON pr.id = pp.produto_id
        JOIN pedidos p ON p.id = pp.pedido_id
        WHERE UPPER(pr.sku) = UPPER(?)
        AND p.status IN ('PENDENTE', 'PARCIAL')
        AND pp.quantidade_bipada < pp.quantidade
        ORDER BY p.criado_em ASC
    """, (sku,)).fetchall()

    db.close()

    pedidos = []
    for r in rows:
        pedidos.append({
            "ml_id": r["ml_id"],
            "cliente_nome": r["cliente_nome"]
        })

    return jsonify({
        "success": True,
        "pedidos": pedidos
    })


@main_routes.route("/scanner/descobrir_pedido_exp", methods=["POST"])
def scanner_descobrir_pedido_exp():
    """Igual a descobrir_pedido mas para pedidos SEPARADOS (tela Expedição)."""
    data = request.get_json(silent=True) or {}
    sku  = str(data.get("sku") or "").strip()

    if not sku:
        return jsonify({"success": False}), 400

    db   = get_db()
    rows = db.execute("""
        SELECT DISTINCT
            p.ml_id,
            p.cliente_nome,
            p.criado_em
        FROM pedido_produtos pp
        JOIN produtos pr ON pr.id = pp.produto_id
        JOIN pedidos   p  ON p.id  = pp.pedido_id
        WHERE UPPER(pr.sku) = UPPER(?)
          AND p.status = 'SEPARADO'
          AND pp.quantidade_bipada < pp.quantidade
        ORDER BY p.criado_em ASC
    """, (sku,)).fetchall()
    db.close()

    return jsonify({
        "success": True,
        "pedidos": [{"ml_id": r["ml_id"], "cliente_nome": r["cliente_nome"]} for r in rows],
    })

# ============================================================
# TELA 2 - SCANNER (conferência)
# ============================================================

@main_routes.route("/scanner")
def scanner():
    pedidos = _load_pedidos("SEPARADO")
    stats = {
        "total_pedidos": len(pedidos),
        "total_itens": sum(int(p.get("total_quantidade") or 0) for p in pedidos),
        "total_bipado": sum(int(p.get("total_bipado") or 0) for p in pedidos),
    }
    return render_template("scanner.html", pedidos=pedidos, stats=stats)


@main_routes.route("/scanner/pedido_atual")
def scanner_pedido_atual():
    db = get_db()
    cur = db.cursor()

    pedido = cur.execute("""
        SELECT id, ml_id, cliente_nome
        FROM pedidos
        WHERE status = 'SEPARADO'
        ORDER BY criado_em
        LIMIT 1
    """).fetchone()

    if not pedido:
        db.close()
        return jsonify({"success": False})

    pedido_id = pedido["id"]

    itens = cur.execute("""
        SELECT
            pr.sku,
            pr.nome,
            pp.quantidade,
            pp.quantidade_bipada,
            pr.estoque_bling,
            pp.parent_nome
        FROM pedido_produtos pp
        JOIN produtos pr ON pr.id = pp.produto_id
        WHERE pp.pedido_id = ?
        ORDER BY pr.nome
    """, (pedido_id,)).fetchall()

    itens_json = []
    for i in itens:
        itens_json.append({
            "sku": i["sku"],
            "nome": i["parent_nome"] or i["nome"],
            "quantidade": i["quantidade"],
            "quantidade_conferida": i["quantidade_bipada"],
            "estoque_bling": i["estoque_bling"]
        })

    db.close()

    return jsonify({
        "success": True,
        "pedido": {
            "id": pedido["id"],
            "ml_id": pedido["ml_id"],
            "cliente_nome": pedido["cliente_nome"],
            "itens": itens_json
        }
    })


@main_routes.route("/scanner/bipar", methods=["POST"])
def scanner_bipar():
    data = request.get_json(silent=True) or {}

    sku = str(data.get("sku") or "").strip()
    ml_id = str(data.get("ml_id") or "").strip()

    if not sku or not ml_id:
        return jsonify({"success": False, "error": "Dados inválidos"}), 400

    db = get_db()
    cur = db.cursor()

    row = cur.execute("""
        SELECT
            pp.id as pp_id,
            pp.quantidade,
            pp.quantidade_bipada,
            p.id as pedido_id,
            p.ml_id,
            p.origem
        FROM pedido_produtos pp
        JOIN produtos pr ON pr.id = pp.produto_id
        JOIN pedidos p ON p.id = pp.pedido_id
        WHERE UPPER(pr.sku) = UPPER(?)
        AND p.ml_id = ?
        AND p.status IN ('PENDENTE', 'PARCIAL', 'SEPARADO')
        AND pp.quantidade_bipada < pp.quantidade
        LIMIT 1
    """, (sku, ml_id)).fetchone()

    if not row:
        db.close()
        return jsonify({"success": False, "error": "Item não pertence ao pedido"}), 404

    cur.execute("""
        UPDATE pedido_produtos
        SET quantidade_bipada = quantidade_bipada + 1
        WHERE id = ?
    """, (row["pp_id"],))

    faltantes = cur.execute("""
        SELECT COUNT(*)
        FROM pedido_produtos
        WHERE pedido_id = ?
        AND quantidade_bipada < quantidade
    """, (row["pedido_id"],)).fetchone()[0]

    # Marca PARCIAL quando parcialmente bipado (evita ficar como PENDENTE)
    if faltantes > 0:
        cur.execute("""
            UPDATE pedidos
            SET status = 'PARCIAL', atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'PENDENTE'
        """, (row["pedido_id"],))

    db.commit()

    if faltantes > 0:
        db.close()
        return jsonify({
            "success": True,
            "pedido_fechado": False,
            "faltando_itens": faltantes
        })

    # ---------------------------
    # PEDIDO FINALIZADO
    # ---------------------------

    # COMERCIAL e SHOPEE nao emitem NF-e pelo sistema — apenas marcam SEPARADO
    if (row["origem"] or "ML") in ("COMERCIAL", "SHOPEE"):
        cur.execute("""
            UPDATE pedidos SET status = 'SEPARADO', atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (row["pedido_id"],))
        db.commit()
        db.close()
        return jsonify({"success": True, "pedido_fechado": True, "faltando_itens": 0})

    try:
        import os
        if os.getenv("EMITIR_VIA_API", "true").lower() in ("true", "1", "yes"):
            from services.emitir_via_rpa import emitir_nfe_por_ml_id
        else:
            from services.emitir_via_rpa import emitir_nfe_por_ml_id

        kit = cur.execute("""
            SELECT parent_sku
            FROM pedido_produtos
            WHERE pedido_id = ?
            AND parent_sku IS NOT NULL
            LIMIT 1
        """, (row["pedido_id"],)).fetchone()

        sku_kit = None
        if kit:
            sku_kit = kit["parent_sku"]
            print(f"📦 Pedido contém KIT → SKU do kit: {sku_kit}", flush=True)

        print(f"🚀 Emitindo NF {ml_id}", flush=True)

        resultado = emitir_nfe_por_ml_id(
            ml_any_id=ml_id,
            sku=sku_kit,
            headless=True
        )

        pdf_path = resultado.get("pdf")

    except Exception as e:
        print("❌ ERRO emissão:", e, flush=True)
        pdf_path = None

    cur.execute("""
        UPDATE pedidos
        SET status = 'CONCLUIDO'
        WHERE id = ?
    """, (row["pedido_id"],))

    db.commit()
    db.close()

    return jsonify({
        "success": True,
        "pedido_fechado": True,
        "faltando_itens": 0,
        "pdf": pdf_path
    })


# ============================================================
# Marcar separado manual (botão)
# ============================================================

@main_routes.route("/pedido/<ml_id>/separar", methods=["POST"])
def marcar_separado(ml_id):
    db = get_db()
    cur = db.execute("""
        UPDATE pedidos
        SET status='SEPARADO', atualizado_em=CURRENT_TIMESTAMP
        WHERE ml_id=?
          AND status IN ('PENDENTE','PARCIAL')
    """, (ml_id,))
    db.commit()
    db.close()

    if cur.rowcount == 0:
        return jsonify({"success": False, "error": "Pedido não encontrado ou status inválido"}), 404
    return jsonify({"success": True, "ml_id": ml_id})

serial_bp = Blueprint("serial_bp", __name__)

@serial_bp.post("/serial/add")
def serial_add():
    data = request.get_json(force=True) or {}
    ml_id = str(data.get("ml_id") or "").strip()
    sku = str(data.get("sku") or "").strip()
    serial = str(data.get("serial") or "").strip()

    if not ml_id or not serial:
        return jsonify({"success": False, "error": "ml_id e serial são obrigatórios"}), 400

    store = SerialStore()
    store.add(ml_id, sku or "SEM_SKU", serial)

    return jsonify({"success": True})

# =========================
# SERVIR PDF PARA IMPRESSÃO
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")

@main_routes.route("/print/<path:filename>")
def print_file(filename):
    path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=False)


# =========================
# GERAR PDF DE TESTE
# =========================
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

@main_routes.route("/debug/print_test")
def debug_print_test_route():
    nome = f"teste_{int(time.time())}.pdf"
    path = os.path.join(DOWNLOADS_DIR, nome)
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica", 20)
    c.drawString(100, 700, "TESTE DE IMPRESSAO")
    c.setFont("Helvetica", 14)
    c.drawString(100, 650, "Se voce esta lendo isso")
    c.drawString(100, 620, "o sistema funcionou")
    c.save()
    return {"pdf": f"/print/{nome}"}


# ============================================================
# REMOVER ITEM(NS) DE UM PEDIDO
# Adicione esses dois routes no seu routes.py
# ============================================================

@main_routes.route("/pedido/<ml_id>/remover_itens", methods=["POST"])
def remover_itens_pedido(ml_id):
    """
    Remove itens específicos de um pedido PENDENTE.

    Body JSON:
      { "skus": ["SKU1", "SKU2"] }            -> remove apenas esses SKUs
      { "skus": [], "kit_skus": ["KITSKU1"] } -> remove todos os itens de um kit pelo parent_sku
      { "tudo": true }                         -> remove TODOS os itens do pedido (zera o pedido)

    O pedido em si NÃO é removido — só os vínculos em pedido_produtos.
    Se quiser apagar o pedido junto, use /pedido/<ml_id>/arquivar.
    """
    data = request.get_json(silent=True) or {}
    skus     = [str(s).strip() for s in (data.get("skus") or []) if s]
    kit_skus = [str(s).strip() for s in (data.get("kit_skus") or []) if s]
    tudo     = bool(data.get("tudo"))

    if not skus and not kit_skus and not tudo:
        return jsonify({"success": False, "error": "Nenhum item informado"}), 400

    db = get_db()
    cur = db.cursor()

    # Confirma que o pedido existe e está PENDENTE
    pedido = cur.execute(
        "SELECT id FROM pedidos WHERE ml_id=? AND status='PENDENTE'",
        (ml_id,)
    ).fetchone()

    if not pedido:
        db.close()
        return jsonify({"success": False, "error": "Pedido não encontrado ou não está PENDENTE"}), 404

    pedido_id = pedido["id"]
    removidos = 0

    if tudo:
        # Remove todos os itens do pedido
        cur.execute("DELETE FROM pedido_produtos WHERE pedido_id=?", (pedido_id,))
        removidos = cur.rowcount

    else:
        # Remove por SKU individual (itens normais)
        for sku in skus:
            cur.execute("""
                DELETE FROM pedido_produtos
                WHERE pedido_id=?
                  AND produto_id IN (
                      SELECT id FROM produtos WHERE UPPER(sku)=UPPER(?)
                  )
                  AND parent_sku IS NULL
            """, (pedido_id, sku))
            removidos += cur.rowcount

        # Remove kit inteiro pelo parent_sku
        for kit_sku in kit_skus:
            cur.execute("""
                DELETE FROM pedido_produtos
                WHERE pedido_id=?
                  AND parent_sku=?
            """, (pedido_id, kit_sku))
            removidos += cur.rowcount

    db.commit()

    # Recalcula totais para retornar ao frontend
    totals = cur.execute("""
        SELECT COUNT(*) as itens,
               SUM(quantidade) as total_qtd,
               SUM(quantidade_bipada) as total_bip
        FROM pedido_produtos
        WHERE pedido_id=?
    """, (pedido_id,)).fetchone()

    db.close()

    return jsonify({
        "success": True,
        "ml_id": ml_id,
        "removidos": removidos,
        "itens_restantes": int((totals["itens"] if totals else 0) or 0),
        "total_quantidade": int((totals["total_qtd"] if totals else 0) or 0),
        "total_bipado": int((totals["total_bip"] if totals else 0) or 0),
    })


# ============================================================
# DELETAR PEDIDO (permanente — remove do banco)
# ============================================================

@main_routes.route("/pedido/<ml_id>", methods=["DELETE"])
def deletar_pedido(ml_id):
    """
    Deleta permanentemente um pedido e todos os seus itens do banco.
    Idempotente: retorna 200 mesmo se o pedido já não existir.
    """
    db = get_db()
    pedido = db.execute(
        "SELECT id FROM pedidos WHERE ml_id=?", (ml_id,)
    ).fetchone()

    if not pedido:
        db.close()
        return jsonify({"success": True, "ml_id": ml_id, "already_deleted": True})

    db.execute("DELETE FROM pedido_produtos WHERE pedido_id=?", (pedido["id"],))
    db.execute("DELETE FROM pedidos WHERE id=?", (pedido["id"],))
    db.commit()
    db.close()

    return jsonify({"success": True, "ml_id": ml_id})


# ============================================================
# LIMPAR PEDIDOS EM LOTE (concluídos / arquivados)
# ============================================================

@main_routes.route("/pedidos/limpar", methods=["POST"])
def limpar_pedidos():
    """
    Deleta permanentemente todos os pedidos com os status informados.

    Body JSON (opcional):
      { "status": ["CONCLUIDO", "ARQUIVADO"] }   ← default

    Retorna:
      { "success": true, "removidos": 42 }
    """
    data = request.get_json(silent=True) or {}
    status_alvo = data.get("status") or ["CONCLUIDO", "ARQUIVADO"]

    if not isinstance(status_alvo, list) or not status_alvo:
        return jsonify({"success": False, "error": "status deve ser uma lista não vazia"}), 400

    db = get_db()
    cur = db.cursor()

    ph = ",".join("?" * len(status_alvo))
    pedidos = cur.execute(
        f"SELECT id FROM pedidos WHERE status IN ({ph})",
        tuple(status_alvo)
    ).fetchall()

    ids = [p["id"] for p in pedidos]
    count = len(ids)

    if ids:
        ph_ids = ",".join("?" * len(ids))
        cur.execute(f"DELETE FROM pedido_produtos WHERE pedido_id IN ({ph_ids})", tuple(ids))
        cur.execute(f"DELETE FROM pedidos WHERE id IN ({ph_ids})", tuple(ids))

    db.commit()
    db.close()

    return jsonify({"success": True, "removidos": count})