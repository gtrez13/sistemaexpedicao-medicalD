-- migrations/008_pedidos_bling_numero.sql
-- Coluna para o número do pedido de venda Bling (ex: "47573")
-- Garante idempotência no sync de vendas internas

ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS bling_numero TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pedidos_bling_numero
    ON pedidos (bling_numero)
    WHERE bling_numero IS NOT NULL;
