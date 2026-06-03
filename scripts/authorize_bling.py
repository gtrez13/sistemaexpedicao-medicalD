# scripts/authorize_bling.py — versão debug completa
import os, webbrowser, base64, secrets, requests, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID     = os.getenv("BLING_CLIENT_ID")
CLIENT_SECRET = os.getenv("BLING_CLIENT_SECRET")
REDIRECT_URI  = "http://127.0.0.1:5000/auth/callback"
STATE         = secrets.token_hex(16)
code_received = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global code_received
        params = parse_qs(urlparse(self.path).query)
        if "error" in params:
            print(f"❌ Bling erro: {params.get('error_description', ['?'])[0]}")
            self.send_response(400); self.end_headers()
            return
        code_received = params.get("code", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"<h1>Autorizado! Pode fechar.</h1>")
        print(f"✅ Código: {code_received}")
    def log_message(self, *a): pass

url = (
    f"https://www.bling.com.br/Api/v3/oauth/authorize"
    f"?response_type=code&client_id={CLIENT_ID}"
    f"&redirect_uri={quote(REDIRECT_URI, safe='')}&state={STATE}"
)
print(f"Abrindo: {url}\n")
webbrowser.open(url)
HTTPServer(("127.0.0.1", 5000), Handler).handle_request()

if not code_received:
    print("❌ Nenhum código recebido"); exit(1)

creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
r = requests.post(
    "https://www.bling.com.br/Api/v3/oauth/token",
    headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
    data={"grant_type": "authorization_code", "code": code_received, "redirect_uri": REDIRECT_URI},
    timeout=15
)

print(f"\n--- Resposta do Bling ---")
print(f"Status: {r.status_code}")
print(f"Body:   {r.text}\n")

if r.status_code != 200 or not r.text.strip():
    print(f"❌ Erro {r.status_code}.")
    if r.status_code == 429:
        print("   Rate limit do Cloudflare. Aguarda 15-30min e tenta de novo.")
    elif r.status_code == 400:
        print("   redirect_uri ou código inválido.")
    exit(1)
    print("❌ Body vazio. Causas prováveis:")
    print("   1. redirect_uri não bate com o registrado no painel Bling")
    print("   2. CLIENT_ID / CLIENT_SECRET errado")
    print("   3. Código expirou ou já foi usado (rodar o script de novo)")
    exit(1)

data = r.json()
if "access_token" not in data:
    print(f"❌ Sem token: {data}"); exit(1)

env = open(".env").read()
for k, v in [("BLING_ACCESS_TOKEN", data["access_token"]), ("BLING_REFRESH_TOKEN", data["refresh_token"])]:
    env = re.sub(rf"^{k}=.*$", f"{k}={v}", env, flags=re.MULTILINE) if k in env else env + f"\n{k}={v}"
open(".env", "w").write(env)

print(f"✅ access_token:  {data['access_token'][:50]}...")
print(f"✅ refresh_token: {data['refresh_token'][:50]}...")
print(f"✅ Salvo no .env!")

# ── Atualiza bling_tokens.json (usado pelo BlingService em runtime) ───────────
import json, time, pathlib

token_file = pathlib.Path(__file__).parent.parent / "bling_tokens.json"
try:
    existing = {}
    if token_file.exists():
        try:
            existing = json.loads(token_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update({
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in":    data.get("expires_in", 21600),
        "expires_at":    time.time() + data.get("expires_in", 21600),
    })
    token_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {token_file.name} atualizado!")
except Exception as e:
    print(f"⚠️  {token_file.name} não atualizado: {e}")

# ── UPSERT no banco (para quando o BlingService migrar pro DB) ────────────────
from datetime import datetime, timedelta

db_url = os.getenv("DATABASE_URL")
if db_url:
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO bling_tokens (instance, access_token, refresh_token, expires_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (instance) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                updated_at    = NOW()
        """, (
            'principal',
            data['access_token'],
            data['refresh_token'],
            datetime.utcnow() + timedelta(seconds=data.get('expires_in', 21600)),
        ))
        conn.close()
        print("✅ Tokens salvos no banco também!")
    except Exception as e:
        print(f"⚠️  Banco não atualizado: {e} (tokens estão no .env e no JSON)")

print("\nAgora pode subir o Flask de novo.")