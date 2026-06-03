"""
services/audit_service.py — Registro de auditoria de ações do WMS.

Uso:
    from services.audit_service import log_action, audited

    # Direto:
    log_action(user_id=1, action='bipar', entity_type='pedido', entity_id='123')

    # Decorator:
    @audited('deletar', 'pedido')
    def deletar_pedido(ml_id): ...
"""
from __future__ import annotations
import json
import time
from functools import wraps


def log_action(
    user_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict | None = None,
    success: bool = True,
    error_message: str | None = None,
    duration_ms: int | None = None,
    ip_address: str | None = None,
) -> None:
    """Grava uma linha em audit_log. Falha silenciosa para não quebrar o fluxo principal."""
    try:
        from db import get_db
        db = get_db()
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        db.execute("""
            INSERT INTO audit_log
                (user_id, action, entity_type, entity_id,
                 metadata, success, error_message, duration_ms, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            meta_str,
            success,
            error_message,
            duration_ms,
            ip_address,
        ))
        db.commit()
        db.close()
    except Exception as exc:
        print(f"⚠️ audit_log falhou [{action}]: {exc}", flush=True)


def _current_user_id() -> int | None:
    """Retorna o id do user Flask-Login atual, ou None se não autenticado."""
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return None


def _client_ip() -> str | None:
    try:
        from flask import request
        return request.remote_addr
    except Exception:
        return None


def audited(action: str, entity_type: str | None = None):
    """
    Decorator que registra a chamada em audit_log automaticamente.

    Captura user_id do Flask-Login e ip do request.
    Entidade: passa entity_id como kwarg da função decorada, ou via request.view_args.

    Uso:
        @audited('deletar', 'pedido')
        def deletar_pedido(ml_id): ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid     = _current_user_id()
            ip      = _client_ip()
            eid     = _extract_entity_id(kwargs)
            start   = time.monotonic()

            try:
                result   = fn(*args, **kwargs)
                duration = int((time.monotonic() - start) * 1000)
                log_action(
                    user_id=uid, action=action, entity_type=entity_type,
                    entity_id=eid, success=True,
                    duration_ms=duration, ip_address=ip,
                )
                return result
            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                log_action(
                    user_id=uid, action=action, entity_type=entity_type,
                    entity_id=eid, success=False,
                    error_message=str(exc), duration_ms=duration, ip_address=ip,
                )
                raise

        return wrapper
    return decorator


def _extract_entity_id(kwargs: dict) -> str | None:
    """Tenta extrair entity_id de kwargs típicos de rotas Flask."""
    for key in ("ml_id", "pedido_id", "sku", "user_id", "id"):
        if key in kwargs:
            return str(kwargs[key])
    return None
