"""Create the first local administrator without raw SQL."""

from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.core import User


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创建 mmcat 首个管理员")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default="管理员")
    parser.add_argument("--password", help="省略时将安全地交互输入")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    password = args.password or getpass.getpass("管理员密码（至少 15 位）：")
    if len(password) < 15:
        raise SystemExit("密码至少需要 15 位。")
    email = args.email.strip().lower()
    async with SessionLocal() as session:
        existing = await session.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise SystemExit("该邮箱已存在；为避免误操作，本命令不会覆盖现有用户。")
        session.add(
            User(
                email=email,
                display_name=args.display_name.strip(),
                password_hash=hash_password(password),
                role="admin",
                status="active",
            )
        )
        await session.commit()
    print(f"管理员已创建：{email}")


if __name__ == "__main__":
    asyncio.run(run(_arguments()))
