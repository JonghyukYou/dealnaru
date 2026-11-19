import os
from html import escape
from urllib.parse import quote

from app.services.public_security import https_url
from app.settings import EMAIL_DEV_MODE, FRONTEND_BASE_URL

try:
    import resend
except ImportError:
    resend = None


RESEND_API_KEY = os.getenv("RESEND_API_KEY")
MAIL_FROM = os.getenv("MAIL_FROM", "DealNaru <onboarding@resend.dev>")

if resend is not None and RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


def build_verify_url(token: str) -> str:
    return f"{FRONTEND_BASE_URL}/verify-email#token={quote(token, safe='')}"


def send_email(to: str, subject: str, html: str):
    if EMAIL_DEV_MODE:
        print("\n[EMAIL_DEV_MODE] 메일 발송 대신 콘솔에 출력합니다.")
        print(f"TO: {to}")
        print(f"SUBJECT: {subject}")
        print(html)
        print()
        return {"dev_mode": True}

    if resend is None:
        raise RuntimeError(
            "resend 패키지가 설치되지 않았습니다. pip install resend 를 실행해주세요."
        )

    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY 환경변수가 설정되지 않았습니다.")

    params = {
        "from": MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }

    return resend.Emails.send(params)


def send_verification_email(to: str, nickname: str, token: str):
    verify_url = build_verify_url(token)
    safe_verify_url = escape(verify_url, quote=True)
    safe_nickname = escape(nickname)

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6;">
      <h2>이메일 인증을 완료해주세요</h2>
      <p>{safe_nickname}님, 딜나루 가입을 환영합니다.</p>
      <p>아래 버튼을 눌러 이메일 인증을 완료해주세요.</p>
      <p>
        <a href="{safe_verify_url}"
           style="display:inline-block;padding:12px 18px;background:#0F766E;color:white;text-decoration:none;border-radius:8px;">
          이메일 인증하기
        </a>
      </p>
      <p>버튼이 열리지 않으면 아래 주소를 브라우저에 복사해주세요.</p>
      <p>{safe_verify_url}</p>
      <p>이 링크는 24시간 동안 유효합니다.</p>
    </div>
    """

    result = send_email(
        to=to,
        subject="[딜나루] 이메일 인증을 완료해주세요",
        html=html,
    )

    if EMAIL_DEV_MODE:
        result["dev_verify_url"] = verify_url

    return result


def send_target_price_email(
    to: str,
    nickname: str,
    game_title: str,
    platform_name: str,
    discounted_price: int,
    target_price: int,
    game_url: str | None = None,
):
    safe_nickname = escape(nickname)
    safe_game_title = escape(game_title)
    safe_platform_name = escape(platform_name)

    link_html = ""

    if safe_url := https_url(game_url):
        link_html = f"""
        <p>
          <a href="{escape(safe_url, quote=True)}"
             style="display:inline-block;padding:12px 18px;background:#16a34a;color:white;text-decoration:none;border-radius:8px;">
            할인 페이지 확인하기
          </a>
        </p>
        """

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6;">
      <h2>위시리스트 목표가 도달!</h2>
      <p>{safe_nickname}님, 기다리던 게임이 목표가 이하로 내려갔습니다.</p>
      <ul>
        <li><strong>게임:</strong> {safe_game_title}</li>
        <li><strong>플랫폼:</strong> {safe_platform_name}</li>
        <li><strong>현재 가격:</strong> {discounted_price:,}원</li>
        <li><strong>목표 가격:</strong> {target_price:,}원</li>
      </ul>
      {link_html}
      <p>사이트 알림함에서도 같은 내용을 확인할 수 있습니다.</p>
    </div>
    """

    return send_email(
        to=to,
        subject=f"[딜나루] {game_title} 목표가 도달!",
        html=html,
    )
