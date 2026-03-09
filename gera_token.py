import requests
import json

# --- PREENCHA AQUI ---
CLIENT_ID = "2237092898339315"
CLIENT_SECRET = "4sudgbnDgJB65RZrikxyHih134oq7kZX" # Pegue no Painel do DevCenter
CODE = "TG-69a0473483622e0001fe2c7c-1786005151"
REDIRECT_URI = "https://www.google.com.br"

url = "https://api.mercadolibre.com/oauth/token"

payload = {
    'grant_type': 'authorization_code',
    'client_id': CLIENT_ID,
    'client_secret': CLIENT_SECRET,
    'code': CODE,
    'redirect_uri': REDIRECT_URI
}

headers = {'Content-Type': 'application/x-www-form-urlencoded'}

response = requests.post(url, data=payload, headers=headers)

if response.status_code == 200:
    tokens = response.json()
    with open('meli_tokens.json', 'w') as f:
        json.dump(tokens, f, indent=4)
    print("✅ Sucesso! Arquivo meli_tokens.json criado.")
else:
    print(f"❌ Erro: {response.status_code}")
    print(response.text)