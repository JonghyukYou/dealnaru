"""관리자 계정을 안전하게 조회하고 복구하는 명령줄 도구."""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models import User, UserSession
from app.services.auth_service import hash_password
from app.utils.time import utc_now


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not email or "@" not in email:
        raise ValueError("올바른 이메일을 입력해주세요.")
    return email


def prompt_password() -> str:
    password = getpass.getpass("새 비밀번호(12~128자): ")
    confirmation = getpass.getpass("새 비밀번호 확인: ")

    if not 12 <= len(password) <= 128:
        raise ValueError("비밀번호는 12~128자여야 합니다.")
    if password != confirmation:
        raise ValueError("입력한 비밀번호가 서로 다릅니다.")

    return password


def list_admins(db) -> int:
    admins = db.query(User).filter(User.is_admin.is_(True)).order_by(User.id).all()

    if not admins:
        print("등록된 관리자 계정이 없습니다.")
        return 1

    print("ID\t이메일\t닉네임\t이메일 인증")
    for user in admins:
        verified = "완료" if user.email_verified else "미완료"
        print(f"{user.id}\t{user.email}\t{user.nickname}\t{verified}")

    return 0


def reset_password(db, email_value: str) -> int:
    email = normalize_email(email_value)
    user = db.query(User).filter(User.email == email).first()

    if user is None:
        raise ValueError(f"{email} 계정을 찾을 수 없습니다.")
    if not user.is_admin:
        raise ValueError(
            "해당 계정은 관리자가 아닙니다. 먼저 promote 명령을 사용하세요."
        )

    user.password_hash = hash_password(prompt_password())
    user.email_verified = True
    user.email_verified_at = user.email_verified_at or utc_now()
    user.email_verify_token_hash = None
    user.email_verify_token_expires_at = None
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.commit()

    print(f"{email} 관리자 계정의 비밀번호를 재설정했습니다.")
    return 0


def promote_user(db, email_value: str) -> int:
    email = normalize_email(email_value)
    user = db.query(User).filter(User.email == email).first()

    if user is None:
        raise ValueError(f"{email} 계정을 찾을 수 없습니다.")

    user.is_admin = True
    user.email_verified = True
    user.email_verified_at = user.email_verified_at or utc_now()
    user.email_verify_token_hash = None
    user.email_verify_token_expires_at = None
    db.commit()

    print(f"{email} 계정에 관리자 권한을 부여했습니다.")
    return 0


def create_admin(db, email_value: str, nickname_value: str) -> int:
    email = normalize_email(email_value)
    nickname = nickname_value.strip()

    if not nickname or len(nickname) > 50:
        raise ValueError("닉네임을 1~50자로 입력해 주세요.")
    if db.query(User).filter(User.email == email).first():
        raise ValueError(
            "이미 가입된 이메일입니다. reset-password 또는 promote를 사용하세요."
        )
    if db.query(User).filter(User.nickname == nickname).first():
        raise ValueError("이미 사용 중인 닉네임입니다.")

    db.add(
        User(
            email=email,
            nickname=nickname,
            password_hash=hash_password(prompt_password()),
            is_admin=True,
            email_verified=True,
            email_verified_at=utc_now(),
        )
    )
    db.commit()

    print(f"{email} 관리자 계정을 생성했습니다.")
    return 0


def delete_user(db, email_value: str) -> int:
    email = normalize_email(email_value)
    user = db.query(User).filter(User.email == email).first()
    if user is None or user.is_admin:
        raise ValueError("일반 회원 계정만 삭제할 수 있습니다.")
    if input("본인 확인과 삭제 요청을 확인했습니다. 삭제할 이메일을 다시 입력하세요: ").strip().lower() != email:
        raise ValueError("확인값이 달라 변경하지 않았습니다.")
    db.delete(user)
    db.commit()
    print("계정과 관련 찜, 알림, 세션을 삭제했습니다.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DealNaru 관리자 계정 관리")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="관리자 이메일과 인증 상태를 조회합니다.")

    reset_parser = subparsers.add_parser(
        "reset-password",
        help="기존 관리자의 비밀번호를 대화형 입력으로 재설정합니다.",
    )
    reset_parser.add_argument("--email", required=True)

    promote_parser = subparsers.add_parser(
        "promote",
        help="기존 회원에게 관리자 권한을 부여합니다.",
    )
    promote_parser.add_argument("--email", required=True)

    create_parser = subparsers.add_parser(
        "create",
        help="새 관리자 계정을 생성합니다.",
    )
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--nickname", default="관리자")

    delete_parser = subparsers.add_parser("delete-user", help="본인 확인 후 일반 회원의 삭제 요청을 처리합니다.")
    delete_parser.add_argument("--email", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db = SessionLocal()

    try:
        if args.command == "list":
            return list_admins(db)
        if args.command == "reset-password":
            return reset_password(db, args.email)
        if args.command == "promote":
            return promote_user(db, args.email)
        if args.command == "create":
            return create_admin(db, args.email, args.nickname)
        if args.command == "delete-user":
            return delete_user(db, args.email)

        raise ValueError("지원하지 않는 명령입니다.")
    except (ValueError, SQLAlchemyError) as error:
        db.rollback()
        print(f"오류: {error}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
