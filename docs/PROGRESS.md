# 작업 대장 — 레이어별 한 줄 기록

> 완료 작업을 레이어로 구분해 한 줄씩만 남긴다. 상세는 ADR·Linear(BORB)·git log.
> 정본은 이 파일과 docs/adr/ — Linear 는 추적용 사본이다.

## Layer 1 — 데이터

- marcap 로더 + 무결성 검증 (ADR-0002, e5b7e3e)
- 종목코드 6자리 정규화 + 유니버스 제외: KONEX·스팩·우선주·리츠·관리종목 (ADR-0003, c77c77c)
- 수정주가 보정 — 액면분할/병합 back-adjust, 정본 layer1/adjust.py (ADR-0006, 9faf15e→d17271b 이관)
- 잔여 점검 3건 종료: ETF/ETN 없음·코드 재사용 없음·거래정지 Amount==0⇔Volume==0 (BORB-32, 4a5fdac)

## Layer 2 — 신호 (LLM)

- (미착수 — Backtest Phase 2 에서 시작)

## Layer 3 — 전략

- screening.py 골격 — 임계값 전부 placeholder (b96f9ba)
- 케이스 검사기 전략 오버레이 배관 — STRATEGIES 레지스트리, ma_cross 는 예시 (0261ddb)
- 조건검색 엔진 — 키움 [0150] 식 4카테고리 20조건, 결정론적 pandas (355d593)
- 조건검색 v2 — 룩백에 수정주가 back-adjust 적용 + TA-Lib 패턴분석 11종 (BORB-41 ①)

## Layer 4 — 실행/백테스트

- 거래비용 정액률 다단계 (ADR-0004, 3690d66)
- 백테스트 엔진 골격 — 신호(t)→t+1 시가 체결, 거래정지 연기, 3분할 가드, N<30 플래그 (ADR-0007, d17271b)
- 슬리피지 제곱근 충격 모델 + 유동성 하한 공식 ADV≥Q·(k/s)² (ADR-0004 갱신, 355d593)

## 도구 — 케이스 검사기 웹 (레이어 밖, 탐색용)

- FastAPI + Vite/React + KLineChart Pro 차트, 수정주가·주/월봉·ko-KR (ADR-0005, 53e772a→0261ddb)
- HTS 멀티뷰 셸 — dockview 도킹(탭·플로팅·팝아웃), ECharts 시장맵, 관심종목 (ADR-0008, c1fad55)
- 화이트 테마 + 키움식 조건검색 UI + 워터마크 제거 (355d593)

## 결정/폐기

- ADR-0001 폐기 — 종가 매매 고정 전제 제거, 진입 방식은 전략 확정 후 (52e8aa9)
- 수급 데이터 소스 미정 (ADR-0002 부분 수락, BORB-33 계열 조사 대기)
- 재무=OpenDART·패턴=TA-Lib 조사 완료 (BORB-41) — 패턴은 반영 완료, 재무는 OpenDART 백필 대기
