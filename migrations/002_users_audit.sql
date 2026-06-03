-- ============================================================
-- migrations/002_users_audit.sql
-- WMS LHV-MED — Users, Audit Log, Bipagem Sessions
-- ============================================================

-- Usuários internos do WMS
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL          PRIMARY KEY,
    username      VARCHAR(50)     UNIQUE NOT NULL,
    email         VARCHAR(120)    UNIQUE NOT NULL,
    password_hash VARCHAR(255)    NOT NULL,
    full_name     VARCHAR(120),
    role          VARCHAR(20)     NOT NULL DEFAULT 'operator'
                                  CHECK (role IN ('admin', 'supervisor', 'operator', 'viewer')),
    active        BOOLEAN         NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);

-- Registro de todas as ações do sistema
CREATE TABLE IF NOT EXISTS audit_log (
    id            SERIAL          PRIMARY KEY,
    user_id       INT             REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR(50)     NOT NULL,
    entity_type   VARCHAR(50),
    entity_id     VARCHAR(100),
    metadata      TEXT,           -- JSON serializado
    success       BOOLEAN         NOT NULL DEFAULT true,
    error_message TEXT,
    duration_ms   INT,
    ip_address    VARCHAR(45),
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user_date ON audit_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_entity    ON audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created   ON audit_log (created_at DESC);

-- Sessões de bipagem (pra calcular produtividade)
CREATE TABLE IF NOT EXISTS bipagem_sessions (
    id            SERIAL          PRIMARY KEY,
    user_id       INT             REFERENCES users(id) ON DELETE SET NULL,
    pedido_id     VARCHAR(100),
    started_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    total_items   INT             NOT NULL DEFAULT 0,
    items_bipped  INT             NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bip_sess_user ON bipagem_sessions (user_id, started_at DESC);
