import os, json
from dotenv import load_dotenv
load_dotenv()
from services.bling_service import BlingService

bling = BlingService()
det = bling._get_json("/nfe/25866856228")
obj = det.get("data") or det

print("=== LINKS/PDF ===")
print(json.dumps(
    {k: v for k, v in obj.items() if any(x in k.lower() for x in ["link", "danfe", "pdf", "simpl", "etiqueta"])},
    indent=2, ensure_ascii=False
))

print("\n=== TODAS AS KEYS ===")
print(list(obj.keys()))