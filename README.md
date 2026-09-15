# Theme Radar

한국(KOSPI/KOSDAQ 보통주)과 미국(S&P 500) 주식시장의 **섹터/테마별 수익률과 순위 변화**를 관찰하는 대시보드.

## 이 시스템이 답하는 질문

1. 과거 어느 섹터/테마의 성과가 높았는가? — 기간별 수익률과 **순위 범프 차트**
2. 어떤 섹터가 시장 등락에 크게 기여했는가? — **기여도 분해**(시장 수익률 = Σ 섹터 기여도)
3. 그 성과가 일부 대형 종목에 집중되어 있는가? — **종목 기여도 분해 및 집중도 지표**

## 기본 사양 요약

| 항목 | 값 |
| --- | --- |
| 조회 대상 | KR: KOSPI/KOSDAQ 보통주 · US: S&P 500 구성종목 |
| 분류 체계 | KR: WI26 · US: GICS(준거) — **자체 제공 매핑 테이블** |
| 기간 단위 | 월(Month), ISO Week — **화면 기본값: 주(Week)** |
| 섹터 수익률 | **직전 기간 말 시가총액 가중** 평균 |
| 통화 | KR: KRW · US: USD (시장 간 환산·통합 없음) |

## 문서 목록

| 문서 | 내용 |
| --- | --- |
| [01-requirements.md](docs/01-requirements.md) | 요구사항, 유니버스, 분류 체계, 기간 체계, 범위 경계, 용어 |
| [02-domain-and-data-model.md](docs/02-domain-and-data-model.md) | 도메인 모델, ERD, 테이블 DDL, 입력 데이터 규격 |
| [03-metrics-spec.md](docs/03-metrics-spec.md) | 지표 계산 명세(수익률·가중치·기여도·순위·집중도), 예외 처리, 검증 규칙 |
| [04-pipeline.md](docs/04-pipeline.md) | 아키텍처, 배치 파이프라인, 재계산 정책, 데이터 품질 |
| [05-api-spec.md](docs/05-api-spec.md) | REST API 명세 |
| [06-ui-spec.md](docs/06-ui-spec.md) | 화면 구성, 범프 차트 및 드릴다운 명세 |
| [07-price-ingestion.md](docs/07-price-ingestion.md) | 주가·상장주식수 수집 모듈: 출처 검증, yfinance·FinanceDataReader 사용 범위, 분할·병합 소급 갱신, 수집 단계 검증 |
| [08-library-review.md](docs/08-library-review.md) | FinanceDataReader·yfinance 검토 기록과 채택 결정, Yahoo 분할 이벤트의 분사 혼입 |
| [09-tech-stack.md](docs/09-tech-stack.md) | 기술 스택(Python·SQLite·FastAPI·ECharts)과 선택 이유, 저장소 구조, 배치 실행, 구현 순서 |

## 표기 규약

- 기간 식별자: 주 `2026-W03`(ISO-8601 week-year), 월 `2026-01`
- 수익률은 소수(0.0123 = +1.23%)로 저장하고 표시 시점에 백분율로 변환
- 시점 표기에서 `t`는 대상 기간, `t-1`은 직전 기간을 의미
