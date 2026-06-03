"""
services/user_service.py — Gerenciamento de usuários internos do WMS.
Compatível com Flask-Login.
"""
from __future__ import annotations
import bcrypt
from db import get_db


# ─── Modelo Flask-Login ──────────────────────────────────────────────────────

class UserModel:
    """Wrapper leve de row do banco, compatível com Flask-Login."""

    def __init__(self, row: dict) -> None:
        self.id        = row["id"]
        self.username  = row["username"]
        self.email     = row["email"]
        self.full_name = row.get("full_name") or ""
        self.role      = row["role"]
        self._active   = row["active"]

    # ── Flask-Login protocol ──────────────────────────────────
    @property
    def is_authenticated(self) -> bool: return True
    @property
    def is_active(self) -> bool:        return bool(self._active)
    @property
    def is_anonymous(self) -> bool:     return False
    def get_id(self) -> str:            return str(self.id)

    def to_dict(self) -> dict:
        return {
            "id":        self.id,
            "username":  self.username,
            "email":     self.email,
            "full_name": self.full_name,
            "role":      self.role,
            "active":    bool(self._active),
        }


# ─── Senha ───────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def validate_password(password: str, username: str = "") -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Senha precisa de no mínimo 8 caracteres"
    if not any(c.isupper() for c in password):
        return False, "Precisa de pelo menos 1 letra maiúscula"
    if not any(c.islower() for c in password):
        return False, "Precisa de pelo menos 1 letra minúscula"
    if not any(c.isdigit() for c in password):
        return False, "Precisa de pelo menos 1 número"
    if username and username.lower() in password.lower():
        return False, "Senha não pode conter o username"
    return True, "OK"


# ─── Lookups ──────────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> UserModel | None:
    db = get_db()
    try:
        row = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return UserModel(row) if row else None
    finally:
        db.close()


def get_user_by_username(username: str) -> UserModel | None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return UserModel(row) if row else None
    finally:
        db.close()


def list_users() -> list[dict]:
    db = get_db()
    try:
        rows = db.execute("""
            SELECT id, username, email, full_name, role, active, created_at, last_login
            FROM users
            ORDER BY id
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


# ─── Criação / autenticação ───────────────────────────────────────────────────

def create_user(
    username: str,
    email: str,
    password: str,
    full_name: str = "",
    role: str = "operator",
) -> UserModel:
    username = username.strip().lower()   # normaliza: sempre minúsculo
    email    = email.strip().lower()

    valid, msg = validate_password(password, username)
    if not valid:
        raise ValueError(msg)

    if role not in ("admin", "supervisor", "operator", "viewer"):
        raise ValueError(f"Role inválido: {role}")

    pw_hash = hash_password(password)
    db = get_db()
    try:
        db.execute("""
            INSERT INTO users (username, email, password_hash, full_name, role)
            VALUES (?, ?, ?, ?, ?)
        """, (username, email, pw_hash, full_name.strip(), role))
        db.commit()
        row = db.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        return UserModel(row)
    finally:
        db.close()


def authenticate(username: str, password: str) -> UserModel | None:
    db = get_db()
    try:
        # LOWER() para case-insensitive: "Gabriel" e "gabriel" encontram a mesma conta
        row = db.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND active = ?",
            (username.strip(), True),
        ).fetchone()
    finally:
        db.close()

    if not row:
        return None
    if not check_password(password, row["password_hash"]):
        return None

    # Atualiza last_login
    try:
        db2 = get_db()
        db2.execute(
            "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
            (row["id"],),
        )
        db2.commit()
        db2.close()
    except Exception as e:
        print(f"⚠️ Falha ao atualizar last_login: {e}", flush=True)

    return UserModel(row)


def update_user(user_id: int, **fields) -> bool:
    """Atualiza campos permitidos de um usuário. Retorna True se encontrou."""
    allowed = {"full_name", "email", "role", "active"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db = get_db()
    try:
        cur = db.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            (*updates.values(), user_id),
        )
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def change_password(user_id: int, new_password: str, username: str = "") -> None:
    valid, msg = validate_password(new_password, username)
    if not valid:
        raise ValueError(msg)
    pw_hash = hash_password(new_password)
    db = get_db()
    try:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (pw_hash, user_id),
        )
        db.commit()
    finally:
        db.close()
