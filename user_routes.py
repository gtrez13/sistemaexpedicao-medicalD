"""
user_routes.py — Autenticação e gestão de usuários internos do WMS.
Rotas sob /api/auth/* (distinto do Bling OAuth em /auth/*).
"""
from __future__ import annotations
import os
import secrets
import traceback
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from flask_login import login_user, logout_user, login_required, current_user

from db import get_db
from services.user_service import (
    authenticate, create_user, get_user_by_id,
    list_users, update_user, change_password,
    hash_password, validate_password,
)
from services.audit_service import log_action
from services.auth_utils import login_limiter
from services.email_service import enviar_email_reset

user_routes = Blueprint("user_routes", __name__, url_prefix="/api/auth")


# ── POST /api/auth/login ──────────────────────────────────────────────────────

@user_routes.post("/login")
def api_login():
    ip       = request.remote_addr or "unknown"
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")

    if not username or not password:
        return jsonify({"error": "username e password são obrigatórios"}), 400

    # Rate limiting: por IP
    ok_ip, msg_ip = login_limiter.check_ip(ip)
    if not ok_ip:
        return jsonify({"error": msg_ip}), 429

    # Lockout: por username
    ok_user, msg_user = login_limiter.check_user(username)
    if not ok_user:
        return jsonify({"error": msg_user}), 429

    login_limiter.record_attempt(ip)

    user = authenticate(username, password)
    if not user:
        login_limiter.record_fail(username)
        log_action(
            user_id=None, action="login_fail",
            metadata={"username": username},
            success=False, ip_address=ip,
        )
        return jsonify({"error": "Credenciais inválidas"}), 401

    login_limiter.record_success(username)
    login_user(user, remember=True)
    log_action(
        user_id=user.id, action="login",
        success=True, ip_address=ip,
    )
    return jsonify({"ok": True, "user": user.to_dict()})


# ── POST /api/auth/logout ─────────────────────────────────────────────────────

@user_routes.post("/logout")
@login_required
def api_logout():
    uid = current_user.id
    logout_user()
    log_action(user_id=uid, action="logout", ip_address=request.remote_addr)
    return jsonify({"ok": True})


# ── GET /api/auth/me ──────────────────────────────────────────────────────────

@user_routes.get("/me")
def api_me():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": current_user.to_dict()})


# ── POST /api/auth/register ───────────────────────────────────────────────────

@user_routes.post("/register")
@login_required
def api_register():
    if current_user.role not in ("admin",):
        return jsonify({"error": "Apenas admins podem criar usuários"}), 403

    data      = request.get_json(silent=True) or {}
    username  = (data.get("username") or "").strip()
    email     = (data.get("email") or "").strip()
    password  = (data.get("password") or "")
    full_name = (data.get("full_name") or "").strip()
    role      = (data.get("role") or "operator").strip()

    if not username or not email or not password:
        return jsonify({"error": "username, email e password são obrigatórios"}), 400

    try:
        user = create_user(username, email, password, full_name, role)
        log_action(
            user_id=current_user.id, action="create_user",
            entity_type="user", entity_id=str(user.id),
            metadata={"username": username, "role": role},
        )
        return jsonify({"ok": True, "user": user.to_dict()}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── GET /api/auth/users ───────────────────────────────────────────────────────

@user_routes.get("/users")
@login_required
def api_users():
    if current_user.role not in ("admin", "supervisor"):
        return jsonify({"error": "Sem permissão"}), 403

    users = list_users()
    # Não expõe password_hash
    safe = [{k: v for k, v in u.items() if k != "password_hash"} for u in users]
    return jsonify({"users": safe})


# ── PATCH /api/auth/users/<id> ────────────────────────────────────────────────

@user_routes.patch("/users/<int:user_id>")
@login_required
def api_update_user(user_id: int):
    if current_user.role != "admin":
        return jsonify({"error": "Apenas admins podem editar usuários"}), 403

    data = request.get_json(silent=True) or {}
    fields = {}
    for f in ("full_name", "email", "role", "active"):
        if f in data:
            fields[f] = data[f]

    if not fields:
        return jsonify({"error": "Nenhum campo para atualizar"}), 400

    ok = update_user(user_id, **fields)
    if not ok:
        return jsonify({"error": "Usuário não encontrado"}), 404

    log_action(
        user_id=current_user.id, action="update_user",
        entity_type="user", entity_id=str(user_id),
        metadata=list(fields.keys()),
    )
    return jsonify({"ok": True})


# ── POST /api/auth/users/<id>/password ───────────────────────────────────────

@user_routes.post("/users/<int:user_id>/password")
@login_required
def api_change_password(user_id: int):
    # Admin pode trocar senha de qualquer user; user pode trocar a própria
    if current_user.role != "admin" and current_user.id != user_id:
        return jsonify({"error": "Sem permissão"}), 403

    data     = request.get_json(silent=True) or {}
    new_pass = (data.get("password") or "")

    if not new_pass:
        return jsonify({"error": "password obrigatório"}), 400

    target = get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "Usuário não encontrado"}), 404

    try:
        change_password(user_id, new_pass, username=target.username)
        log_action(
            user_id=current_user.id, action="change_password",
            entity_type="user", entity_id=str(user_id),
        )
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# ── POST /api/auth/register-public ───────────────────────────────────────────
# Registro público protegido por código de convite (INVITE_CODE no .env).

@user_routes.post("/register-public")
def api_register_public():
    data      = request.get_json(silent=True) or {}
    username  = (data.get("username") or "").strip()
    email     = (data.get("email") or "").strip().lower()
    full_name = (data.get("full_name") or "").strip()
    password  = data.get("password") or ""

    if not username or not email or not password:
        return jsonify({"error": "Preencha todos os campos obrigatórios"}), 400

    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Email inválido"}), 400

    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (username, email),
        ).fetchone()
    finally:
        db.close()

    if existing:
        return jsonify({"error": "Usuário ou email já cadastrado"}), 409

    try:
        user = create_user(username, email, password, full_name, role="operator")
        log_action(
            user_id=user.id, action="register_public",
            entity_type="user", entity_id=str(user.id),
            metadata={"username": username},
            ip_address=request.remote_addr,
        )
        return jsonify({"success": True, "message": "Conta criada! Faça login."}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Erro interno ao criar conta"}), 500


# ── POST /api/auth/forgot-password ───────────────────────────────────────────
# Solicita link de reset. Sempre retorna 200 independente do email existir.

@user_routes.post("/forgot-password")
def api_forgot_password():
    email = (request.get_json(silent=True) or {}).get("email", "").strip().lower()
    generic_ok = {"message": "Se o email existir, enviamos um link de recuperação."}

    if not email:
        return jsonify(generic_ok), 200

    db = get_db()
    try:
        user = db.execute(
            "SELECT id, email, full_name FROM users WHERE email = ? AND active = ?",
            (email, True),
        ).fetchone()

        if user:
            token   = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=1)
            db.execute(
                "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                (user["id"], token, expires),
            )
            db.commit()

            frontend_url = os.getenv("FRONTEND_URL", "http://127.0.0.1:3000")
            reset_link   = f"{frontend_url}/reset-password?token={token}"
            try:
                enviar_email_reset(user["email"], user["full_name"] or "", reset_link)
            except Exception as e:
                print(f"⚠️ Falha ao enviar email de reset: {e}", flush=True)
    except Exception:
        traceback.print_exc()
    finally:
        db.close()

    return jsonify(generic_ok), 200


# ── POST /api/auth/reset-password ────────────────────────────────────────────
# Confirma nova senha com token válido.

@user_routes.post("/reset-password")
def api_reset_password():
    data     = request.get_json(silent=True) or {}
    token    = (data.get("token") or "").strip()
    password = data.get("password") or ""

    if not token:
        return jsonify({"error": "Token ausente"}), 400

    valid, msg = validate_password(password)
    if not valid:
        return jsonify({"error": msg}), 400

    db = get_db()
    try:
        row = db.execute(
            "SELECT id, user_id, expires_at, used FROM password_reset_tokens WHERE token = ?",
            (token,),
        ).fetchone()

        if not row or row["used"]:
            return jsonify({"error": "Link inválido ou já utilizado"}), 400

        expires_at = row["expires_at"]
        # Normaliza para timezone-aware para comparação segura
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < datetime.now(timezone.utc):
            return jsonify({"error": "Link expirado"}), 400

        # Obtém username para validação de senha
        user_row = db.execute("SELECT username FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        username = user_row["username"] if user_row else ""
        valid2, msg2 = validate_password(password, username)
        if not valid2:
            return jsonify({"error": msg2}), 400

        pw_hash = hash_password(password)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, row["user_id"]))
        db.execute("UPDATE password_reset_tokens SET used = true WHERE id = ?", (row["id"],))
        db.commit()

        return jsonify({"success": True, "message": "Senha alterada! Faça login."}), 200
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Erro interno"}), 500
    finally:
        db.close()

