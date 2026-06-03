"""
services/auth_utils.py -- Utilitarios de autenticacao/autorizacao.

- require_role(*roles): decorator RBAC sobre @login_required
- RateLimiter: controle de tentativas de login por IP + lockout por username
"""
from __future__ import annotations

import time
from collections import defaultdict
from functools import wraps

from flask import jsonify
from flask_login import login_required, current_user


# ─── RBAC ────────────────────────────────────────────────────────────────────

def require_role(*roles: str):
    """
    Decorator que exige autenticacao E que o usuario tenha um dos roles indicados.

    Uso:
        @require_role("admin", "supervisor")
        def minha_rota(): ...
    """
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                return jsonify({"error": "Permissao insuficiente", "required_roles": list(roles)}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ─── Rate limiter + lockout ───────────────────────────────────────────────────

class _LoginRateLimiter:
    """
    Controle de tentativas de login em memoria.
    Resets ao reiniciar o servidor (aceitavel para WMS interno de equipe pequena).

    - Por IP:       maximo MAX_PER_IP tentativas em WINDOW_S segundos
    - Por username: apos LOCKOUT_ATTEMPTS falhas, bloqueia LOCKOUT_S segundos
    """

    MAX_PER_IP       = 20     # 20 tentativas por IP a cada 5 min
    WINDOW_S         = 300    # janela de contagem (segundos)
    LOCKOUT_ATTEMPTS = 5      # falhas consecutivas por username ate bloquear
    LOCKOUT_S        = 900    # 15 minutos de lockout

    def __init__(self):
        self._ip_times: dict[str, list[float]] = defaultdict(list)
        self._user_fails: dict[str, list[float]] = defaultdict(list)
        self._user_locked_until: dict[str, float] = {}

    def _clean_window(self, lst: list[float]) -> list[float]:
        cutoff = time.monotonic() - self.WINDOW_S
        return [t for t in lst if t > cutoff]

    def check_ip(self, ip: str) -> tuple[bool, str]:
        """Retorna (permitido, mensagem_erro)."""
        now = time.monotonic()
        self._ip_times[ip] = self._clean_window(self._ip_times[ip])
        if len(self._ip_times[ip]) >= self.MAX_PER_IP:
            wait = int(self.WINDOW_S - (now - self._ip_times[ip][0]))
            return False, f"Muitas tentativas deste IP. Tente em {wait}s."
        return True, ""

    def check_user(self, username: str) -> tuple[bool, str]:
        """Retorna (permitido, mensagem_erro)."""
        now = time.monotonic()
        until = self._user_locked_until.get(username, 0)
        if now < until:
            wait = int(until - now)
            return False, f"Conta bloqueada. Tente novamente em {wait}s."
        return True, ""

    def record_attempt(self, ip: str):
        self._ip_times[ip].append(time.monotonic())

    def record_fail(self, username: str):
        now = time.monotonic()
        self._user_fails[username] = self._clean_window(self._user_fails[username])
        self._user_fails[username].append(now)
        if len(self._user_fails[username]) >= self.LOCKOUT_ATTEMPTS:
            self._user_locked_until[username] = now + self.LOCKOUT_S
            self._user_fails[username] = []

    def record_success(self, username: str):
        self._user_fails.pop(username, None)
        self._user_locked_until.pop(username, None)


# Singleton — partilhado entre todas as requests do processo
login_limiter = _LoginRateLimiter()
