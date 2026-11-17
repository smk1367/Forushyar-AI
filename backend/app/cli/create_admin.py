import asyncio
import os

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.employee import Employee
from app.security import hash_password


async def main() -> None:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    full_name = os.environ.get("ADMIN_FULL_NAME", "").strip()
    if not email or not password or not full_name:
        raise SystemExit("Set ADMIN_EMAIL, ADMIN_PASSWORD and ADMIN_FULL_NAME; do not pass secrets as command-line arguments.")
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(Employee))
        if count:
            raise SystemExit("Bootstrap refused: employee accounts already exist.")
        employee = Employee(full_name=full_name, email=email, password_hash=hash_password(password), role="ADMIN", is_active=True)
        session.add(employee)
        await session.commit()
        print(f"Initial administrator created: {email}")


if __name__ == "__main__":
    asyncio.run(main())
