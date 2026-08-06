// 전략 1호 고정 정의 — **단일 정본** (오너 2026-08-06: "캡슐화 좀 해야할 거 같은데").
//
// 파동·지지저항 값이 카탈로그(서버)·화면 입력·저장 검증 세 곳에 흩어져 있다가
// "② 입력칸 숨김" 변경 때 저장 검증만 옛 가정(사용자 입력 필수)에 남아 저장이 막혔다.
// 이 모듈이 정의의 유일한 출처다 — 화면·요청·검증 전부 여기서 가져다 쓴다.
//
// 값의 출처: 지지저항 = TradingView "Support Resistance Channels" 원본 기본값
// (ADR-0014 개정 2), 사이클 하락 기준 = 임시(피보나치 시작점 새 정의 확정 시 여기만 교체).
// 서버에는 항상 요청 데이터로만 나간다(ADR-0009 — 서버 하드코딩 금지).

export const STRATEGY_ONE_WAVE = {
  cycleDropPct: 50, // 사이클 경계 — 고점 대비 이만큼 빠지면 사이클이 끊긴 것으로 본다(ADR-0013)
  srPrd: 10, // 피벗 기준(좌우 거래일) — 원본 Pivot Period
  srChannelWidthPct: 5, // 존 최대 폭 — 최근 300일 가격폭 대비 % (원본 Maximum Channel Width)
  srLoopback: 290, // 피벗 찾는 구간(거래일) — 원본 Loopback Period
  srMinStrength: 1, // 최소 강도 — 원본 Minimum Strength
  srMaxChannels: 5, // 존 개수(강도순) — 원본 Maximum Number of S/R
} as const

/** 서버 요청용 평면 키 — /api/simulate·/api/backtest·/api/overlay 공용(ADR-0014 개정 2 계약). */
export const SR_PAYLOAD = {
  sr_prd: STRATEGY_ONE_WAVE.srPrd,
  sr_channel_width_pct: STRATEGY_ONE_WAVE.srChannelWidthPct,
  sr_loopback: STRATEGY_ONE_WAVE.srLoopback,
  sr_min_strength: STRATEGY_ONE_WAVE.srMinStrength,
  sr_max_channels: STRATEGY_ONE_WAVE.srMaxChannels,
} as const

/** /api/overlay 등 카탈로그 params 형식이 필요한 곳에 넘기는 전체 정의(사이클 포함). */
export const STRATEGY_ONE_PARAMS = {
  drop_pct: STRATEGY_ONE_WAVE.cycleDropPct,
  ...SR_PAYLOAD,
} as const

/** 이 기법의 파라미터는 사용자 입력이 아니라 고정 정의다 — 화면은 입력칸을 숨기고,
 *  저장 검증은 입력을 요구하지 않으며, 요청 시점에 STRATEGY_ONE_PARAMS 를 주입한다. */
export function isFixedDefinition(entryKey: string): boolean {
  return entryKey === 'fib_retrace'
}
