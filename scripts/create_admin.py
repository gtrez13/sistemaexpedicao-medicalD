#!/usr/bin/env python3
"""
scripts/create_admin.py -- Cria o primeiro usuario admin do WMS.
Uso: python scripts/create_admin.py
"""
import sys
import os
import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db import init_db
from services.user_service import create_user, get_user_by_username


def main():
    init_db()  # auto-selects SQLite or PostgreSQL based on DATABASE_URL

    print("=== Criar usuario admin WMS ===")
    username  = input("Username: ").strip()
    email     = input("E-mail: ").strip()
    full_name = input("Nome completo (opcional): ").strip()
    password  = getpass.getpass("Senha (min 10 chars, upper, lower, digito): ")
    password2 = getpass.getpass("Confirmar senha: ")

    if password != password2:
        print("Senhas nao coincidem.")
        sys.exit(1)

    existing = get_user_by_username(username)
    if existing:
        print(f"Usuario '{username}' ja existe (role={existing.role}). Abortando.")
        sys.exit(1)

    try:
        user = create_user(username, email, password, full_name=full_name, role="admin")
        print(f"Admin criado: {user.username} (id={user.id})")
    except ValueError as e:
        print(f"Erro de validacao: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
