# scripts/testar_melhor_envio.py
import os, requests
from dotenv import load_dotenv
load_dotenv()

token = os.getenv("MELHOR_ENVIO_TOKEN", "").strip()
cep_origem = os.getenv("CEP_ORIGEM", "").strip().replace("-", "")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "LHVMED WMS contato@lhvmed.com.br"
}

# Cotação de teste: 1kg, 20x20x20cm, R$100 de valor
payload = {
    "from": {"postal_code": cep_origem},
    "to":   {"postal_code": "01310100"},  # Av. Paulista SP
    "products": [{
        "id": "teste",
        "width": 20, "height": 20, "length": 20,
        "weight": 1,
        "insurance_value": 100,
        "quantity": 1
    }]
}

r = requests.post(
    "https://melhorenvio.com.br/api/v2/me/shipment/calculate",
    json=payload,
    headers=headers,
    timeout=15
)

print(f"Status: {r.status_code}")

if r.status_code == 200:
    cotacoes = r.json()
    print(f"\n✅ {len(cotacoes)} transportadoras retornadas:\n")
    for c in cotacoes:
        if c.get("error"):
            print(f"  ❌ {c['company']['name']} — {c['error']}")
        else:
            print(f"  ✅ {c['company']['name']} — {c['name']} — R${c['price']} — {c['delivery_time']} dias úteis")
else:
    print(f"Erro: {r.text}")