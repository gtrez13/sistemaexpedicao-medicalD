-- ============================================================
-- migrations/001_initial.sql
-- WMS LHV-MED — Schema PostgreSQL 15
-- Migrado de SQLite (schema v4)
-- ============================================================

-- Extensão pra texto case-insensitive (equivale ao COLLATE NOCASE do SQLite)
CREATE EXTENSION IF NOT EXISTS citext;

-- ============================================================
-- produtos
-- ============================================================
CREATE TABLE IF NOT EXISTS produtos (
    id              SERIAL          PRIMARY KEY,
    sku             CITEXT          UNIQUE NOT NULL,          -- NOCASE via citext
    nome            TEXT            NOT NULL,
    estoque         INTEGER         NOT NULL DEFAULT 0 CHECK (estoque >= 0),
    estoque_bling   INTEGER,
    localizacao     TEXT            NOT NULL DEFAULT 'Geral',
    criado_em       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_produtos_sku ON produtos (sku);

-- ============================================================
-- pedidos
-- ============================================================
CREATE TABLE IF NOT EXISTS pedidos (
    id                      SERIAL          PRIMARY KEY,
    ml_id                   TEXT            UNIQUE NOT NULL,
    cliente_nome            TEXT            NOT NULL,
    status                  TEXT            NOT NULL DEFAULT 'PENDENTE'
                                            CHECK (status IN ('PENDENTE','PARCIAL','SEPARADO','CONCLUIDO','ARQUIVADO')),
    logistic_type           TEXT,
    ml_shipping_id          TEXT,
    ml_ship_status          TEXT,
    ml_ship_substatus       TEXT,
    ml_task                 TEXT,
    bling_pedido_venda_id   BIGINT,
    observacao              TEXT,
    arquivado_em            TIMESTAMPTZ,
    criado_em               TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    atualizado_em           TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pedidos_ml_id       ON pedidos (ml_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_status      ON pedidos (status);
CREATE INDEX IF NOT EXISTS idx_pedidos_criado_em   ON pedidos (criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_pedidos_task        ON pedidos (ml_task);
CREATE INDEX IF NOT EXISTS idx_pedidos_ship        ON pedidos (ml_shipping_id);
CREATE INDEX IF NOT EXISTS idx_pedidos_arq         ON pedidos (status) WHERE status = 'ARQUIVADO';

-- ============================================================
-- pedido_produtos
-- ============================================================
CREATE TABLE IF NOT EXISTS pedido_produtos (
    id                  SERIAL      PRIMARY KEY,
    pedido_id           INTEGER     NOT NULL REFERENCES pedidos(id)  ON DELETE CASCADE,
    produto_id          INTEGER     NOT NULL REFERENCES produtos(id) ON DELETE RESTRICT,
    quantidade          INTEGER     NOT NULL DEFAULT 1  CHECK (quantidade > 0),
    quantidade_bipada   INTEGER     NOT NULL DEFAULT 0  CHECK (quantidade_bipada >= 0),
    quantidade_separada INTEGER     NOT NULL DEFAULT 0,
    parent_sku          TEXT,
    parent_nome         TEXT,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pp_pedido_id  ON pedido_produtos (pedido_id);
CREATE INDEX IF NOT EXISTS idx_pp_produto_id ON pedido_produtos (produto_id);
CREATE INDEX IF NOT EXISTS idx_pp_parent_sku ON pedido_produtos (parent_sku) WHERE parent_sku IS NOT NULL;

-- Equivalente ao índice condicional faltando do SQLite v3
CREATE INDEX IF NOT EXISTS idx_pp_faltando
    ON pedido_produtos (pedido_id)
    WHERE quantidade_bipada < quantidade;

-- Unique: item normal (sem parent) só aparece 1x por pedido
CREATE UNIQUE INDEX IF NOT EXISTS uq_pp_normal
    ON pedido_produtos (pedido_id, produto_id)
    WHERE parent_sku IS NULL;

-- Unique: componente de kit: único por (pedido, produto, kit_pai)
CREATE UNIQUE INDEX IF NOT EXISTS uq_pp_kit
    ON pedido_produtos (pedido_id, produto_id, parent_sku)
    WHERE parent_sku IS NOT NULL;

-- ============================================================
-- Trigger: atualiza atualizado_em automaticamente
-- ============================================================
CREATE OR REPLACE FUNCTION set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pedidos_atualizado_em ON pedidos;
CREATE TRIGGER trg_pedidos_atualizado_em
    BEFORE UPDATE ON pedidos
    FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

-- ============================================================
-- sku_alias — mapeamento MLB item_id → SKU Bling
-- Permite resolver aliases encadeados (ex.: variação → SKU pai)
-- ============================================================
CREATE TABLE IF NOT EXISTS sku_alias (
    id          SERIAL      PRIMARY KEY,
    ml_item_id  TEXT        NOT NULL,
    sku_bling   TEXT        NOT NULL,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ml_item_id)
);

CREATE INDEX IF NOT EXISTS idx_sku_alias_ml_item ON sku_alias (ml_item_id);
