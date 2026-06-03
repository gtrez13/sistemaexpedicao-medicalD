import os
import json
import base64
import secrets
import urllib.parse
import requests
import time

from flask import Blueprint, session, redirect, request, jsonify
from dotenv import load_dotenv

load_dotenv()

auth_routes = Blueprint("auth_routes", __name__)

AUTH_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


# ---------------------------------------------------
# LOGIN → REDIRECIONA PARA BLING
# ---------------------------------------------------
@auth_routes.get("/auth/login")
def bling_login():

    client_id = (os.getenv("BLING_CLIENT_ID") or "").strip()
    redirect_uri = (os.getenv("BLING_REDIRECT_URI") or "").strip()

    if not client_id or not redirect_uri:
        return jsonify({
            "success": False,
            "error": "missing_env",
            "detail": "BLING_CLIENT_ID ou BLING_REDIRECT_URI ausentes"
        }), 400

    state = secrets.token_hex(16)
    session["bling_oauth_state"] = state

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }

    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    return redirect(url)


# ---------------------------------------------------
# CALLBACK → TROCA CODE POR TOKEN
# ---------------------------------------------------
@auth_routes.get("/auth/callback")
def bling_callback():

    if request.args.get("error"):
        return jsonify({
            "success": False,
            "error": request.args.get("error"),
            "error_description": request.args.get("error_description"),
        }), 400

    code = (request.args.get("code") or "").strip()
    state = (request.args.get("state") or "").strip()

    expected_state = session.get("bling_oauth_state")
    session.pop("bling_oauth_state", None)

    if expected_state and (not state or state != expected_state):
        return jsonify({
            "success": False,
            "error": "state_invalid"
        }), 400

    client_id = (os.getenv("BLING_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("BLING_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.getenv("BLING_REDIRECT_URI") or "").strip()

    if not code:
        return jsonify({
            "success": False,
            "error": "missing_code"
        }), 400

    headers = {
        "Authorization": f"Basic {_basic_auth(client_id, client_secret)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    try:

        r = requests.post(
            TOKEN_URL,
            headers=headers,
            data=payload,
            timeout=(10, 60)
        )

    except Exception as e:

        return jsonify({
            "success": False,
            "error": "request_failed",
            "detail": str(e)
        }), 500

    if r.status_code not in (200, 201):

        return jsonify({
            "success": False,
            "error": "token_exchange_failed",
            "status": r.status_code,
            "detail": r.text
        }), 400

    tokens = r.json() or {}

    # -----------------------------
    # caminho do arquivo de tokens
    # -----------------------------
    BASE_DIR = os.path.dirname(os.path.dirname(__file__))
    token_file = os.path.join(BASE_DIR, "bling_tokens.json")

    # -----------------------------
    # preservar refresh_token antigo
    # -----------------------------
    old_tokens = {}

    if os.path.exists(token_file):

        try:
            with open(token_file, "r", encoding="utf-8") as f:
                old_tokens = json.load(f)
        except Exception:
            old_tokens = {}

    if "refresh_token" not in tokens and "refresh_token" in old_tokens:
        tokens["refresh_token"] = old_tokens["refresh_token"]

    # -----------------------------
    # calcular expiração
    # -----------------------------
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 21600))

    # -----------------------------
    # salvar tokens
    # -----------------------------
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)

    return jsonify({"ok": True})