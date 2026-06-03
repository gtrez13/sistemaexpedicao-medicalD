-- Prazo máximo de envio: ML (lead_time do shipment) e Shopee (ship_by_date)
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS prazo_envio  TIMESTAMPTZ;
ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS ship_by_date TIMESTAMPTZ;
