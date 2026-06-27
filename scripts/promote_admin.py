from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal, init_db
from app.models import User


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote an existing user account to admin.")
    parser.add_argument("email", help="Registered account email to promote.")
    args = parser.parse_args()

    init_db()
    email = args.email.lower()
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit(f"No registered user found for {email}. Register it in the web app first.")
        user.role = "admin"
        user.status = "active"
        db.commit()
        print(f"Promoted {user.email} to admin.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
