"""
scripts/authorize_shopee.py — Fluxo OAuth Shopee Open Platform
Obtém access_token + refresh_token e persiste no .env e no banco.

Uso:
    python scripts/authorize_shopee.py

Requisitos no .env (ou hardcode abaixo):
    SHOPEE_PARTNER_ID
    SHOPEE_PARTNER_KEY
"""
import os
import re
import sys
import hmac
import time
import json
import hashlib
import pathlib
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, quote

from dotenv import load_dotenv

# ── resolve raiz do projeto (dois níveis acima de scripts/) ──────────────────
ROOT    = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH, override=True)

# ── credenciais ───────────────────────────────────────────────────────────────
_pid = os.getenv("SHOPEE_PARTNER_ID")
_key = os.getenv("SHOPEE_PARTNER_KEY")
if not _pid or not _key:
    print("ERRO: SHOPEE_PARTNER_ID e SHOPEE_PARTNER_KEY devem estar no .env")
    sys.exit(1)
PARTNER_ID  = int(_pid)
PARTNER_KEY = _key
REDIRECT_URL = "https://lhvmed.com.br"

# A Shopee unificou sandbox e produção no mesmo endpoint.
# O partner_id de teste (1234639) já diferencia o ambiente.
BASE_URL = "https://partner.shopeemobile.com"

print(f"\n{'='*50}")
print(f"  Shopee OAuth")
print(f"  partner_id: {PARTNER_ID}")
print(f"  base_url  : {BASE_URL}")
print(f"{'='*50}\n")


# ── helpers de assinatura ─────────────────────────────────────────────────────

def _sign_public(path: str, timestamp: int) -> str:
    """Assinatura para endpoints sem shop_id/access_token (ex: auth URL inicial)."""
    base = f"{PARTNER_ID}{path}{timestamp}"
    return hmac.new(
        PARTNER_KEY.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ── PASSO 1: gera URL de autorização ─────────────────────────────────────────

path_auth = "/api/v2/shop/auth_partner"
ts        = int(time.time())
sign      = _sign_public(path_auth, ts)

auth_url = (
    f"{BASE_URL}{path_auth}"
    f"?partner_id={PARTNER_ID}"
    f"&timestamp={ts}"
    f"&sign={sign}"
    f"&redirect={quote(REDIRECT_URL, safe='')}"
)

print(f"Abrindo URL de autorização no browser...\n{auth_url}\n")
webbrowser.open(auth_url)

# ── PASSO 2: usuário cola a URL de redirect ───────────────────────────────────

print("Após autorizar o app na Shopee:")
print(f"  -> a pagina {REDIRECT_URL} vai abrir normalmente")
print(f"  → a URL na barra do browser vai ter ?code=...&shop_id=... no final\n")
print("Copie a URL COMPLETA da barra do browser e cole aqui:\n")

try:
    raw_url = input("URL: ").strip()
except KeyboardInterrupt:
    print("\n❌ Cancelado.")
    sys.exit(1)

if not raw_url:
    print("❌ URL vazia. Tente novamente.")
    sys.exit(1)

parsed = urlparse(raw_url)
params = parse_qs(parsed.query)

if "error" in params:
    err = params.get("error", ["?"])[0]
    desc = params.get("error_description", [err])[0]
    print(f"\n❌ Shopee retornou erro: {desc}")
    sys.exit(1)

code_list    = params.get("code",    [])
shop_id_list = params.get("shop_id", [])

if not code_list or not shop_id_list:
    print(f"\n❌ Não encontrei 'code' ou 'shop_id' na URL: {raw_url}")
    print("   Certifique-se de copiar a URL COMPLETA incluindo os parâmetros.")
    sys.exit(1)

code    = code_list[0]
shop_id = int(shop_id_list[0])
print(f"\n✅ code     : {code[:30]}...")
print(f"✅ shop_id  : {shop_id}")

# ── PASSO 3: troca code por access_token ─────────────────────────────────────

import requests

path_token = "/api/v2/auth/token/get"
ts2        = int(time.time())
sign2      = _sign_public(path_token, ts2)

url_token = f"{BASE_URL}{path_token}"
headers   = {"Content-Type": "application/json"}
payload   = {
    "code":       code,
    "shop_id":    shop_id,
    "partner_id": PARTNER_ID,
}
query = {
    "partner_id": PARTNER_ID,
    "timestamp":  ts2,
    "sign":       sign2,
}

print(f"\nTrocando code por tokens em {url_token}...")

r = requests.post(url_token, params=query, headers=headers, json=payload, timeout=15)

print(f"Status: {r.status_code}")

if r.status_code != 200:
    print(f"❌ Falha na troca de tokens: {r.status_code} — {r.text}")
    if r.status_code == 400:
        print("   Causas comuns:")
        print("   - O 'code' já foi usado (é de uso único — rerun o script)")
        print("   - O 'code' expirou (válido por ~300s — refaça a autorização no browser)")
    sys.exit(1)

try:
    data = r.json()
except Exception:
    print(f"❌ Resposta não é JSON válido: {r.text[:300]}")
    sys.exit(1)

if data.get("error") or not data.get("access_token"):
    print(f"❌ Resposta com erro: {data}")
    sys.exit(1)

access_token  = data["access_token"]
refresh_token = data["refresh_token"]
expires_in    = int(data.get("expire_in") or data.get("expires_in") or 14400)

print(f"\n✅ access_token  : {access_token[:20]}...")
print(f"✅ refresh_token : {refresh_token[:20]}...")
print(f"✅ expires_in    : {expires_in}s ({expires_in // 3600}h)")

# ── PASSO 4: salva no .env ───────────────────────────────────────────────────

env_text = ENV_PATH.read_text(encoding="utf-8")

updates = {
    "SHOPEE_PARTNER_ID":   str(PARTNER_ID),
    "SHOPEE_PARTNER_KEY":  PARTNER_KEY,
    "SHOPEE_SHOP_ID":      str(shop_id),
    "SHOPEE_ACCESS_TOKEN": access_token,
    "SHOPEE_REFRESH_TOKEN": refresh_token,
}

for key, val in updates.items():
    if re.search(rf"^{key}\s*=", env_text, flags=re.MULTILINE):
        env_text = re.sub(
            rf"^{key}\s*=.*$",
            f"{key}={val}",
            env_text,
            flags=re.MULTILINE,
        )
    else:
        env_text += f"\n{key}={val}"

ENV_PATH.write_text(env_text, encoding="utf-8")
print(f"\n✅ .env atualizado! ({ENV_PATH})")

# ── PASSO 5: salva no banco ───────────────────────────────────────────────────

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("⚠️  DATABASE_URL não configurado — tokens salvos apenas no .env.")
else:
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()

        # Cria a tabela se não existir
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shopee_tokens (
                id            SERIAL      PRIMARY KEY,
                shop_id       BIGINT      UNIQUE NOT NULL,
                access_token  TEXT        NOT NULL,
                refresh_token TEXT        NOT NULL,
                expires_at    TIMESTAMPTZ NOT NULL,
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        cur.execute("""
            INSERT INTO shopee_tokens (shop_id, access_token, refresh_token, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (shop_id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                updated_at    = NOW()
        """, (shop_id, access_token, refresh_token, expires_at))

        conn.close()
        print("✅ Tokens salvos no banco (shopee_tokens)!")

    except Exception as e:
        print(f"⚠️  Banco não atualizado: {e}")
        print("   Tokens estão seguros no .env — o sync funcionará normalmente.")

# ── PASSO 6: salva bling_tokens.json (compatibilidade) ───────────────────────

token_file = ROOT / "shopee_tokens.json"
try:
    existing = {}
    if token_file.exists():
        try:
            existing = json.loads(token_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update({
        "partner_id":    PARTNER_ID,
        "shop_id":       shop_id,
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "expires_in":    expires_in,
        "expires_at":    time.time() + expires_in,
    })
    token_file.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"✅ {token_file.name} atualizado!")
except Exception as e:
    print(f"⚠️  {token_file.name} não atualizado: {e}")

print(f"\n{'='*50}")
print("  Autorização Shopee concluída!")
print(f"  shop_id: {shop_id}")
print(f"  Agora pode subir o Flask e testar:")
print(f"  POST /api/sync/shopee")
print(f"{'='*50}\n")
