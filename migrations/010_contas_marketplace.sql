-- Gerenciamento de múltiplas contas ML e Shopee
CREATE TABLE IF NOT EXISTS contas_marketplace (
  id                    SERIAL       PRIMARY KEY,
  plataforma            VARCHAR(20)  NOT NULL CHECK (plataforma IN ('ML','SHOPEE','COMERCIAL')),
  nome                  VARCHAR(100) NOT NULL,
  ativo                 BOOLEAN      DEFAULT true,
  ml_access_token       TEXT,
  ml_refresh_token      TEXT,
  ml_user_id            TEXT,
  ml_expires_at         TIMESTAMPTZ,
  shopee_shop_id        BIGINT,
  shopee_access_token   TEXT,
  shopee_refresh_token  TEXT,
  shopee_expires_at     TIMESTAMPTZ,
  criado_em             TIMESTAMPTZ  DEFAULT NOW(),
  updated_at            TIMESTAMPTZ  DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_conta_plataforma_nome
  ON contas_marketplace (plataforma, nome);

-- Liga pedidos à conta de origem
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS conta_id   INTEGER REFERENCES contas_marketplace(id);
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS conta_nome VARCHAR(100);
