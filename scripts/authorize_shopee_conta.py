"""
scripts/authorize_shopee_conta.py — OAuth Shopee para múltiplas contas

Uso:
    python scripts/authorize_shopee_conta.py "LHVMED 2"

Salva na tabela contas_marketplace com o nome informado.
"""
import os, sys, hmac, time, hashlib, webbrowser, pathlib, requests
from urllib.parse import urlparse, parse_qs, quote
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

NOME        = sys.argv[1] if len(sys.argv) > 1 else "LHVMED 1"
PARTNER_ID  = int(os.getenv("SHOPEE_PARTNER_ID") or 0)
PARTNER_KEY = os.getenv("SHOPEE_PARTNER_KEY") or ""
REDIRECT_URL = "https://lhvmed.com.br"
BASE_URL    = "https://partner.shopeemobile.com"

if not PARTNER_ID or not PARTNER_KEY:
    print("ERRO: SHOPEE_PARTNER_ID e SHOPEE_PARTNER_KEY devem estar no .env")
    sys.exit(1)

print(f"\n{'='*50}")
print(f"  OAuth Shopee — conta: {NOME}")
print(f"  partner_id: {PARTNER_ID}")
print(f"{'='*50}\n")

path_auth = "/api/v2/shop/auth_partner"
ts        = int(time.time())
base      = f"{PARTNER_ID}{path_auth}{ts}"
sign      = hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()

auth_url = (
    f"{BASE_URL}{path_auth}"
    f"?partner_id={PARTNER_ID}&timestamp={ts}&sign={sign}"
    f"&redirect={quote(REDIRECT_URL, safe='')}"
)
print(f"Abrindo URL de autorizacao...\n{auth_url}\n")
webbrowser.open(auth_url)

print(f"Apos autorizar, a pagina {REDIRECT_URL} vai abrir.")
print("Cole a URL completa da barra do browser:\n")
try:
    raw_url = input("URL: ").strip()
except KeyboardInterrupt:
    print("\nCancelado.")
    sys.exit(1)

parsed   = urlparse(raw_url)
params   = parse_qs(parsed.query)
codes    = params.get("code", [])
shop_ids = params.get("shop_id", [])

if not codes or not shop_ids:
    print(f"Nao encontrei 'code' ou 'shop_id' na URL: {raw_url}")
    sys.exit(1)

code    = codes[0]
shop_id = int(shop_ids[0])
print(f"code    : {code[:20]}...")
print(f"shop_id : {shop_id}")

# Troca code por token
path_token = "/api/v2/auth/token/get"
ts2  = int(time.time())
base2 = f"{PARTNER_ID}{path_token}{ts2}"
sign2 = hmac.new(PARTNER_KEY.encode(), base2.encode(), hashlib.sha256).hexdigest()

r = requests.post(
    f"{BASE_URL}{path_token}",
    params={"partner_id": PARTNER_ID, "timestamp": ts2, "sign": sign2},
    json={"code": code, "shop_id": shop_id, "partner_id": PARTNER_ID},
    headers={"Content-Type": "application/json"},
    timeout=15,
)
print(f"Status: {r.status_code}")
if r.status_code != 200:
    print(f"Falha: {r.text[:300]}")
    sys.exit(1)

data = r.json()
if not data.get("access_token"):
    print(f"Erro na resposta: {data}")
    sys.exit(1)

access_token  = data["access_token"]
refresh_token = data["refresh_token"]
expires_in    = int(data.get("expire_in") or data.get("expires_in") or 14400)
print(f"access_token  : {access_token[:20]}...")
print(f"refresh_token : {refresh_token[:20]}...")

# Salva no banco
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("DATABASE_URL nao configurado.")
    sys.exit(0)

import psycopg2
from datetime import datetime, timezone, timedelta
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
cur.execute("""
    INSERT INTO contas_marketplace
        (plataforma, nome, shopee_shop_id, shopee_access_token, shopee_refresh_token, shopee_expires_at)
    VALUES ('SHOPEE', %s, %s, %s, %s, %s)
    ON CONFLICT (plataforma, nome) DO UPDATE SET
        shopee_shop_id       = EXCLUDED.shopee_shop_id,
        shopee_access_token  = EXCLUDED.shopee_access_token,
        shopee_refresh_token = EXCLUDED.shopee_refresh_token,
        shopee_expires_at    = EXCLUDED.shopee_expires_at,
        updated_at           = NOW()
""", (NOME, shop_id, access_token, refresh_token, expires_at))
conn.close()

print(f"\nConta '{NOME}' (shop_id={shop_id}) salva em contas_marketplace.")
print(f"O sync Shopee vai incluir essa conta automaticamente.\n")
