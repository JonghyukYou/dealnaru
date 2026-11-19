"""Check a rendered Compose JSON config without printing secret values."""
import json
import re
import sys
from urllib.parse import urlsplit

config = json.load(sys.stdin)
db = config["services"]["db"]["environment"]
api = config["services"]["api"]["environment"]
web = config["services"]["web"]
public = web["build"]["args"]
domain = web["environment"]["SITE_DOMAIN"]

errors = []
hex_password = re.compile(r"[0-9a-f]{64}")
email = re.compile(r"[^\s@<>?:]+@[^\s@<>?:]+\.[^\s@<>?:]+")
if not all(hex_password.fullmatch(db.get(key, "")) for key in ("POSTGRES_PASSWORD", "DB_APP_PASSWORD")):
    errors.append("DB 비밀번호 2개는 각각 openssl rand -hex 32로 생성하세요.")
if db["POSTGRES_PASSWORD"] == db["DB_APP_PASSWORD"]:
    errors.append("DB 서비스 비밀번호와 API 역할 비밀번호가 같으면 안 됩니다.")

domain_label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
if not re.fullmatch(rf"{domain_label}(?:\.{domain_label})+", domain, re.IGNORECASE) or len(domain) > 253:
    errors.append("SITE_DOMAIN에 https://나 경로 없이 실제 도메인만 입력하세요.")
for value, field in [(public["VITE_SUPPORT_EMAIL"], "VITE_SUPPORT_EMAIL"),
                     (web["environment"]["ACME_EMAIL"], "ACME_EMAIL")]:
    if not email.fullmatch(value) or value.endswith(("@example.com", "@example.org")):
        errors.append(f"{field}에 실제 수신 가능한 이메일을 입력하세요.")
if not (api["RESEND_API_KEY"].startswith("re_") and len(api["RESEND_API_KEY"]) > 5):
    errors.append("Resend 발송 키를 확인하세요.")
if not re.search(r"@([a-z0-9-]+\.)*" + re.escape(domain) + r">?$", api["MAIL_FROM"], re.IGNORECASE):
    errors.append("MAIL_FROM은 Resend에서 인증한 SITE_DOMAIN 또는 그 하위 도메인 주소로 설정하세요.")
try:
    repo = urlsplit(public["VITE_REPOSITORY_URL"])
    if (repo.scheme != "https" or not repo.hostname or repo.username or repo.query or repo.fragment
            or len(repo.path.strip("/").split("/")) < 2 or "example." in repo.hostname):
        raise ValueError
except ValueError:
    errors.append("VITE_REPOSITORY_URL에 https://호스트/계정/저장소 형식의 실제 주소를 입력하세요.")
for key in ("VITE_OPERATOR_NAME", "VITE_HOSTING_NOTICE", "VITE_MAIL_NOTICE", "VITE_RETENTION_NOTICE", "VITE_PRIVACY_OFFICER", "VITE_OVERSEAS_NOTICE"):
    if not public.get(key, "").strip() or any(x in public[key] for x in ("기입하세요", "여기에 입력", "REPLACE_ME")):
        errors.append(f"{key}에 실제 공개할 설명을 입력하세요.")
if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
print("운영 설정 형식 확인 완료. DNS, 메일 발송, 외부 데이터 수집은 배포 후 확인하세요.")
