"""
scripts/authorize_ml_conta.py — OAuth ML para múltiplas contas/apps

Uso:
    python scripts/authorize_ml_conta.py "SDM SAUDE"
    python scripts/authorize_ml_conta.py "LHVMED 1"

Lógica de seleção do App:
    - Nome contém "SDM"  → ML_APP_ID_SDM / ML_APP_SECRET_SDM
    - Caso contrário     → ML_APP_ID    / ML_APP_SECRET  (LHVMED 1)

Salva na tabela contas_marketplace e nos campos ML_*_SDM do .env.
"""
import os, sys, re, webbrowser, pathlib, requests
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv, set_key

ROOT     = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH, override=True)

NOME = sys.argv[1] if len(sys.argv) > 1 else "LHVMED 1"

# ── Seleciona as credenciais do App correto ────────────────────────────────────
if "SDM" in NOME.upper():
    CLIENT_ID     = os.getenv("ML_APP_ID_SDM")
    CLIENT_SECRET = os.getenv("ML_APP_SECRET_SDM")
    ENV_KEY_AT    = "ML_ACCESS_TOKEN_SDM"
    ENV_KEY_RT    = "ML_REFRESH_TOKEN_SDM"
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERRO: ML_APP_ID_SDM e ML_APP_SECRET_SDM devem estar no .env")
        sys.exit(1)
else:
    CLIENT_ID     = os.getenv("ML_APP_ID")
    CLIENT_SECRET = os.getenv("ML_APP_SECRET")
    ENV_KEY_AT    = "ML_ACCESS_TOKEN"
    ENV_KEY_RT    = "ML_REFRESH_TOKEN"
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERRO: ML_APP_ID e ML_APP_SECRET devem estar no .env")
        sys.exit(1)

REDIRECT_URI = os.getenv("ML_REDIRECT_URI") or os.getenv("ML_REDIRECT_URL") or "https://lhvmed.com.br"

print(f"\n{'='*55}")
print(f"  OAuth ML — conta : {NOME}")
print(f"  client_id        : {CLIENT_ID}")
print(f"  redirect_uri     : {REDIRECT_URI}")
print(f"{'='*55}\n")

# ── Passo 1: gera URL de autorização ─────────────────────────────────────────
auth_url = (
    f"https://auth.mercadolivre.com.br/authorization"
    f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
)
print(f"Abrindo URL de autorizacao no browser...\n{auth_url}\n")
webbrowser.open(auth_url)

print("Apos autorizar, cole a URL completa do redirect:\n")
try:
    raw_url = input("URL: ").strip()
except KeyboardInterrupt:
    print("\nCancelado.")
    sys.exit(1)

parsed = urlparse(raw_url)
params = parse_qs(parsed.query)
codes  = params.get("code", [])

if not codes:
    print(f"Nao encontrei 'code' na URL: {raw_url}")
    sys.exit(1)

code = codes[0]
print(f"code: {code[:25]}...")

# ── Passo 2: troca code por access_token ─────────────────────────────────────
r = requests.post(
    "https://api.mercadolibre.com/oauth/token",
    data={
        "grant_type":    "authorization_code",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
    },
    timeout=15,
)
print(f"Status: {r.status_code}")

if r.status_code != 200:
    print(f"Falha na troca de tokens: {r.text[:400]}")
    sys.exit(1)

data          = r.json()
access_token  = data["access_token"]
refresh_token = data["refresh_token"]
user_id       = str(data.get("user_id", ""))
expires_in    = int(data.get("expires_in", 21600))

print(f"access_token : {access_token[:25]}...")
print(f"user_id      : {user_id}")
print(f"expires_in   : {expires_in}s ({expires_in//3600}h)")

# ── Passo 3: salva no banco ───────────────────────────────────────────────────
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("DATABASE_URL nao configurado — salve manualmente.")
    sys.exit(0)

import psycopg2
from datetime import datetime, timezone, timedelta

conn = psycopg2.connect(db_url)
conn.autocommit = True
cur  = conn.cursor()

expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

cur.execute("""
    INSERT INTO contas_marketplace
        (plataforma, nome, ml_app_id, ml_app_secret,
         ml_access_token, ml_refresh_token, ml_user_id, ml_expires_at)
    VALUES ('ML', %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (plataforma, nome) DO UPDATE SET
        ml_app_id        = EXCLUDED.ml_app_id,
        ml_app_secret    = EXCLUDED.ml_app_secret,
        ml_access_token  = EXCLUDED.ml_access_token,
        ml_refresh_token = EXCLUDED.ml_refresh_token,
        ml_user_id       = EXCLUDED.ml_user_id,
        ml_expires_at    = EXCLUDED.ml_expires_at,
        updated_at       = NOW()
""", (NOME, CLIENT_ID, CLIENT_SECRET, access_token, refresh_token, user_id, expires_at))

# Confirma resultado
cur.execute("SELECT id, nome, ml_user_id, LEFT(ml_access_token,20) as tok FROM contas_marketplace WHERE plataforma='ML' ORDER BY nome")
rows = cur.fetchall()
conn.close()

print(f"\nContas ML no banco:")
for row in rows:
    print(f"  id={row[0]} | {row[1]} | user_id={row[2]} | token={row[3]}...")

# ── Passo 4: salva no .env ────────────────────────────────────────────────────
set_key(str(ENV_PATH), ENV_KEY_AT, access_token)
set_key(str(ENV_PATH), ENV_KEY_RT, refresh_token)
print(f"\n{ENV_KEY_AT} e {ENV_KEY_RT} salvos no .env")

print(f"\n{'='*55}")
print(f"  Conta '{NOME}' autorizada com sucesso!")
print(f"  O sync ML vai incluir essa conta automaticamente.")
print(f"{'='*55}\n")
