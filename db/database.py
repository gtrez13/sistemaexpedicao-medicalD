import os
import sqlite3
from contextlib import contextmanager

# db/wms.db
DB_PATH = os.path.join(os.path.dirname(__file__), "wms.db")

# =========================
# Conexão
# =========================
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


@contextmanager
def db_session():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =========================
# Helpers de schema
# =========================
def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table_name,)
    ).fetchone()
    return row is not None


def _columns(conn, table: str) -> set:
    if not _table_exists(conn, table):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] if not isinstance(r, sqlite3.Row) else r["name"] for r in rows}


def _user_version(conn) -> int:
    row = conn.execute("PRAGMA user_version;").fetchone()
    if isinstance(row, sqlite3.Row):
        return int(row[0])
    return int(row[0])


def _set_user_version(conn, v: int):
    conn.execute(f"PRAGMA user_version = {int(v)};")


# =========================
# Schema v1 (base)
# =========================
def _create_schema_v1(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE NOT NULL COLLATE NOCASE,
            nome TEXT NOT NULL,
            estoque INTEGER DEFAULT 0 CHECK (estoque >= 0),
            localizacao TEXT DEFAULT 'Geral',
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ml_id TEXT UNIQUE NOT NULL,
            cliente_nome TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDENTE',
            logistic_type TEXT,
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
            atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pedido_produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            quantidade INTEGER NOT NULL DEFAULT 1 CHECK (quantidade > 0),
            quantidade_bipada INTEGER NOT NULL DEFAULT 0 CHECK (quantidade_bipada >= 0),
            criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE,
            FOREIGN KEY(produto_id) REFERENCES produtos(id) ON DELETE RESTRICT
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_produtos_sku ON produtos(sku);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_status ON pedidos(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pp_pedido ON pedido_produtos(pedido_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pp_produto ON pedido_produtos(produto_id);")

    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_pedidos_atualizado
        AFTER UPDATE ON pedidos
        FOR EACH ROW
        BEGIN
            UPDATE pedidos
            SET atualizado_em = CURRENT_TIMESTAMP
            WHERE id = OLD.id;
        END;
    """)


# =========================
# Migrações versionadas
# =========================
def _migrate_to_v2(conn):
    cols_ped = _columns(conn, "pedidos")
    cols_prod = _columns(conn, "produtos")

    if "estoque_bling" not in cols_prod:
        conn.execute("ALTER TABLE produtos ADD COLUMN estoque_bling INTEGER;")

    add_cols = {
        "ml_shipping_id": "TEXT",
        "ml_ship_status": "TEXT",
        "ml_ship_substatus": "TEXT",
        "ml_task": "TEXT",
        "bling_pedido_venda_id": "INTEGER",
    }
    for c, typ in add_cols.items():
        if c not in cols_ped:
            conn.execute(f"ALTER TABLE pedidos ADD COLUMN {c} {typ};")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_ml_id ON pedidos(ml_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_task ON pedidos(ml_task);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_ship ON pedidos(ml_shipping_id);")


def _migrate_to_v3(conn):
    cols_pp = _columns(conn, "pedido_produtos")

    if "quantidade_separada" not in cols_pp:
        conn.execute("ALTER TABLE pedido_produtos ADD COLUMN quantidade_separada INTEGER NOT NULL DEFAULT 0;")

    if "parent_sku" not in cols_pp:
        conn.execute("ALTER TABLE pedido_produtos ADD COLUMN parent_sku TEXT;")

    if "parent_nome" not in cols_pp:
        conn.execute("ALTER TABLE pedido_produtos ADD COLUMN parent_nome TEXT;")

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pp_normal
        ON pedido_produtos(pedido_id, produto_id)
        WHERE parent_sku IS NULL;
    """)

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pp_kit
        ON pedido_produtos(pedido_id, produto_id, parent_sku)
        WHERE parent_sku IS NOT NULL;
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pp_faltando
        ON pedido_produtos(pedido_id)
        WHERE quantidade_bipada < quantidade;
    """)


def _migrate_to_v4(conn):
    """
    v4: suporte a ARQUIVADO
    - pedidos.observacao: campo de observação livre
    - pedidos.arquivado_em: data de arquivamento
    - status 'ARQUIVADO' passa a ser válido (sem CHECK, validado no app)
    """
    cols_ped = _columns(conn, "pedidos")

    if "observacao" not in cols_ped:
        conn.execute("ALTER TABLE pedidos ADD COLUMN observacao TEXT;")

    if "arquivado_em" not in cols_ped:
        conn.execute("ALTER TABLE pedidos ADD COLUMN arquivado_em DATETIME;")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_status_arq ON pedidos(status) WHERE status = 'ARQUIVADO';")


def _migrate_to_v5(conn):
    """v5: users, audit_log, bipagem_sessions para autenticação e rastreamento."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name     TEXT,
            role          TEXT NOT NULL DEFAULT 'operator'
                          CHECK (role IN ('admin', 'supervisor', 'operator', 'viewer')),
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login    DATETIME
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER REFERENCES users(id),
            action        TEXT NOT NULL,
            entity_type   TEXT,
            entity_id     TEXT,
            metadata      TEXT,
            success       INTEGER NOT NULL DEFAULT 1,
            error_message TEXT,
            duration_ms   INTEGER,
            ip_address    TEXT,
            created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bipagem_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER REFERENCES users(id),
            pedido_id     TEXT,
            started_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at  DATETIME,
            total_items   INTEGER NOT NULL DEFAULT 0,
            items_bipped  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_date ON audit_log(user_id, created_at);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit_log(action);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_entity    ON audit_log(entity_type, entity_id);")


def _migrate_to_v6(conn):
    """v6: origem em pedidos (ML|COMERCIAL) + dimensoes de produto para frete."""
    cols_ped  = _columns(conn, "pedidos")
    cols_prod = _columns(conn, "produtos")

    if "origem" not in cols_ped:
        conn.execute("ALTER TABLE pedidos ADD COLUMN origem TEXT NOT NULL DEFAULT 'ML';")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_origem ON pedidos(origem);")

    for col, typ in [
        ("peso_kg",        "REAL"),
        ("altura_cm",      "REAL"),
        ("largura_cm",     "REAL"),
        ("comprimento_cm", "REAL"),
    ]:
        if col not in cols_prod:
            conn.execute(f"ALTER TABLE produtos ADD COLUMN {col} {typ};")


def migrate_db(conn):
    if not _table_exists(conn, "pedidos") or not _table_exists(conn, "produtos") or not _table_exists(conn, "pedido_produtos"):
        _create_schema_v1(conn)
        _set_user_version(conn, 1)

    v = _user_version(conn)

    if v < 1:
        _create_schema_v1(conn)
        v = 1
        _set_user_version(conn, v)

    if v < 2:
        _migrate_to_v2(conn)
        v = 2
        _set_user_version(conn, v)

    if v < 3:
        _migrate_to_v3(conn)
        v = 3
        _set_user_version(conn, v)

    if v < 4:
        _migrate_to_v4(conn)
        v = 4
        _set_user_version(conn, v)

    if v < 5:
        _migrate_to_v5(conn)
        v = 5
        _set_user_version(conn, v)

    if v < 6:
        _migrate_to_v6(conn)
        v = 6
        _set_user_version(conn, v)

    return v


def init_db():
    with db_session() as conn:
        _create_schema_v1(conn)
        final_v = migrate_db(conn)

    print(f"✅ WMS Database inicializado/migrado (schema v{final_v})!", flush=True)


if __name__ == "__main__":
    init_db()
