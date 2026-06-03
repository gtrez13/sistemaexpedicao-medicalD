-- migrations/003_origem_produtos_dim.sql
-- Tarefa 4: coluna origem em pedidos (ML | COMERCIAL)
-- Tarefa 5: dimensoes de produto para cotacao de frete (Melhor Envio)

-- ── pedidos.origem ────────────────────────────────────────────────────────────
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS origem TEXT NOT NULL DEFAULT 'ML';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_pedidos_origem'
          AND conrelid = 'pedidos'::regclass
    ) THEN
        ALTER TABLE pedidos ADD CONSTRAINT chk_pedidos_origem
            CHECK (origem IN ('ML', 'COMERCIAL'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pedidos_origem ON pedidos (origem);

-- ── produtos: dimensoes para cotacao de frete ─────────────────────────────────
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS peso_kg         NUMERIC(8,3);
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS altura_cm       NUMERIC(6,1);
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS largura_cm      NUMERIC(6,1);
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS comprimento_cm  NUMERIC(6,1);
