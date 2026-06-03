-- ============================================================
-- migrations/005_bling_tokens.sql
-- Tabela para persistência dos tokens OAuth2 do Bling no banco.
-- ============================================================

CREATE TABLE IF NOT EXISTS bling_tokens (
    id            SERIAL       PRIMARY KEY,
    instance      VARCHAR(50)  UNIQUE NOT NULL DEFAULT 'principal',
    access_token  TEXT         NOT NULL,
    refresh_token TEXT         NOT NULL,
    expires_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bling_tokens_instance ON bling_tokens (instance);
