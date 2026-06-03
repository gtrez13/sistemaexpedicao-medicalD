"""
services/metrics_service.py — Sessionização inteligente de atividade de operadores.

Agrupa eventos de um usuário em "sessões de trabalho": se o intervalo entre dois
eventos superar GAP_MINUTOS, a sessão anterior é encerrada e uma nova começa.
"""
from __future__ import annotations
from datetime import datetime, timezone


GAP_MINUTOS = 30  # buraco que encerra uma sessão


def _naive_utc(ts: datetime) -> datetime:
    """Normaliza para naive UTC para comparações seguras."""
    if ts is None:
        return datetime.utcnow()
    if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def calcular_sessoes(eventos: list, gap_minutos: int = GAP_MINUTOS) -> list[dict]:
    """
    eventos: lista de datetimes (timezone-aware ou naive) de atividades do usuário.
    Retorna lista de sessões: [{inicio, fim, duracao_min, eventos}].
    """
    if not eventos:
        return []

    evs = sorted(_naive_utc(ts) for ts in eventos)
    sessoes: list[dict] = []
    ini = fim = evs[0]
    count = 1

    for ts in evs[1:]:
        if (ts - fim).total_seconds() / 60 > gap_minutos:
            sessoes.append({
                "inicio":      ini,
                "fim":         fim,
                "duracao_min": round((fim - ini).total_seconds() / 60, 1),
                "eventos":     count,
            })
            ini   = ts
            count = 1
        else:
            count += 1
        fim = ts

    sessoes.append({
        "inicio":      ini,
        "fim":         fim,
        "duracao_min": round((fim - ini).total_seconds() / 60, 1),
        "eventos":     count,
    })
    return sessoes


def status_usuario(ultima_atividade, gap_minutos: int = GAP_MINUTOS) -> str:
    """
    Retorna 'ativo' (< 5 min), 'ocioso' (5-30 min) ou 'encerrado' (> 30 min).
    Usa UTC para evitar problemas de timezone.
    """
    min_atras = minutos_atras(ultima_atividade)
    if min_atras <= 5:
        return "ativo"
    if min_atras <= gap_minutos:
        return "ocioso"
    return "encerrado"


def minutos_atras(ultima_atividade) -> float:
    """Quantos minutos se passaram desde a última atividade."""
    if ultima_atividade is None:
        return 9999.0
    ua = _naive_utc(ultima_atividade)
    return (datetime.utcnow() - ua).total_seconds() / 60
