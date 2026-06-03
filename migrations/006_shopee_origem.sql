-- migrations/006_shopee_origem.sql
-- Adiciona suporte ao canal SHOPEE em pedidos

-- Remove constraint antiga (apenas ML e COMERCIAL)
ALTER TABLE pedidos DROP CONSTRAINT IF EXISTS chk_pedidos_origem;

-- Recria incluindo SHOPEE
ALTER TABLE pedidos ADD CONSTRAINT chk_pedidos_origem
    CHECK (origem IN ('ML', 'COMERCIAL', 'SHOPEE'));

-- Coluna para rastrear o order_sn nativo da Shopee
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS shopee_order_sn TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pedidos_shopee_sn
    ON pedidos (shopee_order_sn)
    WHERE shopee_order_sn IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pedidos_shopee_sn
    ON pedidos (shopee_order_sn)
    WHERE shopee_order_sn IS NOT NULL;
