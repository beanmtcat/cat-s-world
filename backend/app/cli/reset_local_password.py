"""Interactively reset a local development user's password."""

from __future__ import annotations

import argparse
import asyncio
from getpass import getpass

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.session import engine


async def reset(email: str, password: str) -> None:
    password_hash = hash_password(password)
    if not verify_password(password, password_hash):
        raise RuntimeError("生成的密码哈希验证失败，未修改数据库")

    async with engine.begin() as connection:
        user_id = (
            await connection.execute(
                text("""
                    UPDATE mmcat_users
                    SET password_hash = :password_hash,
                        password_changed_at = now(),
                        failed_login_count = 0,
                        locked_until = NULL,
                        mtime = now()
                    WHERE email = :email AND isvalid = true
                    RETURNING id
                """),
                {"password_hash": password_hash, "email": email},
            )
        ).scalar_one_or_none()
        if user_id is None:
            raise RuntimeError("账号不存在，未修改数据库")
        await connection.execute(
            text("""
                UPDATE mmcat_user_sessions
                SET revoked_at = now(), isvalid = false, mtime = now()
                WHERE user_id = :user_id AND revoked_at IS NULL
            """),
            {"user_id": user_id},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="仅重置本地开发环境的用户密码")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env != "development":
        raise SystemExit("只允许在 APP_ENV=development 时运行")
    database = make_url(settings.database_url)
    if database.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("数据库不是本机地址，拒绝重置密码")

    email = args.email.strip().lower()
    print(f"目标：{database.host}:{database.port or 5432}/{database.database}，账号：{email}")
    if input("确认重置本地密码？输入 YES：") != "YES":
        raise SystemExit("已取消")
    password = getpass("新密码（至少 15 位）：")
    confirmation = getpass("再输入一次：")
    if len(password) < 15 or password != confirmation:
        raise SystemExit("密码过短或两次输入不一致，未修改数据库")

    try:
        asyncio.run(reset(email, password))
    finally:
        password = ""
        confirmation = ""
    print("本地密码已更新，旧会话已失效")


if __name__ == "__main__":
    main()
