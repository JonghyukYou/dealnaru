# 딜나루 1.1.3 배포 안내 — GitHub에서 시작하기

2026년 11월 19일 기준. 첨부된 1.0 DOCX 안내서를 현재 소스와 설정에 맞춰 갱신한 문서입니다. 처음 배포한다면 이 문서 하나를 순서대로 따라가세요. **실제 서버 생성·결제·도메인 변경은 아직 수행하지 않았습니다.**

이 문서는 Windows PC, 공개 GitHub 저장소, AWS Lightsail의 Ubuntu 24.04 LTS 서버, HostingKR의 `dealnaru.life`, Resend를 기준으로 합니다. 다른 도메인을 소유했다면 문서의 도메인 예시와 `SITE_DOMAIN`, 발신 주소를 실제 값으로 바꾸세요. 학교에서 공인 IP가 있는 서버를 제공한다면 그 서버를 사용할 수 있습니다.

| 구분 | 하는 일 |
| --- | --- |
| GitHub | 소스와 수정 이력을 보관. 사이트 하단 링크를 누르면 이 저장소가 열림 |
| Ubuntu 서버 | React 화면, FastAPI, PostgreSQL을 실제로 실행. 학교 PC가 꺼져 있어도 접속 가능 |
| 도메인·DNS | `dealnaru.life`가 서버의 고정 IP로 연결되도록 설정 |
| Resend | 회원 인증·목표가 알림 메일 발송. 문의를 받는 메일함은 따로 준비 |

GitHub에 파일을 올리는 단계만으로 사이트가 실행되지는 않습니다. 이 프로젝트에는 백엔드와 DB가 필요하므로 안내서의 서버 단계까지 진행합니다. 자동 배포는 설정되어 있지 않습니다.

## 1. GitHub에 처음 파일 올리기 — PC 브라우저

### 1-1. 올릴 파일 준비

1. 이번 `DealNaru-v1.1.3.zip`을 PC에 다운로드하고 **새 폴더에 압축을 해제**합니다.
2. 압축을 푼 `dealnaru` 폴더 안으로 들어갑니다. 여기서 `backend`, `frontend`, `deploy`, `docs`, `compose.yaml`, `README.md`가 보여야 합니다.
3. 실행하던 개발 폴더 대신 새로 압축을 푼 소스를 사용합니다. 실제 `.env`, DB, 백업, 개인 키, `venv`, `node_modules`는 올리지 않습니다. `.env.example`은 값이 비어 있는 설정 양식이므로 올립니다. 브라우저 업로드는 `.gitignore`를 자동 적용하지 않습니다.

### 1-2. 저장소 만들기

1. [GitHub](https://github.com/)에 가입하거나 로그인합니다.
2. 오른쪽 위 **+ → New repository**를 누릅니다. [새 저장소 화면](https://github.com/new)으로 바로 들어가도 됩니다.
3. 아래처럼 설정합니다.

| 입력 항목 | 설정 |
| --- | --- |
| Owner | 본인 계정 |
| Repository name | `dealnaru` 권장. 다른 이름도 가능 |
| Description | 선택. 예: `딜나루 캡스톤디자인 프로젝트` |
| Visibility | 방문자가 로그인 없이 소스를 보게 하려면 **Public** |
| Add README | **Off**. 소스에 README가 이미 있음 |
| Add .gitignore | **None**. 소스에 이미 있음 |
| License | 아직 선택하지 않았다면 None. 나중에 프로젝트 배포 범위에 맞게 정할 수 있음 |

4. **Create repository**를 누릅니다.

### 1-3. 파일 업로드

1. 빈 저장소 안내에 있는 **uploading an existing file**을 누릅니다. 파일이 있는 저장소라면 **Add file → Upload files**입니다.
2. 탐색기에서 `dealnaru` **안의 `backend` 폴더 하나**를 업로드 화면에 끌어 놓습니다. 업로드 대기 목록에 `backend/app/...`처럼 나오는지 봅니다.
3. 설명에 `Add backend` 등을 입력하고 **Commit changes**로 저장합니다. 브랜치 선택이 있으면 처음에는 `main`에 저장합니다.
4. 저장소의 **맨 위 Code 화면으로 돌아와** Add file → Upload files를 다시 누릅니다. `frontend`, `deploy`, `docs`도 각각 같은 방법으로 올립니다.
5. 마지막에 `dealnaru` 폴더 바로 안의 파일을 올립니다. `compose.yaml`, `README.md`, `.env.example`, `.gitignore`, `.dockerignore`가 포함되어야 합니다. 이름 앞에 점이 있는 파일도 빠뜨리지 않습니다.
6. 각 업로드마다 Commit changes를 누릅니다. GitHub 웹 업로드는 한 번에 최대 100개 파일이므로 전체 폴더를 한 번에 올리지 말고 위 순서로 나눕니다.

**ZIP 파일만 올리거나, 바깥쪽 `dealnaru` 폴더 전체를 한 겹 더 올리지 않습니다.** 저장소 첫 화면에서 다음 항목을 바로 볼 수 있으면 맞습니다.

| 저장소 최상위 항목 | 확인 |
| --- | --- |
| `backend/`, `frontend/`, `deploy/`, `docs/` | 서로 같은 단계에 있는 네 폴더 |
| `compose.yaml`, `README.md` | 폴더 안으로 더 들어가지 않아도 보임 |
| `.env.example`, `.gitignore`, `.dockerignore` | 실제 비밀값이 없는 양식·설정 파일 |

시크릿 창에서 저장소를 열어 파일 목록과 README가 보이는지 확인합니다. 주소창의 `https://github.com/본인계정/dealnaru` 형식 주소를 복사해 둡니다. 이 **저장소 웹 주소**가 사이트 하단의 GitHub 링크입니다. 아직 만든 적 없는 주소를 그대로 사용하지 마세요.

공식 안내: [저장소 만들기](https://docs.github.com/en/repositories/creating-and-managing-repositories/quickstart-for-repositories), [파일·폴더 업로드](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

## 2. 사이트 하단의 GitHub 링크

이번 소스에서는 하단의 “게임 가격 출처: IsThereAnyDeal 등” 문구를 제거했습니다. 출처 설명은 저장소의 README에 있고, 이용약관·개인정보 처리방침 옆에 **GitHub 저장소 ↗** 링크가 표시됩니다.

서버에서 만드는 루트 `.env`에 다음처럼 **본인의 실제 주소**를 입력하면 됩니다.

```dotenv
VITE_REPOSITORY_URL=https://github.com/본인계정/dealnaru
```

위의 `본인계정`은 반드시 실제 계정명으로 바꿉니다. 사이트 주소나 `.git`으로 끝나는 복제 주소 대신 브라우저에서 README가 보이는 주소를 사용합니다. 구체적인 서버 설정은 7단계에 있습니다. 주소를 설정하지 않은 로컬 개발 화면에는 링크가 표시되지 않습니다.

로컬에서 먼저 링크를 보고 싶다면 `frontend/.env.example`을 `frontend/.env.local`로 복사하고 그 안의 `VITE_REPOSITORY_URL`을 채운 후 `npm run dev`를 다시 시작합니다. 이 실제 환경 파일은 GitHub에 올리지 않습니다.

## 3. Ubuntu 서버 만들기 — Lightsail 브라우저

1. [Lightsail 콘솔](https://lightsail.aws.amazon.com/)에 로그인하고 **Create instance**를 누릅니다.
2. 서울 리전, **Linux/Unix → OS Only → Ubuntu 24.04 LTS**를 선택합니다.
3. IPv4를 포함한 **Dual-stack** 구성을 선택합니다. 이 서버에서 웹 빌드·API·DB를 함께 실행하므로 RAM 4 GB를 시작점으로 권장합니다. 실제 요금은 선택 화면에서 확인합니다.
4. 이름을 `dealnaru`로 정하고 생성합니다.
5. **Networking → Create static IP**에서 같은 리전의 고정 IPv4를 만들고 이 인스턴스에 연결합니다. 이 IP를 기록합니다.
6. 인스턴스의 Networking에서 IPv4 방화벽 **80/TCP, 443/TCP**를 허용합니다. SSH 22는 본인 IP로 제한하거나 콘솔의 Lightsail 브라우저 SSH 허용 옵션을 사용합니다. IPv6 규칙은 별도이며 5432·8000·5173은 공개하지 않습니다.
7. **Connect → Connect using SSH**로 검은 터미널을 엽니다. 이후 “서버 SSH”라고 적힌 명령은 이 창에 입력합니다. PC의 PowerShell과 구분하세요.

공식 안내: [인스턴스 생성·브라우저 SSH](https://docs.aws.amazon.com/lightsail/latest/userguide/getting-started-with-amazon-lightsail.html), [고정 IP](https://docs.aws.amazon.com/lightsail/latest/userguide/lightsail-create-static-ip.html), [방화벽](https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-firewall-and-port-mappings-in-amazon-lightsail.html).

## 4. 도메인을 서버에 연결 — HostingKR 브라우저

1. HostingKR → **나의서비스 → 도메인 관리 → dealnaru.life → 네임서버 / DNS**로 들어갑니다. 타사 네임서버를 사용한다면 그 DNS 서비스에서 설정합니다.
2. **DNS 레코드 관리 → 새 DNS 레코드 추가**에서 저장합니다.

| 유형 | 호스트 이름 | 값 |
| --- | --- | --- |
| A | 공란. 루트 도메인 | Lightsail 고정 IPv4 |
| CNAME | `www` | `dealnaru.life` |

A 값은 숫자 IP만 넣습니다. `https://`를 붙이지 않습니다. 동일 이름의 이전 서버 A/AAAA와 충돌하는 www 레코드는 확인해 정리하고, 기존 메일용 TXT·MX는 유지합니다.

3. PC PowerShell에서 확인합니다.

```powershell
Resolve-DnsName dealnaru.life -Type A
Resolve-DnsName www.dealnaru.life -Type CNAME
```

A가 고정 IP를 가리키고 www가 루트 도메인을 가리켜야 합니다. DNS가 아직 반영되지 않았으면 잠시 후 다시 확인합니다.

공식 안내: [HostingKR DNS 레코드 설정](https://help.hosting.kr/hc/ko/articles/5259561590809).

## 5. 인증 메일 발송 준비 — Resend 브라우저

현재 운영 구성은 회원가입 인증 메일을 실제로 발송하므로 이 단계도 필요합니다.

1. [Resend Domains](https://resend.com/domains) → **Add Domain**에서 `mail.dealnaru.life`를 추가합니다.
2. Resend가 생성한 DKIM·SPF 및 발송용 MX/TXT/CNAME을 해당 화면의 **유형·이름·값·우선순위 그대로** HostingKR DNS에 추가합니다. 레코드 종류와 값은 발급 화면을 기준으로 합니다.
3. HostingKR의 호스트 이름에는 도메인이 자동으로 붙는지 확인해 앞부분만 입력합니다. 루트의 기존 수신 MX를 바꾸지 않습니다.
4. Resend에서 도메인이 **Verified**가 되면 그 도메인에 제한한 발송용 API 키를 만듭니다.
5. 발신 주소는 `DealNaru <verify@mail.dealnaru.life>`처럼 인증한 하위 도메인과 일치시킵니다. 인증 링크용 메일은 클릭 추적을 끄고 점검합니다.
6. 실제 문의를 받을 기존 이메일도 별도로 준비합니다. 발신 주소를 적는 것만으로 수신 메일함이 만들어지는 것은 아닙니다.

공식 안내: [Resend 도메인 추가·인증](https://resend.com/docs/add-a-domain).

## 6. Docker 설치와 GitHub 소스 가져오기 — 서버 SSH

새 Ubuntu 서버를 기준으로 합니다. 이미 Docker 공식 패키지를 설치했다면 설치 부분을 반복하지 말고 버전을 확인합니다.

```bash
sudo apt update
sudo apt install -y ca-certificates curl git openssl python3
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

그다음 GitHub 소스를 가져옵니다. 입력 질문이 나오면 1단계에서 복사한 **실제 저장소 웹 주소**를 붙여 넣습니다.

```bash
cd "$HOME"
read -r -p "GitHub 저장소 HTTPS 주소: " repo_url
git clone "$repo_url" dealnaru
cd "$HOME/dealnaru"
ls -a
```

공개 저장소는 다운로드에 GitHub 비밀번호가 필요하지 않습니다. `backend`, `frontend`, `deploy`, `docs`, `compose.yaml`, `.env.example`이 보여야 합니다. `destination path ... already exists`라면 기존 폴더를 지우지 말고 먼저 어느 배포본인지 확인합니다.

이번 안내서는 서버의 `$HOME/dealnaru` 폴더를 사용합니다. 이전 DOCX의 ZIP 업로드 방식인 `$HOME/dealnaru-release/dealnaru`와 섞어서 진행하지 마세요. 이미 이전 방식으로 운영 중이라면 기존 폴더·DB를 유지하고 해당 폴더에 새 소스를 반영합니다.

공식 안내: [Docker Ubuntu 설치](https://docs.docker.com/engine/install/ubuntu/).

## 7. 운영 환경 파일 작성 — 서버 SSH

**처음 배포할 때만** 양식을 복사합니다. 기존 운영 `.env`를 덮어쓰지 않습니다.

```bash
cd "$HOME/dealnaru"
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
openssl rand -hex 32
nano .env
```

두 번 출력된 서로 다른 64자리 값을 아래 DB 비밀번호 두 칸에 각각 넣습니다. 나머지는 실제 값을 입력합니다.

| 키 | 입력할 내용 |
| --- | --- |
| `SITE_DOMAIN` | `dealnaru.life`. 실제 소유 도메인이 다르면 그 도메인 |
| `POSTGRES_PASSWORD` | 첫 번째 64자리 DB 관리자 비밀번호 |
| `DB_APP_PASSWORD` | 두 번째, 서로 다른 64자리 API용 DB 비밀번호 |
| `ITAD_API_KEY` | 사용할 ITAD 앱 키. 사용하지 않으면 공란이며 해당 API 기반 이력 제공 범위가 줄어듦 |
| `USED_MARKET_ENABLED` | 기본 `false`. 중고나라 수집을 사용하려면 기존 출처 사용 범위를 확인한 뒤 `true` |
| `RESEND_API_KEY` | Resend에서 발급한 실제 발송 키 |
| `MAIL_FROM` | `'DealNaru <verify@mail.dealnaru.life>'`처럼 인증한 도메인 발신 주소 |
| `ACME_EMAIL` | HTTPS 인증서 연락을 받을 실제 이메일 |
| `AUTO_REFRESH_ENABLED` | 처음에는 `false`. 데이터 확인 후 관리자에서 켬 |
| `VITE_REPOSITORY_URL` | 실제 `https://github.com/계정/저장소` 주소 |
| `VITE_OPERATOR_NAME` | 실제 운영자 또는 운영 기관명 |
| `VITE_SUPPORT_EMAIL` | 문의와 개인정보 요청을 받을 실제 이메일 |
| `VITE_HOSTING_NOTICE` | 실제 호스팅 업체·위탁 목적·서버 위치 |
| `VITE_MAIL_NOTICE` | 실제 메일 발송 업체·위탁 목적·처리 내용 |
| `VITE_RETENTION_NOTICE` | 실제 백업 보관 기간·삭제 방식·삭제 요청 반영 주기 |
| **`VITE_PRIVACY_OFFICER`** | 실제 개인정보 업무 담당자 또는 부서명. 구버전 DOCX에 빠진 필수 항목 |
| **`VITE_OVERSEAS_NOTICE`** | 실제 국외 이전 현황과 처리 근거·거부 방법. 구버전 DOCX에 빠진 필수 항목 |

공백이나 `#`가 들어가는 설명은 `VITE_OPERATOR_NAME='딜나루 운영팀'`처럼 작은따옴표로 감쌉니다. 따옴표 안에 다시 작은따옴표를 넣지 않습니다. `VITE_` 값은 웹 화면에 공개되므로 키·비밀번호를 넣지 않습니다.

개인정보 문구는 실제 계약과 운영 방식에 맞게 씁니다. `VITE_OVERSEAS_NOTICE`에는 해당하는 경우 이전받는 자·연락처, 국가, 항목, 목적, 시기·방법, 보유 기간, 근거, 거부 방법·영향을 적습니다. 실제 확인 없이 국가나 보관 기간을 임의로 채우지 않습니다. 상세 설정 설명은 [1.1 안내](UPGRADE_1_1_KO.md)를 참고하세요.

운영에서는 프로젝트 루트 `.env` 하나를 사용합니다. Windows 개발용 `backend/.env`나 `frontend/.env.local`을 서버로 복사하지 않습니다. API는 같은 도메인의 `/api`로 연결되도록 Docker 빌드가 설정되어 있습니다.

nano에서 **Ctrl+O → Enter → Ctrl+X**로 저장하고 나옵니다. 검사합니다.

```bash
set -o pipefail
sudo docker compose config --format json | python3 deploy/check_config.py
```

오류가 나면 표시된 항목을 고친 뒤 다시 검사합니다. 이 검사는 형식을 확인합니다. 실제 DNS·메일·수집 성공은 배포 후 점검합니다. `docker compose config` 전체 출력에는 비밀값이 포함되므로 공유하지 않습니다.

## 8. DB와 사이트 실행 — 서버 SSH

새 DB에서 시작하는 절차입니다. 기존 PC의 게임·가격 이력도 옮기려면 **API를 처음 시작하기 전에 아래 부록**을 확인합니다. 운영 DB를 이미 사용 중이면 초기화하지 않습니다.

```bash
cd "$HOME/dealnaru"
sudo docker compose up -d db
sudo docker compose ps db
sudo docker compose up -d --build api
sudo docker compose ps
sudo docker compose logs --tail=80 api
sudo docker compose up -d --build web
sudo docker compose ps
sudo docker compose logs --tail=80 web
```

DB와 API는 `healthy`, web은 실행 중이어야 합니다. 처음 빌드에는 시간이 걸릴 수 있습니다. DNS와 80·443 포트가 준비되면 Caddy가 HTTPS 인증서를 자동 발급합니다.

PC 브라우저에서 `https://dealnaru.life`를 엽니다. PC PowerShell에서도 확인합니다.

```powershell
curl.exe -fsS https://dealnaru.life/api/health
curl.exe -I http://dealnaru.life/
curl.exe -I https://www.dealnaru.life/
curl.exe -I https://dealnaru.life/terms
```

상태 응답은 `{"status":"ok","database":"connected"}`입니다. HTTP와 www는 루트 HTTPS로 이동해야 합니다. `/terms`는 200이며 `X-Robots-Tag: noindex, nofollow` 헤더가 있어야 합니다. 인증서 오류가 나면 DNS와 web 로그를 먼저 확인합니다.

## 9. 관리자와 실제 데이터 준비

서버 SSH에서 관리자 계정을 만듭니다. 비밀번호는 12~128자로 대화형 입력하며 명령줄 인수에 넣지 않습니다.

```bash
cd "$HOME/dealnaru"
read -r -p "관리자 이메일: " admin_email
sudo docker compose exec api python -m app.admin_cli create --email "$admin_email" --nickname "운영 관리자"
sudo docker compose exec api python -m app.admin_cli list
```

이미 있는 관리자라면 `create` 대신 `reset-password --email "$admin_email"`을 사용합니다.

1. 브라우저에서 관리자 로그인 후 `/admin`으로 들어갑니다.
2. **원하는 게임 추가**에서 게임을 검색하고 **가격·이력 수집**을 실행합니다. 새 서버 DB에는 로컬 PC의 게임 이력이 자동으로 들어오지 않습니다.
3. **인기 게임·가격 이력 보강**, **공식 혜택 갱신**을 실행합니다. 이력 작업은 별도 대기열의 완료 로그도 확인합니다.
4. 중고나라를 사용할 때는 서버 `.env`의 `USED_MARKET_ENABLED=true`를 확인하고 `sudo docker compose up -d api`로 반영한 뒤 관리자에서 **중고마켓 수집**을 따로 실행합니다. 당근은 포함되지 않습니다.
5. 가격·원문·확인일을 확인하고 관리자에서 **자동 갱신**을 켭니다. 기본은 360분, 6시간입니다. 이후 관리 화면의 DB 저장 설정이 초기 환경 변수보다 우선합니다. 새 중고 매물 수집은 자동 가격 갱신과 별개입니다.
6. 휴대폰 데이터망에서 접속·검색·새로고침, 별도 일반 계정 가입·실제 인증메일·로그인·찜·알림, 하단 GitHub 링크를 확인합니다. 학교 PC를 꺼도 접속되어야 합니다.

## 10. Google 검색 등록은 진행하지 않음

Google Search Console 가입, 소유권 TXT 등록, 사이트맵 제출은 생략합니다. 이번 소스는 HTML의 `noindex, nofollow`와 Caddy의 `X-Robots-Tag`를 설정하고 사이트맵을 제거했습니다. Google 등 이 규칙을 따르는 검색엔진이 페이지를 색인하지 않도록 요청하는 설정입니다.

직접 URL 접속, 링크 공유, 로그인·관리자 기능은 그대로 사용할 수 있습니다. **접속을 막거나 사이트를 비공개로 만드는 기능은 아닙니다.** 공개 GitHub 저장소는 별도로 검색에 나타날 수 있습니다.

페이지에서 noindex를 읽을 수 있어야 하므로 `robots.txt`에 `Disallow: /`를 추가하지 않습니다. API 경로만 기존처럼 크롤링에서 제외합니다. 향후 검색 노출을 원하면 HTML 메타 태그와 Caddy 헤더를 함께 수정하고 웹을 다시 빌드합니다.

공식 설명: [Google noindex](https://developers.google.com/search/docs/crawling-indexing/block-indexing).

## 11. 배포 후에도 수정할 수 있음

화면·가격 수집 코드·약관·설명·기능 모두 계속 수정할 수 있습니다. 바꾸는 대상에 따라 다음과 같이 반영합니다.

| 수정 내용 | 반영 방법 |
| --- | --- |
| 화면의 짧은 문구·README | GitHub 파일에서 연필 아이콘 → 편집 → Commit changes. 웹 코드라면 서버에서 소스 반영·재빌드 |
| 기능·여러 파일 | PC에서 개발·확인한 뒤 GitHub에 변경 파일 반영. 삭제한 파일은 저장소에서도 삭제 |
| GitHub 주소·운영자·처리방침 공개 설정 | 서버 루트 `.env` 수정 → web 재빌드 |
| DB에 저장된 실제 가격·게임·수집 설정 | 관리자 기능으로 갱신. 소스 재배포가 필요 없는 데이터 변경 |

GitHub의 Commit changes는 저장소에 저장하는 동작입니다. **현재 구성에서는 실제 사이트가 자동 갱신되지 않습니다.** 서버에서 다음 절차로 가져와 재빌드합니다.

새 버전을 반영할 때마다 먼저 DB를 백업합니다. 아래 블록은 실패하면 다음 명령을 멈추도록 되어 있습니다. 같은 서버·같은 프로젝트 폴더·같은 `.env`와 DB 볼륨을 유지합니다.

```bash
(
  set -euo pipefail
  cd "$HOME/dealnaru"
  install -d -m 700 "$HOME/dealnaru-backups"
  umask 077
  backup_file="$HOME/dealnaru-backups/dealnaru-$(date +%Y%m%d-%H%M%S).dump"
  sudo docker compose exec -T db pg_dump -U postgres -Fc dealnaru > "$backup_file.tmp"
  sudo docker compose exec -T db pg_restore --list < "$backup_file.tmp" >/dev/null
  mv "$backup_file.tmp" "$backup_file"
  git pull --ff-only
  sudo docker compose config --format json | python3 deploy/check_config.py
  sudo docker compose up -d --build api web
  sudo docker compose ps
)
```

`git pull`에서 충돌이 나면 중단하고 파일 상태를 확인합니다. 실제 운영 값을 소스에 적지 않고 `.env`에만 저장하면 충돌 가능성을 줄일 수 있습니다. 이 방법은 6단계에서 Git clone으로 설치한 서버용이며, ZIP으로 설치한 기존 폴더에서는 Git pull 대신 새 소스를 반영합니다.

GitHub 링크나 운영자 공개 문구만 서버 `.env`에서 바꿨다면 파일을 저장한 뒤 아래만 실행합니다.

```bash
cd "$HOME/dealnaru"
sudo docker compose up -d --build web
```

`VITE_` 값은 빌드할 때 화면에 포함되므로 단순 재시작만으로 바뀌지 않습니다. 수정·재빌드는 짧은 접속 중단이 생길 수 있습니다. 회원·찜·가격 기록은 DB 볼륨에 남습니다. `docker compose down -v`나 `app.seed --reset`은 업데이트 명령이 아니므로 실행하지 않습니다.

백업에는 개인정보가 포함될 수 있으므로 저장소 밖에 접근을 제한해 보관합니다. 파일 목록 확인 외에 분리된 PostgreSQL에서 복원 시험도 하고, 실제 운영 보관 정책에 따라 별도 사본을 관리합니다. PostgreSQL 메이저 버전 변경은 일반 코드 업데이트와 분리합니다.

## 12. 막혔을 때 확인할 곳

| 증상 | 확인 |
| --- | --- |
| `compose.yaml`을 찾지 못함 | `cd "$HOME/dealnaru"` 후 `ls -a`. 저장소에 바깥 폴더 한 겹을 더 올렸는지 확인 |
| 환경 변수 필수 오류 | 루트 `.env`, 특히 `VITE_PRIVACY_OFFICER`·`VITE_OVERSEAS_NOTICE` 포함 여부 |
| 인증서 오류·다른 사이트가 열림 | DNS A/AAAA, 고정 IP, 80/443, `sudo docker compose logs --tail=100 web` |
| 502·API가 실행되지 않음 | `sudo docker compose ps`, `sudo docker compose logs --tail=100 api db` |
| 메일이 안 옴 | Resend의 Verified 도메인·발신 주소·발송 기록, 스팸함 |
| 게임·차트가 비어 있음 | 새 DB인지 확인. 관리자 수집, ITAD 키, 가격 이력 대기열 상태 |
| 중고 매물이 없음 | `USED_MARKET_ENABLED`, 별도 수집 로그, 최근 14일과 출처 응답 상태 |
| 하단 GitHub 링크가 없음·예전 주소임 | `VITE_REPOSITORY_URL` 실제 웹 주소, Public 공개 범위, web 재빌드 |
| GitHub를 수정해도 화면은 그대로 | 서버에서 Git pull·재빌드까지 했는지 확인 |
| Google 검색에서 안 보임 | 이번 구성의 의도된 동작. 직접 주소로 접속 |

## 부록. PC의 기존 게임·가격 이력 이전

처음부터 관리자 수집으로 채울 경우 생략합니다. 기존 기록을 가져와야 할 때만 **8단계의 API 첫 실행 전에** 진행합니다. 이미 운영 중인 DB에 복원을 덮어쓰지 않습니다.


### A. 새 DB로 시작

자료 이전 없이 본문의 8단계를 진행합니다. API가 처음 실행되면 필요한 테이블과 프로그램 카탈로그를 만듭니다. 이후 관리자 화면에서 최신 게임과 이력을 수집합니다. 학교 PC의 DB는 별도로 남아 있어 개발이나 백업에 사용할 수 있습니다.

### B. 기존 게임 목록·가격 이력을 이전하는 경우

1. 로컬 pgAdmin 4의 Query Tool에서 `SELECT current_database(), version();`을 실행해 DB 이름과 PostgreSQL 버전을 기록합니다. DB 우클릭 → **Backup → Format: Custom**으로 `local-full.backup`을 만들어 본인 PC에만 보관합니다. pgAdmin이 실행 파일을 못 찾으면 Preferences의 **Paths → Binary paths**에서 설치된 PostgreSQL `bin` 경로를 설정합니다.
2. 운영에 이전할 데이터는 별도 덤프로 준비합니다. 아래 PostgreSQL 경로와 포트는 본인 환경에 맞게 바꿉니다. 백업을 만든 클라이언트가 서버보다 너무 새 버전인 경우, 운영 PostgreSQL 18에서 읽을 수 있는지 먼저 확인합니다. SCP 업로드에는 해당 Lightsail 인스턴스에 사용하는 개인 키가 필요합니다. 콘솔의 SSH 키 관리 화면에서 준비한 파일의 실제 경로를 입력합니다. 아래 명령은 PC의 PowerShell에서 실행합니다.

```powershell
$PgBin = 'C:\Program Files\PostgreSQL\18\bin'
$DbName = Read-Host "로컬 개발 DB 이름"
$DbUser = Read-Host "로컬 PostgreSQL 사용자"
$ServerIp = Read-Host "Lightsail 고정 IPv4"
$SshKey = Read-Host "해당 서버 개인 키 파일의 전체 경로"
$Exclude = @('--exclude-table-data=public.users', '--exclude-table-data=public.user_sessions', '--exclude-table-data=public.wishlist_items', '--exclude-table-data=public.notifications', '--exclude-table-data=public.crawling_logs', '--exclude-table-data=public.auto_refresh_state', '--exclude-table-data=public.used_market_items', '--exclude-table-data=public.used_market_summaries')
& "$PgBin\pg_dump.exe" -h localhost -p 5432 -U $DbUser -d $DbName -Fc -f ".\dealnaru-catalog.backup" @Exclude
if ($LASTEXITCODE -ne 0) { throw "pg_dump 실패" }
& "$PgBin\pg_restore.exe" --list ".\dealnaru-catalog.backup"
scp -i "$SshKey" ".\dealnaru-catalog.backup" "ubuntu@${ServerIp}:/home/ubuntu/"
```

이 파일에 포함될 게임·프로그램 데이터의 공개 출처와 시연용 가격 여부를 검토합니다. 검토하지 않은 데이터가 섞였거나 다른 테이블을 추가해 사용했다면 검증 DB를 먼저 거치거나 A를 선택합니다. 개인 계정·찜·알림·중고 매물은 위 명령에서 데이터만 제외돼 이전되지 않습니다.

3. **웹이나 API를 먼저 띄우지 않은 새 DB**에 복원합니다. Ubuntu의 프로젝트 폴더에서 아래와 같이 실행합니다.

```bash
cd "$HOME/dealnaru"
sudo docker compose up -d db
sudo docker compose ps db
chmod 600 "$HOME/dealnaru-catalog.backup"
sudo docker compose exec -T db psql -U postgres -d dealnaru -c '\dt'
sudo docker compose exec -T db pg_restore -U postgres --role=dealnaru_app \
  -d dealnaru --no-owner --no-privileges --exit-on-error --single-transaction \
  < "$HOME/dealnaru-catalog.backup"
sudo docker compose exec -T db psql -U postgres -d dealnaru \
  -c "select count(*) as games from games;" \
  -c "select count(*) as prices from price_snapshots;" \
  -c "select count(*) as imported_users from users;"
```

DB가 healthy인지 확인한 뒤 복원합니다. 첫 번째 `\dt` 결과에 앱 테이블이 없고, 이전 후 `imported_users`는 0이어야 합니다. `relation already exists`라면 이미 앱이 테이블을 만든 DB일 수 있습니다. 운영 DB를 `--clean`으로 덮어쓰지 마세요. 복원이 끝나면 본문의 8단계 API 빌드·실행부터 이어갑니다. [pgAdmin 백업](https://www.pgadmin.org/docs/pgadmin4/latest/backup_dialog.html), [PostgreSQL 복원](https://www.postgresql.org/docs/current/app-pgrestore.html).

