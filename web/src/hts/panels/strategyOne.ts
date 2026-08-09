// 전략 1호 고정 정의 — **단일 정본** (오너 2026-08-06: "캡슐화 좀 해야할 거 같은데").
//
// 파동·지지저항 값이 카탈로그(서버)·화면 입력·저장 검증 세 곳에 흩어져 있다가
// "② 입력칸 숨김" 변경 때 저장 검증만 옛 가정(사용자 입력 필수)에 남아 저장이 막혔다.
// 이 모듈이 정의의 유일한 출처다 — 화면·요청·검증 전부 여기서 가져다 쓴다.
//
// 값의 출처는 전부 업계 표준이다.
// 꺾임점 = 트레이딩뷰 내장 "Auto Fib Retracement" 규격(ADR-0013 5차),
// 시작점 = 그 꺾임점 위에서 시장 구조로 판정한 "이번 상승장의 출발 바닥"(ADR-0013 6차),
// 지지저항 = 트레이딩뷰 "Support Resistance Channels"(ADR-0014 개정 2).
// 서버에는 항상 요청 데이터로만 나간다(ADR-0009).
//
// 파동 기준은 우리가 지어낸 계산식 세 가지(베이스 길이 → 고정 낙폭 -50% → 낙폭÷변동성×배수)를
// 전부 걷어내고 표준으로 바꾼 것이다. 오너 지시 2026-08-07: "공인된 공식이 있대. 이상한
// 계산식 니 추측대로 만들지 말고." 잔파동 기준이 그 종목 하루 변동폭에 비례해서(자동 방식)
// 종목마다 기준이 알아서 달라진다 — 손으로 맞출 값이 없다.

import type { BandMode, DeviationMode, SrScope, SrSource, StartMode } from '../../api'

// 값은 원본 기본값(좌우 5봉 · 3배) 바로 옆인 **좌우 5봉 · 4배**다. 배수만 3 → 4 로 올렸다.
// 이 값이 오너가 말한 시작점 4건을 전부 맞추고(2026-08-07), 정답과 무관한 넓은 표본
// (거래대금 상위 77종목)에서도 가장 좋았다:
//   되돌림 구간에 살 자리가 남는 종목  51/77 → 65/77
//   현재가의 되돌림 위치(중앙)         73.0% → 58.3%   (눌림 살 자리가 앞에 남는다)
// 맞는 구간이 점이 아니라 면이다 — 좌우 4~7봉 × 3.8~4.6배 전부 4/4.
export const STRATEGY_ONE_WAVE = {
  // 시작점 (ADR-0013 7차) — 평평하게 기던 구간을 거래대금이 늘며 뚫고 올라간 날.
  // Darvas Box(1960) + Weinstein 2국면 돌파(1988). 실측 근거는 아래 주석.
  startMode: '평평한 구간 돌파' as StartMode,
  startBoxBars: 20,
  startVolumeMult: 2,
  startKeepMult: 2,
  zzDepth: 10, // 꼭대기·바닥 판단 — 좌우 5봉 (원본 Depth 기본값 그대로)
  zzDeviation: 4, // 이만큼은 움직여야 한 파동 (원본 3 → 4, 위 실측 근거)
  zzDeviationMode: '자동' as DeviationMode, // 자동 = 하루 변동폭의 배수 / 고정 = %
  // 자리 후보 = 모든 봉의 고가·저가. 차트 기능과 **같은 후보**다 (오너 2026-08-09).
  // 꺾임점만 보면 되돌림 선 5개 중 2~3개가 비었다 — 삼성 3/5 · 현대차 3/5 → 둘 다 5/5.
  srSource: '고가·저가 전부' as SrSource,
  srPrd: 5, // '꺾임점'을 골랐을 때만 쓴다 (좌우 봉수)
  srLoopback: 120, // '최근 N봉' 범위일 때 거슬러 볼 봉 수 — 원본 290 에서 줄였다(아래 근거)
  // 한 자리로 묶는 폭 — 차트 기능(2%)보다 조금 넓은 3%. 실측(2026-08-09) 삼성전자
  // 1/27~2/11 박스 천장 + 3월 저점이 3%에서 하나로 묶인다(165,500~170,600, 11개).
  srChannelWidthPct: 3,
  srMinStrength: 1, // 밴드 안에서 방향이 바뀐 최소 횟수 (오너 확정 2026-08-08)
  // 자리 안에 라운드 가격이 여럿이면 **굵은 숫자 우선**, 단 되돌림 선에서 이만큼 넘게
  // 떨어진 값은 뺀다 (오너 확정 2026-08-09: "굵은 것 우선, 단 선에서 너무 멀면 뺀다").
  // 5%인 근거 — 삼성전자 기준일 2026-08-04 실측:
  //   38.2% 선 258,391 · 250,000 은 -3.25% → 남는다 (오너가 원한 값)
  //   61.8% 선 186,659 · 200,000 은 +7.15% → 빠진다 (자리 맨 윗끝이라 위 차수와 9%밖에 안 벌어졌다)
  srRoundMaxGapPct: 5,
  // 피보나치 선 위아래 밴드 (ADR-0014 2차 개정) — 오너 2026-08-09:
  // "피보나치 선 위아래로 밴드 그려. 그리고 지지저항 찾아. 끝"
  fibBandMode: '자동' as BandMode, // 자동 = 하루에 움직이는 폭 배수 / 파동폭 = % / 가격 = %
  fibBandValue: 0.5,
  srScope: '파동 구간' as SrScope, // 파동 바닥 이후만 본다 — 가격대가 안 어긋난다
} as const

// **원본 좌우 10봉을 왜 5로 내렸나.** 10봉이면 21봉 창에서 제일 높아야 인정이라, 꼭대기
// 찍고 내려온 최근 구간을 통째로 놓친다. 실측(2026-08-09, 기준일 2026-08-04): 삼성전자
// 꼭대기 이후 31봉에서 좌우 10봉으로 잡히는 자리가 **0개**였다. 오너가 눈으로 본
// "25만 구간에서 치고박고" 가 안 잡힌 이유다. 좌우 5봉이면 240,000 이 잡힌다.
// 5종목 × 피보 5선 = 25칸 중 지지저항이 붙은 수:
//   좌우 10봉 10/25 · 좌우 5봉 19/25 · 좌우 3봉 21/25 · 좌우 2봉 24/25
// 원본 값이 10 인 건 그 지표가 **화면 전체에서 굵직한 자리 몇 개**를 뽑는 용도라서다.
// 우리는 피보나치 선 밴드라는 좁은 구간 안에서 찾으므로 더 촘촘해야 한다.
//
// **원본 290 봉을 왜 줄였나.** 290봉이면 14개월인데, 그 사이 종목 가격대가 통째로
// 바뀌면 옛날 자리가 지금 자리와 같은 자격으로 뽑힌다. 실측(2026-08-08, 기준일
// 2026-08-04): 삼성전자가 290봉 동안 53,800 → 374,500 (7배)이라 24만원짜리 종목에
// 67,500~74,000 지지선이 떴다. 하이닉스는 157만원인데 245,000~306,500 이 떴다.
// 이제 기본 범위는 '파동 구간'이라 이 값은 '최근 N봉'을 골랐을 때만 쓰인다.

/** 서버 요청용 평면 키 — /api/simulate·/api/backtest·/api/overlay 공용(ADR-0014 2차 개정 계약). */
export const SR_PAYLOAD = {
  sr_source: STRATEGY_ONE_WAVE.srSource,
  sr_prd: STRATEGY_ONE_WAVE.srPrd,
  sr_loopback: STRATEGY_ONE_WAVE.srLoopback,
  sr_channel_width_pct: STRATEGY_ONE_WAVE.srChannelWidthPct,
  sr_min_strength: STRATEGY_ONE_WAVE.srMinStrength,
  sr_round_max_gap_pct: STRATEGY_ONE_WAVE.srRoundMaxGapPct,
} as const

/** 피보나치 선 띠 — 서버(fib_zone.band_params_from·fibonacci.sr_channels_for)가 읽는 이름. */
export const BAND_PAYLOAD = {
  fib_band_mode: STRATEGY_ONE_WAVE.fibBandMode,
  fib_band_value: STRATEGY_ONE_WAVE.fibBandValue,
  sr_scope: STRATEGY_ONE_WAVE.srScope,
} as const

/** 시작점 — 서버(base_breakout.box_params_from)가 읽는 이름 그대로.
 *
 *  **실측(2026-08-09, 기준일 2026-08-04)** — 오너가 찍은 시작점 두 건과 대조:
 *    현대차   정답 2025-10-16 → 2025-10-16 (+0일)  구간 212,000~225,000 · 중간 218,500
 *    하이닉스 정답 2025-09-10 → 2025-09-10 (+0일)  구간 245,000~288,500 · 중간 266,750
 *  20봉 · 당일 2배 · 이후 2배에서만 둘 다 맞는다. 하이닉스는 이웃 값에서 크게 어긋나
 *  (당일 2배·이후 1.8배 → 97일 이르다) **맞는 구간이 면이 아니라 점**이다. 백테스트로
 *  다시 확인해야 한다 — ADR-0013 6차 값(좌우 5~7봉 × 3.8~4.6배)과 성격이 다르다. */
export const START_PAYLOAD = {
  start_mode: STRATEGY_ONE_WAVE.startMode,
  start_box_bars: STRATEGY_ONE_WAVE.startBoxBars,
  start_volume_mult: STRATEGY_ONE_WAVE.startVolumeMult,
  start_keep_mult: STRATEGY_ONE_WAVE.startKeepMult,
} as const

/** 파동 파라미터 평면 키 — 서버(zigzag_params_from)가 읽는 이름 그대로. */
export const ZZ_PAYLOAD = {
  zz_depth: STRATEGY_ONE_WAVE.zzDepth,
  zz_deviation: STRATEGY_ONE_WAVE.zzDeviation,
  zz_deviation_mode: STRATEGY_ONE_WAVE.zzDeviationMode,
} as const

/** /api/overlay 등 카탈로그 params 형식이 필요한 곳에 넘기는 전체 정의.
 *  **키 이름이 서버 카탈로그(case_overlay `fib_retrace`)와 정확히 같아야 한다** —
 *  다르면 "알 수 없는 파라미터"로 400 이 난다(옛 vol_mult·min_bars 가 그랬다). */
export const STRATEGY_ONE_PARAMS = {
  ...START_PAYLOAD,
  ...ZZ_PAYLOAD,
  ...BAND_PAYLOAD,
  ...SR_PAYLOAD,
} as const

/** 되돌림 비율 — 서버 `fibonacci.FIB_RATIOS` 와 같은 값(업계 표준, ADR-0009 §4 예외).
 *  손절선을 어느 선에 걸지 고르는 데 쓴다. 마지막 값(78.6%)이 5번째 선 = 기본값
 *  (오너 2026-08-10: "5번째 선(78.6%)에 손절"). */
export const FIB_STOP_CHOICES = [0.236, 0.382, 0.5, 0.618, 0.786] as const
export const DEFAULT_FIB_STOP_RATIO: number = FIB_STOP_CHOICES.at(-1) ?? 0.786

/** 이 기법의 파라미터는 사용자 입력이 아니라 고정 정의다 — 화면은 입력칸을 숨기고,
 *  저장 검증은 입력을 요구하지 않으며, 요청 시점에 STRATEGY_ONE_PARAMS 를 주입한다. */
export function isFixedDefinition(entryKey: string): boolean {
  return entryKey === 'fib_retrace'
}
