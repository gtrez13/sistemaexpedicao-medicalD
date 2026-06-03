-- ============================================================
-- migrations/004_password_reset.sql
-- Tokens de recuperação de senha
-- ============================================================

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id         SERIAL       PRIMARY KEY,
    user_id    INT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token      VARCHAR(100) UNIQUE NOT NULL,
    expires_at TIMESTAMPTZ  NOT NULL,
    used       BOOLEAN      NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reset_token ON password_reset_tokens (token);
