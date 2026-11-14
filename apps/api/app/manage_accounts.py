"""Operator-only pilot account provisioning. Never put passwords in command arguments."""

import argparse
from getpass import getpass

from sqlalchemy import delete

from app.accounts import account_by_email, hash_password
from app.db import init_db, new_id, session_scope
from app.experience_models import AccessAudit, Account, LoginSession
from app.models import Person
from app.schemas.experience import RegisterIn


def create_owner(email: str, display_name: str, password: str) -> str:
    body = RegisterIn(email=email, display_name=display_name, password=password)
    with session_scope() as session:
        if account_by_email(session, body.email):
            raise ValueError(
                "An account already exists. Use reset-password only after verifying its owner."
            )
        person = Person(id=new_id("person"), role="child", display_name=body.display_name.strip())
        if not person.display_name:
            raise ValueError("A display name is required")
        session.add(person)
        session.flush()
        session.add(
            Account(person_id=person.id, email=body.email, password_hash=hash_password(password))
        )
        session.add(
            AccessAudit(id=new_id("audit"), actor_id=person.id, action="operator.account_created")
        )
        return person.id


def reset_password(email: str, password: str) -> None:
    body = RegisterIn(email=email, display_name="Account recovery", password=password)
    with session_scope() as session:
        account = account_by_email(session, body.email)
        if not account:
            raise ValueError("Account not found")
        account.password_hash = hash_password(password)
        session.add(account)
        session.exec(delete(LoginSession).where(LoginSession.person_id == account.person_id))
        session.add(
            AccessAudit(
                id=new_id("audit"),
                actor_id=account.person_id,
                action="operator.password_reset_sessions_revoked",
            )
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["create-owner", "reset-password"])
    parser.add_argument(
        "--email", required=True, help="Account identifier; independently verify the person"
    )
    parser.add_argument("--name", help="Required for create-owner")
    args = parser.parse_args(argv)
    if args.operation == "create-owner" and not args.name:
        parser.error("create-owner requires --name")
    password = getpass("New password (at least 12 characters): ")
    if getpass("Repeat password: ") != password:
        parser.error("Passwords did not match")
    init_db()
    try:
        if args.operation == "create-owner":
            create_owner(args.email, args.name, password)
        else:
            reset_password(args.email, password)
    except ValueError:
        # Pydantic validation errors may include input values. Never echo them.
        parser.error(
            "Account operation failed. Check email, name, password length, and whether the account exists."
        )
    print("Account operation completed. No credentials were written to the console.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
