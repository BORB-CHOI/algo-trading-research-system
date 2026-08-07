// 전략 1호 고정 정의 — **단일 정본** (오너 2026-08-06: "캡슐화 좀 해야할 거 같은데").
//
// 파동·지지저항 값이 카탈로그(서버)·화면 입력·저장 검증 세 곳에 흩어져 있다가
// "② 입력칸 숨김" 변경 때 저장 검증만 옛 가정(사용자 입력 필수)에 남아 저장이 막혔다.
// 이 모듈이 정의의 유일한 출처다 — 화면·요청·검증 전부 여기서 가져다 쓴다.
//
// 값의 출처: 지지저항 = TradingView "Support Resistance Channels" 원본 기본값
// (ADR-0014 개정 2), 사이클 하락 기준 = 임시(피보나치 시작점 새 정의 확정 시 여기만 교체).
// 서버에는 항상 요청 데이터로만 나간다(ADR-0009 — 서버 하드코딩 금지).

// 사이클이 끊겼다고 보는 기준 — **변동성 방식**(ADR-0013 개정 3차, 오너 2026-08-07).
// 고정 낙폭(-50%)은 종목마다 결과가 3년 반 전~일주일 전으로 널뛰었다. 로보티즈는 -45%가
// 일상이고 삼성전자는 -25%도 큰 사건이라, 같은 숫자를 들이대면 안 된다.
// 낙폭을 **그 종목 평소 변동성으로 나눈 값**을 쓰면 종목이 자기 기준을 스스로 갖는다.
// 값은 오너가 찍은 시작점 15건에서 역산한 것 — 화면에서 돌려보며 조정한다.
export const STRATEGY_ONE_WAVE = {
  cycleVolMult: 10, // 낙폭이 평소 변동성의 몇 배면 사이클이 끊기는가
  cycleMinBars: 100, // 그 하락이 최소 몇 봉 끌어야 하는가 ("주르르륵 흐른다")
  cycleLookbackBars: 500, // 신고가로부터 몇 봉까지 거슬러 보는가 (오너: 차트 500봉)
  cycleDropPct: 50, // (구) 고정 낙폭 방식 — 변동성 방식을 끄면 이 값을 쓴다
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
  vol_mult: STRATEGY_ONE_WAVE.cycleVolMult,
  min_bars: STRATEGY_ONE_WAVE.cycleMinBars,
  lookback_bars: STRATEGY_ONE_WAVE.cycleLookbackBars,
  ...SR_PAYLOAD,
} as const

/** 이 기법의 파라미터는 사용자 입력이 아니라 고정 정의다 — 화면은 입력칸을 숨기고,
 *  저장 검증은 입력을 요구하지 않으며, 요청 시점에 STRATEGY_ONE_PARAMS 를 주입한다. */
export function isFixedDefinition(entryKey: string): boolean {
  return entryKey === 'fib_retrace'
}
