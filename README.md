# invest-web

AI 기반 주식 분석 및 모의투자 웹 서비스입니다.

국내/해외 종목의 재무 데이터·기술 지표·뉴스를 조합해 Claude AI가 종합 분석 리포트를 생성하고, 사용자 조건(성향·기간·관심 업종)에 맞는 투자 후보를 추천합니다. 가상 자금으로 매수·매도를 연습할 수 있는 모의투자 기능도 포함되어 있습니다.

## 주요 기능

- **종목 분석** — 재무제표, 기술 지표(RSI·MACD·볼린저밴드), 뉴스를 종합한 Claude AI 리포트
- **AI 추천기** — 투자 성향·기간·관심 업종 조건 기반 후보 추천 및 분할매수 계획
- **모의투자** — 가상 계좌로 국내·해외 종목 매수/매도 연습 (실시간 현재가 기준 체결)
- **보유 종목 관리** — 보유 종목 평가손익 조회 및 AI 보유 판단

## 기술 스택

| 구분 | 기술 |
|---|---|
| Frontend | Next.js 15, TypeScript, Tailwind CSS |
| Backend | FastAPI, Python 3.11 |
| AI | Anthropic Claude API (claude-sonnet-4-6) |
| 주식 데이터 (국내) | 한국투자증권 KIS Open API |
| 주식 데이터 (해외) | yfinance |
| 재무 데이터 | DART Open API, KIS Open API |
| DB | SQLite (모의투자 기록) |

## 실행 방법

### 사전 요구사항

- Python 3.11+
- Node.js 18+
- 아래 API 키 발급 필요

| 키 | 발급처 | 비고 |
|---|---|---|
| KIS App Key / Secret | [한국투자증권 OpenAPI](https://apiportal.koreainvestment.com) | 증권 계좌 필요 |
| Anthropic API Key | [Anthropic Console](https://console.anthropic.com) | 유료 |
| DART API Key | [OpenDart](https://opendart.fss.or.kr) | 무료 |

### 1. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`.env` 파일 생성:

```env
KIS_APP_KEY=your_kis_app_key
KIS_APP_SECRET=your_kis_app_secret
KIS_ACCOUNT_NO=your_account_number
KIS_IS_MOCK=true
ANTHROPIC_API_KEY=your_anthropic_api_key
DART_API_KEY=your_dart_api_key
```

서버 실행:

```bash
uvicorn app.main:app --reload --port 8001
```

### 2. Frontend

```bash
cd frontend
npm install
```

`.env.local` 파일 생성:

```env
NEXT_PUBLIC_API_URL=http://localhost:8001
```

개발 서버 실행:

```bash
npm run dev
```

브라우저에서 `http://localhost:3000` 접속

## API 키 없이 사용 가능한 기능

| 기능 | KIS 없이 | Anthropic 없이 |
|---|---|---|
| 해외 주식 조회 | ✅ | ✅ |
| 해외 종목 모의투자 | ✅ | ✅ |
| AI 분석 리포트 | ✅ | ❌ (리포트 미생성) |
| 국내 주식 조회 | ❌ | ✅ |
| 국내 종목 모의투자 | ❌ | ✅ |

## 주의사항

이 서비스는 투자 판단 보조용입니다. 추천 결과는 알고리즘 기반 참고 자료이며, 실제 투자 손익에 대한 책임은 투자자 본인에게 있습니다.
