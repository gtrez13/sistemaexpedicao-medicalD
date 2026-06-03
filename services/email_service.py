"""
services/email_service.py — Envio de email SMTP com fallback para console.

Variáveis de ambiente necessárias:
  SMTP_HOST  — ex: smtp.gmail.com
  SMTP_PORT  — ex: 587 (padrão)
  SMTP_USER  — seu-email@gmail.com
  SMTP_PASS  — senha de app (não a senha normal — ver myaccount.google.com → Senhas de app)

Se SMTP não estiver configurado, o link é impresso no console para testes locais.
"""
from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText


def enviar_email_reset(to_email: str, nome: str, reset_link: str) -> None:
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_host or not smtp_user or not smtp_pass:
        print(f"\n[email_service] ⚠️  SMTP não configurado — link de reset (use para testar):", flush=True)
        print(f"[email_service]    {reset_link}\n", flush=True)
        return

    nome_display = nome or "usuário"
    corpo = (
        f"Olá {nome_display},\n\n"
        f"Recebemos uma solicitação para redefinir sua senha no WMS LHVMED.\n"
        f"Clique no link abaixo (válido por 1 hora):\n\n"
        f"{reset_link}\n\n"
        f"Se você não solicitou, ignore este email com segurança.\n\n"
        f"— Equipe LHVMED"
    )

    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = "Recuperação de senha — WMS LHVMED"
    msg["From"]    = smtp_user
    msg["To"]      = to_email

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print(f"[email_service] ✅ Email de reset enviado para {to_email}", flush=True)
