-- migrations/007_shopee_tokens.sql
-- Tabela de tokens OAuth Shopee (criada também pelo authorize_shopee.py)

CREATE TABLE IF NOT EXISTS shopee_tokens (
    id            SERIAL      PRIMARY KEY,
    shop_id       BIGINT      UNIQUE NOT NULL,
    access_token  TEXT        NOT NULL,
    refresh_token TEXT        NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shopee_tokens_updated
    ON shopee_tokens (updated_at DESC);
