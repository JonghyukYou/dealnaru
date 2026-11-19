# 정식 요금제 확인 기록 — 1.1.2

기록 기준: 2026년 11월 19일 한국 시간. 제품 20개, 활성 요금제 70개입니다.

- 최근 공식 확인 기록: 61개.
- 과거 확인 금액을 날짜와 함께 제공: 9개.
- 저장된 금액 미확인: 0개.

JetBrains 3개는 이번 수정에서 공식 페이지를 다시 요청해 파싱했습니다. Google AI 4개와 나머지 항목은 앞선 수정의 확인 기록을 이어받았으며 모든 출처를 이번에 다시 요청한 것은 아닙니다. 과거 값을 최신 현재가로 일괄 승격하지 않습니다.

| 제품 | 요금제 | 최근 확인 | 과거 확인값 | 금액 미확인 |
| --- | ---: | ---: | ---: | ---: |
| Adobe Creative Cloud | 1 | 0 | 1 | 0 |
| ChatGPT | 4 | 4 | 0 | 0 |
| Gemini · Google AI | 4 | 4 | 0 | 0 |
| Claude | 4 | 4 | 0 | 0 |
| Figma Professional | 2 | 2 | 0 | 0 |
| JetBrains Toolbox | 3 | 3 | 0 | 0 |
| Notion Plus | 8 | 4 | 4 | 0 |
| Perplexity | 4 | 4 | 0 | 0 |
| Cursor | 4 | 4 | 0 | 0 |
| GitHub Copilot | 4 | 4 | 0 | 0 |
| Microsoft 365 | 6 | 6 | 0 | 0 |
| Canva | 2 | 0 | 2 | 0 |
| Adobe Photoshop | 1 | 0 | 1 | 0 |
| Adobe Premiere | 1 | 0 | 1 | 0 |
| Midjourney | 8 | 8 | 0 | 0 |
| ElevenLabs | 4 | 4 | 0 | 0 |
| Descript | 4 | 4 | 0 | 0 |
| Obsidian | 3 | 3 | 0 | 0 |
| DaVinci Resolve | 2 | 2 | 0 | 0 |
| LibreOffice | 1 | 1 | 0 | 0 |

## 이번에 보강한 JetBrains

공식 구매 페이지의 `productsData`에서 All Products Pack 개인용 월간·연간 신규 구독 가격을 읽습니다. Toolbox App은 공식 FAQ에서 무료 관리 앱임을 확인해 별도 표시합니다. [이번 수정·적용 안내](UPDATE_1_1_2_KO.md)에 구분과 출처를 정리했습니다.

## 앞선 수정에서 확인한 가격 구조

- Google AI: 공식 페이지에 연결된 공개 한국 가격 JSON에서 요금제별 키·국가·통화를 함께 확인합니다.
- Notion: 이스케이프된 JSON의 상품·청구 기간·통화를 확인합니다. USD와 과거 KRW 확인값을 임의 환산하지 않습니다.
- Figma: 가격과 단위 사이 공백을 처리하고 연간 결제액과 월 환산액을 구분합니다.
- Midjourney: 비교표의 정확한 요금제 열과 월간·연간 행을 읽습니다.
- ElevenLabs: 첫 달 프로모션과 이후 정기 요금을 구분합니다.
- DaVinci Resolve: 정가와 미국 공식 웹 특가를 구분합니다.

Adobe·Canva 등 공개 HTML이나 접근 제한으로 재확인하지 못한 출처는 마지막 확인값과 날짜를 보존합니다. 관리자에서 갱신하더라도 외부 출처의 구조 변경이나 접속 제한은 별도로 확인해야 합니다.

각 요금제의 공식 주소는 `backend/app/data/software_plan_catalog.json`의 `url`과 서비스 상세 화면에 있습니다. 중고 매물은 현재 중고나라만 지원하며, 접속 차단·robots.txt 확인 실패·파싱 실패를 실제 매물 수집 성공으로 계산하지 않습니다.
