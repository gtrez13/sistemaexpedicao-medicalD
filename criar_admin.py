import os
from dotenv import load_dotenv
load_dotenv()

from db import get_db
import bcrypt

db = get_db()
senha = bcrypt.hashpw('Medicald@2026'.encode(), bcrypt.gensalt()).decode()
db.execute("""
INSERT INTO users (username, email, full_name, password_hash, role, active)
VALUES ('admin', 'admin@medicald.com', 'Admin MEDICALD', %s, 'admin', true)
ON CONFLICT (username) DO UPDATE SET 
    role='admin', 
    active=true, 
    password_hash=EXCLUDED.password_hash
""", (senha,))
db.commit()
db.close()
print('Admin criado! Login: admin / Medicald@2026')