"""Transactional-email delivery; development logs links instead of sending them."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from app.core.config import get_settings

logger = logging.getLogger("mmcat.mail")


def _send_verification(address: str, token: str) -> None:
    settings = get_settings()
    link = f"{str(settings.app_base_url).rstrip('/')}/verify-email?token={quote(token)}"
    if settings.app_env != "production":
        logger.info("Email verification link for %s: %s", address, link)
        return
    message = EmailMessage()
    message["Subject"] = "验证你的猫子的世界账号"
    message["From"] = settings.mail_from
    message["To"] = address
    message.set_content(f"请在 24 小时内打开以下链接验证邮箱：\n{link}")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_username.get_secret_value(), settings.smtp_password.get_secret_value())
        smtp.send_message(message)


async def send_verification_email(address: str, token: str) -> None:
    await asyncio.to_thread(_send_verification, address, token)
