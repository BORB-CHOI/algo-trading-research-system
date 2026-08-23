import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  ActionType,
  dispose as disposeKLineChart,
  init,
  LoadDataType,
  registerIndicator,
  registerOverlay,
  type Chart,
  type KLineData,
} from 'klinecharts'
import { mergeLive, shouldApply, todayStart, type LiveBarResponse } from './liveBar'
import { registerKoreanLocale } from './locales'

// 장중 실시간 오늘 봉 폴링 간격. 장 밖·탭 숨김·화면 밖이면 느리게.
const LIVE_POLL_MS = 2000
const LIVE_POLL_IDLE_MS = 10_000
const LIVE_POLL_CLOSED_MS = 60_000
import { allVisible, type OverlayVisibility } from './simVisibility'
import {
  fetchPriceZones,
  fetchSupportResistance,
  postOverlay,
  postSignals,
  type OverlayFill,
  type OverlayLine,
  type OverlaySeries,
  type OverlayTouch,
  type Signal,
} from './api'

/** registerIndicator 의 draw 콜백이 받는 인자. klinecharts 가 타입을 내보내지 않아
 *  우리가 쓰는 필드만 추려 선언한다 — 그리기 헬퍼로 쪼개려면 이름이 필요하다. */
type DrawCtx = {
  ctx: CanvasRenderingContext2D
  kLineDataList: KLineData[]
  visibleRange: { from: number; to: number }
  bounding: { width: number; height: number }
  xAxis: { convertToPixel: (v: number) => number }
  yAxis: { convertToPixel: (v: number) => number }
}

// 한국식: 상승 = 빨강, 하락 = 파랑. 형광 말고 차분한 톤.
const RED = '#e01e1e'
const BLUE = '#1668d0'

/** 그린 수평선 옆에 가격을 **항상** 보여줄지. 그리기 도구 등록은 전역 1회라
 *  런타임 값은 이 모듈 변수로 읽는다(그릴 때마다 다시 읽힌다). 기본은 꺼짐 —
 *  오너가 버튼으로 켠다(2026-08-18). */
let alwaysShowLinePrice = false

/** 수평선 가격을 항상 보일지 정한다. 바꾼 뒤에는 다시 그려야 반영된다.
 *  **내보내지 않는다** — 이 파일 안 버튼만 쓴다(fast refresh 경고를 늘리지 않게). */
function setAlwaysShowLinePrice(on: boolean): void {
  alwaysShowLinePrice = on
}

let indicatorsRegistered = false
function ensureIndicators(): void {
  if (indicatorsRegistered) return
  indicatorsRegistered = true

  // 거래대금(turnover) 보조지표 — 내장엔 없어 전역 등록하면 Pro 지표 메뉴에도 뜬다.
  registerIndicator<{ turnover: number }>({
    name: 'TURNOVER',
    shortName: '거래대금',
    figures: [{ key: 'turnover', title: '거래대금: ', type: 'bar' }],
    calc: (dataList) => dataList.map((d) => ({ turnover: (d.turnover as number | undefined) ?? 0 })),
  })

  // ── 그리기 도구 '선분' 라벨 강화 — 트레이딩뷰처럼 양 끝점에 가격·날짜, 끝점엔 변화율까지
  // (오너 지시 2026-08-06). registerOverlay 는 같은 이름을 조용히 덮어쓴다(klinecharts 소스
  // 확인) — 내장 segment 정의(두 점 선)에 텍스트 도형만 얹어 재등록한다.
  const day = (ts?: number) => (ts ? new Date(ts).toISOString().slice(2, 10) : '')
  registerOverlay({
    name: 'segment',
    totalStep: 3,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: true,
    createPointFigures: ({ coordinates, overlay }) => {
      if (coordinates.length !== 2) return []
      const pts = overlay.points
      const texts = coordinates.map((c, i) => {
        const v = pts[i]?.value
        if (v == null) return null
        let text = `${Math.round(v).toLocaleString('ko-KR')}`
        const v0 = pts[0]?.value
        if (i === 1 && v0) text += ` (${(((v - v0) / v0) * 100).toFixed(1)}%)`
        const d = day(pts[i]?.timestamp)
        if (d) text += ` · ${d}`
        return {
          type: 'text',
          ignoreEvent: true,
          attrs: { x: c.x + 6, y: c.y - 6, text, baseline: 'bottom' },
          styles: {
            color: '#1668d0',
            size: 11,
            backgroundColor: 'rgba(255,255,255,0.9)',
            paddingLeft: 4,
            paddingRight: 4,
            paddingTop: 2,
            paddingBottom: 2,
            borderRadius: 2,
          },
        }
      })
      return [
        { type: 'line', attrs: { coordinates } },
        ...texts.filter((t): t is NonNullable<typeof t> => t != null),
      ]
    },
  })

  // ── 그리기 도구 '수평선' — 가격을 **항상** 보이게 (오너 2026-08-18).
  //
  // 내장 정의는 선 하나만 그리고 가격은 **Y축 딱지**로만 낸다. 그 딱지는 지금 눌러
  // 고른 선에만 뜬다 — klinecharts `OverlayYAxisView.getDefaultFigures` 가
  // `overlay.id === clickInstanceInfo.instance?.id` 로 거른다. 그래서 배경을 누르면
  // 사라진다. 옵션으로 끌 수 있는 게 아니라, 선 위에 글자를 직접 얹어 재등록한다 —
  // `createPointFigures` 는 고르든 말든 늘 그려지고, 끌면 좌표가 같이 따라온다.
  //
  // 기본은 꺼짐이다. 수평선을 여러 개 그으면 오른쪽 끝에 이미 줄지어 있는
  // 되돌림·지지저항 글자와 겹치기 때문에, 켤지 말지는 오너가 버튼으로 정한다.
  registerOverlay({
    name: 'horizontalStraightLine',
    totalStep: 2,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: true, // 눌러 고르면 Y축 딱지도 그대로 뜬다
    createPointFigures: ({ coordinates, bounding, overlay }) => {
      const y = coordinates[0]?.y
      if (y == null) return []
      const line = {
        type: 'line',
        attrs: { coordinates: [{ x: 0, y }, { x: bounding.width, y }] },
      }
      const v = overlay.points[0]?.value
      if (!alwaysShowLinePrice || v == null) return [line]
      return [
        line,
        {
          type: 'text',
          ignoreEvent: true, // 글자가 선 집는 걸 방해하지 않게
          attrs: {
            x: bounding.width - 2,
            y: y - 3,
            text: Math.round(v).toLocaleString('ko-KR'),
            align: 'right',
            baseline: 'bottom',
          },
          styles: {
            color: '#1668d0',
            size: 11,
            backgroundColor: 'rgba(255,255,255,0.9)',
            paddingLeft: 4,
            paddingRight: 4,
            paddingTop: 2,
            paddingBottom: 2,
            borderRadius: 2,
          },
        },
      ]
    },
  })
}

// ── 전략 신호 지표 — 차트 인스턴스별 ──────────────────────────
// 신호 계산은 전부 파이썬(/api/signals). 여기는 받은 결과를 그리기만 한다.
// 멀티뷰에서 차트마다 종목이 다를 수 있으므로, 신호 저장소를 전역 하나로 두면
// 마지막 응답이 모든 차트에 그려진다. 차트마다 고유 이름의 지표를 등록해
// 클로저로 자기 저장소만 읽게 한다. (klinecharts 에 unregister 가 없어
// 정의 자체는 남지만, 마운트당 1개라 유해하지 않다.)
type SignalStore = {
  indicatorName: string
  set: (signals: Signal[]) => void
  clear: () => void
  size: () => number
}

let signalSeq = 0

function createSignalIndicator(): SignalStore {
  const byTime = new Map<number, Signal>()
  const name = `SIGNALS_${++signalSeq}`
  registerIndicator({
    name,
    shortName: '전략신호',
    figures: [],
    calc: (dataList) => dataList.map(() => ({})),
    draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }) => {
      if (byTime.size === 0) return true
      ctx.save()
      ctx.font = '12px sans-serif'
      ctx.textAlign = 'center'
      for (let i = visibleRange.from; i < visibleRange.to; i++) {
        const bar = kLineDataList[i]
        if (!bar) continue
        const sig = byTime.get(bar.timestamp)
        if (!sig) continue
        const x = xAxis.convertToPixel(i)
        if (sig.side === 'buy') {
          ctx.fillStyle = RED
          ctx.fillText('▲', x, yAxis.convertToPixel(bar.low) + 16)
        } else {
          ctx.fillStyle = BLUE
          ctx.fillText('▼', x, yAxis.convertToPixel(bar.high) - 6)
        }
      }
      ctx.restore()
      return true
    },
  })
  return {
    indicatorName: name,
    set(signals) {
      byTime.clear()
      for (const s of signals) byTime.set(new Date(`${s.time}T00:00:00`).getTime(), s)
    },
    clear: () => byTime.clear(),
    size: () => byTime.size,
  }
}

// ── 전략 오버레이 지표 — 차트 인스턴스별 (신호 지표와 같은 per-instance 패턴) ──
// 피보나치 레벨·라운드 피겨·베이스/신고가 수평선은 전부 파이썬(/api/overlay)이 계산한다.
// 여기는 받은 선·터치 마커를 그리기만 한다 — 시각 전용, 매매 판단 아님.
// 라이트 테마 대비색: fib 주황 실선 / sr 회색 점선 / anchor 파랑 점선.
const OVERLAY_COLORS: Record<OverlayLine['kind'], string> = {
  fib: '#d97706', // 피보나치 레벨 — 실선
  sr: '#6b7280', // 지지/저항 수평선 (ADR-0014) — 점선
  ob: '#7c3aed', // 오더블록 — 보라 (ADR-0014 5차)
  fvg: '#0d9488', // 가격 빈틈(FVG) — 청록
  anchor: '#2f6fed', // 사이클 저점/고점 기준선 — 점선
  buy: '#dd3c44', // 분할 매수 목표가 — 한국식 상승색(빨강)과 맞춘다
  sell: '#0062df', // 분할 매도 목표가 — 하락색(파랑)
  stop: '#111827', // 손절선 — 매매선과 확실히 구분되는 진회색
}
// 손절은 "여기 깨지면 끝"이라 화면을 가로지르는 선으로 남긴다.
const THICK_KINDS = new Set<OverlayLine['kind']>(['stop'])
// 매수·매도는 **선을 안 긋는다** (오너 2026-08-09: "선으로 긋지 말고 화살표로만").
// 체결은 그 봉 위에 화살표 + 가격, 아직 안 걸린 목표가는 오른쪽 끝에 화살표 + 가격.
const ARROW_KINDS = new Set<OverlayLine['kind']>(['buy', 'sell'])
const TOUCH_COLOR = '#d97706' // 레벨 근접(◆) 마커

// 도구 막대에 그리는 차트 기능 세 겹. 라벨(=값)과 설명문.
const TOOL_LAYERS: readonly (readonly [ToolLayer, string])[] = [
  ['지지저항', '가격이 여러 번 닿은 자리'],
  ['오더블록', '세게 밀고 나간 봉 직전의 마지막 반대색 봉'],
  ['가격 빈틈', '세 봉이 안 겹쳐 생긴 구간 (FVG). 이미 다시 지나간 자리는 흐리게'],
] as const
const VWAP_COLOR = '#7c3aed' // 앵커 VWAP — 피보나치/매매선과 겹치지 않는 보라

/** 차트 위에 얹는 겹치기 — 라벨은 쉬운 말로. 정본 타입은 simVisibility.OverlayVisibility. */
// 매수·매도 주문가 가로선을 없앴으므로(오너 2026-08-22) 그 칸도 뺀다 — 눌러도 바뀌는 게
// 없는 버튼이 남아 있으면 "안 먹는다"로 읽힌다. 산·판 자리는 '사고판 자리'로 켜고 끈다.
const OVERLAY_LAYERS: readonly (readonly [keyof OverlayVisibility, string])[] = [
  ['fib', '되돌림'],
  ['sr', '지지저항'],
  ['anchor', '파동'],
  ['stop', '손절'],
  ['fills', '사고판 자리'],
] as const

/** 차트 도구 겹치기 — 각각 따로 켜고 끈다 (오너 2026-08-09: "둘 다 넣되 따로 켜고 끔"). */
export type ToolLayer = '지지저항' | '오더블록' | '가격 빈틈'

type OverlayStore = {
  indicatorName: string
  set: (lines: OverlayLine[], touches: OverlayTouch[]) => void
  /** 차트 도구로 그리는 선 — 전략 오버레이와 **따로** 둔다. 도구는 전략이 아니라
   *  차트 기능이라(오너 2026-08-09) 전략을 바꿔도 안 지워진다.
   *  `name` 별 슬롯이라 지지저항·오더블록·가격 빈틈을 따로 켜고 끌 수 있다. */
  setTool: (name: ToolLayer, lines: OverlayLine[]) => void
  /** 시뮬레이션 결과 — 체결 마커와 곡선(앵커 VWAP). 없으면 빈 배열로 지운다. */
  setSim: (fills: OverlayFill[], series: OverlaySeries[]) => void
  setVisibility: (v: OverlayVisibility) => void
  clear: () => void
}

/** 'YYYY-MM-DD' → 그날 0시 타임스탬프. 봉 timestamp 와 맞추는 유일한 창구다. */
function dayTs(day: string): number {
  return new Date(`${day}T00:00:00`).getTime()
}

type SeriesDraw = { label: string; color: string; byTime: Map<number, number> }

/** 곡선(앵커 VWAP). 앵커 이전 구간은 값이 없어 선을 끊는다 — 이어 그리면 없는 지지선이 보인다. */
function drawSeries(c: DrawCtx, list: SeriesDraw[]): void {
  const { ctx, kLineDataList, visibleRange, xAxis, yAxis } = c
  for (const s of list) {
    ctx.strokeStyle = s.color
    ctx.lineWidth = 1.5
    ctx.setLineDash([])
    ctx.beginPath()
    let started = false
    for (let i = visibleRange.from; i < visibleRange.to; i++) {
      const bar = kLineDataList[i]
      const v = bar ? s.byTime.get(bar.timestamp) : undefined
      if (v == null) {
        started = false
        continue
      }
      const x = xAxis.convertToPixel(i)
      const y = yAxis.convertToPixel(v)
      if (started) ctx.lineTo(x, y)
      else {
        ctx.moveTo(x, y)
        started = true
      }
    }
    ctx.stroke()
  }
  ctx.lineWidth = 1
}

// 가격을 라벨에 병기할 선 종류 — 차트 그리기 도구(피보나치 선분)처럼 "비율 + 가격"으로
// 읽히게 fib 도 병기한다 (오너 2026-08-06: 트레이딩뷰식 표기).
// sr 은 빠진다 — 라벨 안에 이미 라운드 가격이 들어 있어 뒤에 또 붙이면 같은 숫자가 두 번
// 찍힌다("50.0% 지지저항 · … · 210,000 · 220,000 220,000", 실측 2026-08-09).
const PRICE_LABEL_KINDS = new Set<OverlayLine['kind']>(['stop', 'anchor', 'fib'])

/** 아직 안 걸린 매수·매도 목표가 — 오른쪽 끝에 화살표 하나와 가격만. */

/** 수평선 + 우측 라벨. y축 범위 밖 레벨은 건너뛴다.
 *
 *  자리가 많으면(차트 기능 지지저항은 200봉에서 28~31개) 라벨이 서로 포개져 아무것도
 *  못 읽는다. **선은 다 그리고 라벨만** 앞 라벨과 붙으면 건너뛴다 — 마우스를 올리거나
 *  줌을 키우면 그때 읽힌다. */
const LABEL_GAP_PX = 13

/** 자리가 **생긴 날의 x좌표**. 없거나 화면 왼쪽 밖이면 0(왼쪽 끝부터).
 *
 *  오더블록·가격 빈틈은 그 봉에서 생긴 자리라 그 전 과거엔 존재하지 않았다. 화면을
 *  통째로 가로지르게 그리면 생기기도 전인 구간까지 덮여 차트가 안 보인다
 *  (오너 지적 2026-08-09). 표준 지표들도 그 봉부터 오른쪽으로만 사각형을 뻗는다. */
function startX(c: DrawCtx, day?: string): number {
  if (!day) return 0
  const ts = dayTs(day)
  const { kLineDataList, visibleRange, xAxis } = c
  for (let i = visibleRange.from; i < visibleRange.to; i++) {
    const bar = kLineDataList[i]
    if (bar && bar.timestamp >= ts) return xAxis.convertToPixel(i)
  }
  return 0
}

function drawLines(c: DrawCtx, list: OverlayLine[]): void {
  const { ctx, bounding, yAxis } = c
  const labelled: number[] = []
  // **아직 살아 있는 자리를 먼저** 그린다 — 라벨 자리가 한정돼 있어서, 순서를 안 정하면
  // 이미 지나간 자리가 라벨을 먼저 차지해 정작 쓸 자리의 값이 안 보인다.
  const ordered = [...list].sort((a, b) => Number(a.dim ?? false) - Number(b.dim ?? false))
  for (const ln of ordered) {
    const y = Math.round(yAxis.convertToPixel(ln.price)) + 0.5 // +0.5 = 1px 라인 선명하게
    if (y < 0 || y > bounding.height) continue
    // 이미 지나간 자리(메워진 빈틈·뚫린 오더블록)는 흐리게 — 지금 쓸 자리와 구분된다.
    const color = ln.dim ? `${OVERLAY_COLORS[ln.kind]}66` : OVERLAY_COLORS[ln.kind]
    // 폭 있는 자리는 반투명 띠를 먼저 깐다 — **그 선과 같은 색**이라 무엇의 띠인지
    // 바로 읽힌다(피보나치 주황 / 오더블록 보라 / 가격 빈틈 청록).
    // 피보나치 띠는 선 위아래로 벌린 밴드고(오너 2026-08-09: "회색 띠가 왜 지지저항에?"),
    // 오더블록·빈틈 띠는 그 자리가 실제로 차지하는 폭이다.
    const x0 = startX(c, ln.start)
    if (ln.top != null && ln.bottom != null && ln.top > ln.bottom) {
      const yTop = yAxis.convertToPixel(ln.top)
      const yBot = yAxis.convertToPixel(ln.bottom)
      ctx.fillStyle = `${OVERLAY_COLORS[ln.kind]}${ln.dim ? '0a' : '1a'}` // 같은 색, 지나간 건 더 옅게
      ctx.fillRect(x0, yTop, bounding.width - x0, Math.max(1, yBot - yTop))
    }
    const thick = THICK_KINDS.has(ln.kind)
    ctx.strokeStyle = color
    ctx.lineWidth = thick ? 2 : 1
    ctx.setLineDash(ln.kind === 'fib' || thick ? [] : [4, 3])
    ctx.beginPath()
    ctx.moveTo(x0, y)
    ctx.lineTo(bounding.width, y)
    ctx.stroke()
    // 우측 라벨 — 캔들과 겹쳐도 읽히게 반투명 흰 바탕(라이트 테마) 위에 그린다.
    // 선 **아래**에 둔다 — 위에 얹으면 바로 위 선·라벨과 포개져 숫자가 안 읽힌다
    // (오너 지적 2026-08-06: "퍼센테이지랑 숫자값을 선 아래에").
    ctx.setLineDash([])
    ctx.lineWidth = 1
    if (labelled.some((y2) => Math.abs(y2 - y) < LABEL_GAP_PX)) continue
    labelled.push(y)
    const label = PRICE_LABEL_KINDS.has(ln.kind)
      ? `${ln.label} ${ln.price.toLocaleString('ko-KR')}`
      : ln.label
    const pad = 3
    const w = ctx.measureText(label).width
    const x = bounding.width - w - pad * 2 - 2
    ctx.fillStyle = color
    ctx.textAlign = 'left'
    ctx.fillText(label, x + pad, y + 13)
  }
}

// 체결 마커 색 — 증권사 앱(나무)과 같게 맞춘다 (오너 2026-08-22: "디자인은 이거랑 똑같게 해라").
// 매수는 주황 동그라미에 흰 ↑, 매도는 남색 동그라미에 흰 ↓.
const MARK_FILL = { buy: '#f5821f', sell: '#3b5ba9', stop: '#111827' } as const
const MARK_R = 8 // 동그라미 반지름
const MARK_GAP = 5 // 봉 끝에서 띄우는 간격
const MARK_STEP = MARK_R * 2 + 13 // 한 봉에 여러 건이면 이만큼씩 쌓는다

/** 체결 표식 하나 — 동그라미 + 흰 화살표, 그 바깥에 차수·가격. */
function drawMark(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  up: boolean,
  label: string,
  price: number,
): void {
  ctx.beginPath()
  ctx.arc(x, y, MARK_R, 0, Math.PI * 2)
  ctx.fillStyle = color
  ctx.fill()

  ctx.fillStyle = '#ffffff'
  ctx.font = 'bold 11px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(up ? '↑' : '↓', x, y + 0.5)

  ctx.textBaseline = 'alphabetic'
  ctx.fillStyle = color
  ctx.font = 'bold 10px sans-serif'
  // 글자는 화살표가 가리키는 **바깥쪽**으로 — 캔들을 안 가린다.
  ctx.fillText(`${label} ${price.toLocaleString('ko-KR')}`, x, up ? y + MARK_R + 11 : y - MARK_R - 4)
  ctx.font = '11px sans-serif'
}

/** 체결 표식 — **매수는 봉 아래, 매도는 봉 위**. 한 봉에 여러 건이면 쌓는다.
 *
 *  오너 2026-08-22: "매수는 봉 아래, 매도는 봉 위에다", "봉 바로 밑에 매수/매도
 *  1차, 2차, 3차가 쌓이는 식의 표시만 필요한 거다."
 *
 *  전에는 **체결가 자리**에 그려서 캔들 몸통 위에 겹쳤고, 같은 봉에 두 건이면 포개졌다.
 *  이제 봉의 저가/고가를 기준으로 바깥에 붙이고 차수 순서대로 쌓는다.
 */
function drawFillMarks(c: DrawCtx, fillsByTime: Map<number, OverlayFill[]>): void {
  const { ctx, kLineDataList, visibleRange, xAxis, yAxis } = c
  for (let i = visibleRange.from; i < visibleRange.to; i++) {
    const bar = kLineDataList[i]
    if (!bar) continue
    const marks = fillsByTime.get(bar.timestamp)
    if (!marks) continue
    const x = xAxis.convertToPixel(i)
    const byStage = [...marks].sort((a, b) => (a.stage ?? 0) - (b.stage ?? 0))

    let yb = yAxis.convertToPixel(bar.low) + MARK_R + MARK_GAP
    let ys = yAxis.convertToPixel(bar.high) - MARK_R - MARK_GAP
    for (const m of byStage) {
      if (m.side === 'buy') {
        drawMark(ctx, x, yb, MARK_FILL.buy, true, `${m.stage}차`, m.price)
        yb += MARK_STEP // 아래로 쌓는다
      } else {
        const isStop = m.stage === 0 // stage 0 = 손절 체결
        drawMark(ctx, x, ys, isStop ? MARK_FILL.stop : MARK_FILL.sell, false,
          isStop ? '손절' : `${m.stage}차`, m.price)
        ys -= MARK_STEP // 위로 쌓는다
      }
    }
  }
}

/** 봉마다 찍히는 마커를 공통으로 순회한다 (터치 ◆). */
function forEachBarMark<T>(
  c: DrawCtx,
  byTime: Map<number, T[]>,
  fn: (mark: T, x: number) => void,
): void {
  const { kLineDataList, visibleRange, xAxis } = c
  for (let i = visibleRange.from; i < visibleRange.to; i++) {
    const bar = kLineDataList[i]
    if (!bar) continue
    const marks = byTime.get(bar.timestamp)
    if (!marks) continue
    const x = xAxis.convertToPixel(i)
    for (const m of marks) fn(m, x)
  }
}

let overlaySeq2 = 0

function createOverlayIndicator(): OverlayStore {
  let lines: OverlayLine[] = []
  const toolLines = new Map<ToolLayer, OverlayLine[]>()
  const touchesByTime = new Map<number, OverlayTouch[]>()
  const fillsByTime = new Map<number, OverlayFill[]>()
  let seriesList: SeriesDraw[] = []
  let vis = allVisible()
  const name = `OVERLAY_${++overlaySeq2}`
  registerIndicator({
    name,
    shortName: '전략오버레이',
    figures: [],
    calc: (dataList) => dataList.map(() => ({})),
    draw: (c) => {
      const empty =
        lines.length === 0 &&
        toolLines.size === 0 &&
        touchesByTime.size === 0 &&
        fillsByTime.size === 0 &&
        seriesList.length === 0
      if (empty) return true
      const { ctx, yAxis } = c
      ctx.save()
      ctx.font = '11px sans-serif'
      ctx.lineWidth = 1

      const visOf = vis as Partial<Record<OverlayLine['kind'], boolean>>
      drawSeries(c, seriesList) // 곡선을 먼저 — 수평선 아래에 깔린다 (현재 시뮬은 곡선 없음)
      const tools = [...toolLines.values()].flat()
      const shown = [...tools, ...lines].filter((ln) => visOf[ln.kind] ?? true)
      // 매수·매도 **주문가 가로선은 안 그린다** (오너 2026-08-22: "이 표시 필요 없다
      // 지워라. 봉 바로 밑에 매수/매도 1차, 2차, 3차가 쌓이는 식의 표시만 필요한 거다").
      // 서버도 그 선을 안 보낸다 — 여기 거르는 건 옛 응답·보관본 대비다.
      drawLines(c, shown.filter((ln) => !ARROW_KINDS.has(ln.kind)))

      ctx.textAlign = 'center'
      ctx.fillStyle = TOUCH_COLOR
      forEachBarMark(c, touchesByTime, (t, x) => {
        ctx.fillText('◆', x, yAxis.convertToPixel(t.price) + 4)
      })

      if (vis.fills) drawFillMarks(c, fillsByTime)

      ctx.restore()
      return true
    },
  })

  function index<T extends { time: string }>(items: T[], into: Map<number, T[]>): void {
    into.clear()
    for (const it of items) {
      const ts = dayTs(it.time)
      const arr = into.get(ts)
      if (arr) arr.push(it)
      else into.set(ts, [it])
    }
  }

  return {
    indicatorName: name,
    set(nextLines, nextTouches) {
      lines = nextLines
      index(nextTouches, touchesByTime)
    },
    setTool(name, nextLines) {
      if (nextLines.length === 0) toolLines.delete(name)
      else toolLines.set(name, nextLines)
    },
    setSim(fills, series) {
      index(fills, fillsByTime)
      seriesList = series.map((s) => ({
        label: s.label,
        color: s.color ?? VWAP_COLOR,
        byTime: new Map(s.points.map((p) => [dayTs(p.time), p.value])),
      }))
    },
    setVisibility(v) {
      vis = v
    },
    clear() {
      lines = []
      // toolLines 는 안 지운다 — 차트 도구는 전략과 무관하게 켜져 있어야 한다.
      touchesByTime.clear()
      fillsByTime.clear()
      seriesList = []
    },
  }
}

// ─────────────────────────────────────────────────────────────
// 데이터 적재 — 우리 FastAPI 를 직접 부른다.
// KLineChart Pro(껍데기)를 벗으면서 Pro 의 Datafeed 규격도 같이 걷어냈다(ADR-0005 개정).
// Pro 가 하던 일은 셋뿐이었다: 데이터 받아오기 · 종목/주기 바꾸기 · 툴바 그리기.
// 앞의 둘은 여기서, 셋째는 위쪽 도구 막대에서 한다. 지표·그리기 도구는 **엔진에 이미 있다**.
// ─────────────────────────────────────────────────────────────

/** 봉 주기 — 서버 `/api/candles?period=` 가 받는 값 그대로.
 *  분봉은 나무증권 수집본(2026-08-15~). 1~15분은 서버 보관이 약 6주라 그만큼만 보인다. */
export type BarPeriod =
  | 'day'
  | 'week'
  | 'month'
  | 'min1'
  | 'min3'
  | 'min5'
  | 'min10'
  | 'min15'
  | 'min30'
  | 'min60'
  | 'min120'
  | 'min240'

export const PERIOD_LABEL: Record<BarPeriod, string> = {
  min1: '1분',
  min3: '3분',
  min5: '5분',
  min10: '10분',
  min15: '15분',
  min30: '30분',
  min60: '60분',
  min120: '120분',
  min240: '240분',
  day: '일',
  week: '주',
  month: '월',
}

/** 툴바에 늘어놓을 순서·묶음. 분봉은 드롭다운 하나로 접는다 — 버튼 12개는 너무 길다. */
const MINUTE_PERIODS: BarPeriod[] = [
  'min1',
  'min3',
  'min5',
  'min10',
  'min15',
  'min30',
  'min60',
  'min120',
  'min240',
]
const CALENDAR_PERIODS: BarPeriod[] = ['day', 'week', 'month']

/** 시장 — 2025-03 넥스트레이드(NXT) 개장 후 체결이 두 거래소에 나뉜다. 통합 = KRX+NXT 전체.
 *  통합이 정본(오너 2026-08-15). NXT 미상장 종목이나 수집 전이면 서버가 KRX 값으로 대신 준다. */
export type BarMarket = 'unt' | 'krx' | 'nxt'

export const MARKET_LABEL: Record<BarMarket, string> = { unt: '통합', krx: 'KRX', nxt: 'NXT' }

/** 하루 안쪽 주기(분봉). 등록돼 있으면 `baseStampOf` 가 시각까지 붙인다. */
const INTRADAY_PERIODS = new Set<BarPeriod>(MINUTE_PERIODS)

const p2 = (n: number) => String(n).padStart(2, '0')

/** 봉 하나 → **기준 시점** 문자열.
 *
 *  일·주·월봉은 날짜까지다. 주봉·월봉이라고 따로 계산할 게 없는 이유: 서버가 봉 날짜를
 *  **그 주/달의 마지막 실제 거래일**로 실어 주기 때문이다(api/main.py `resample_candles`).
 *  즉 주봉 하나의 오른쪽 끝 = 그 주 마지막 거래일 = 그대로 기준일.
 *
 *  분봉·틱이 생기면 `INTRADAY_PERIODS` 에 주기를 넣기만 하면 시각까지 붙는다 —
 *  부르는 쪽(③ 시뮬레이션 등)은 안 고쳐도 된다. */
function baseStampOf(ts: number, period: BarPeriod): string {
  const d = new Date(ts)
  const day = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
  return INTRADAY_PERIODS.has(period) ? `${day}T${p2(d.getHours())}:${p2(d.getMinutes())}` : day
}

/** 화면 오른쪽 끝에 있는 봉 = 기준 시점. 차트를 옮길 때마다 부모에게 올려 보낸다. */
export type BaseBar = {
  /** 서버 `end` 에 그대로 넣는 값. 일·주·월은 'YYYY-MM-DD'. */
  time: string
  timestamp: number
  period: BarPeriod
}

/** 봉 하나가 걸치는 달력 날짜 수(대략). 거래일은 1년에 250일쯤이라 일봉 1개 ≈ 1.5일.
 *  분봉은 하루 약 400분(NXT 08:00~20:00 포함)을 주 5일 → 달력 하루 ≈ 285분. */
const DAYS_PER_BAR: Record<BarPeriod, number> = {
  min1: 1 / 285,
  min3: 3 / 285,
  min5: 5 / 285,
  min10: 10 / 285,
  min15: 15 / 285,
  min30: 30 / 285,
  min60: 60 / 285,
  min120: 120 / 285,
  min240: 240 / 285,
  day: 1.5,
  week: 8,
  month: 33,
}

/** 지표 계산에 필요한 여유 봉. MA60·MA20 등이 화면 첫 봉부터 그려지려면 앞이 더 있어야 한다. */
const WARMUP_BARS = 250

/** 받아올 시작일. `bars === 0`(전체)이면 null — 서버에 전체 이력을 달라고 한다.
 *
 *  **`start` 를 빼면 서버가 올해치만 준다**(실측 2026-08-07: 144봉). Pro 데이터피드가
 *  화면 구간에서 계산해 넘기던 값인데, Pro 를 벗으면서 같이 사라져 500봉이 안 나왔다.
 *
 *  필요한 만큼만 받는다. 넉넉히 받아두면 버튼 전환이 빠르지만, 주봉 1000개는 20년치라
 *  첫 로딩이 몇 초씩 걸린다(실측: 전체 이력 삼성전자 980KB · 6.7초).
 *
 *  `anchorMs` 는 "이 시점까지 화면에 담으려면" 의 기준. 기본은 지금 — 과거 기준일로
 *  거슬러 갈 때만 그 날짜를 넣어 앞쪽을 더 받는다. */
function startFor(period: BarPeriod, bars: BarCount, anchorMs = Date.now()): string | null {
  if (bars === 0) return null
  const need = (bars + WARMUP_BARS) * DAYS_PER_BAR[period]
  return new Date(anchorMs - need * 86_400_000).toISOString().slice(0, 10)
}

async function fetchCandles(
  code: string,
  period: BarPeriod,
  bars: BarCount,
  minStart?: string,
  market: BarMarket = 'unt',
  /** 이 날짜까지만. 왼쪽으로 밀어 **더 옛날 봉을 이어 받을 때** 쓴다(그 앞 구간만 받는다). */
  end?: string,
): Promise<{ bars: KLineData[]; source: string }> {
  // `end` 가 있으면 그 시점을 기준으로 거슬러 센다 — 지금부터 세면 엉뚱한 구간을 받는다.
  const auto = startFor(period, bars, end ? Date.parse(`${end}T00:00:00`) : undefined)
  // 전체(bars===0)면 이미 다 받으므로 minStart 는 볼 필요 없다.
  const start = auto == null ? '1990-01-01' : minStart && minStart < auto ? minStart : auto
  const q =
    `code=${encodeURIComponent(code)}&period=${period}&start=${start}&market=${market}` +
    (end ? `&end=${end}` : '')
  const res = await fetch(`/api/candles?${q}`)
  if (!res.ok) return { bars: [], source: 'none' } // 404 등 — 화면은 "데이터 없음"으로 남는다
  const { candles, source } = (await res.json()) as {
    source: string
    candles: {
      time: string
      open: number
      high: number
      low: number
      close: number
      volume: number
      amount: number
      marcap: number | null
    }[]
  }
  const rows = candles.map((c) => ({
    // 분봉은 서버가 'YYYY-MM-DDTHH:MM' 으로 준다 — 'T' 가 있으면 시각까지 그대로.
    timestamp: new Date(c.time.includes('T') ? c.time : `${c.time}T00:00:00`).getTime(),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
    turnover: c.amount,
    marcap: c.marcap,
  }))
  return { bars: rows, source: source ?? 'none' }
}

// 이동평균 기간과 색 — 증권사 앱(나무) 범례와 같게 맞춘다 (오너 2026-08-22).
// 5 녹색 · 20 빨강 · 60 파랑 · 120 주황 · 200 보라.
export const MA_PERIODS = [5, 20, 60, 120, 200] as const
const MA_COLORS = ['#22a06b', '#e03131', '#1971c2', '#f08c00', '#7048e8'] as const

type PickedCandle = {
  bar: KLineData
  previous?: KLineData
  averages: Partial<Record<(typeof MA_PERIODS)[number], number>>
}

type CandleClick = { dataIndex?: number; data?: KLineData }

function signedPct(value: number | undefined, base: number | undefined): string {
  if (value == null || base == null || base === 0) return ''
  const pct = ((value / base) - 1) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
}

function compactWon(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '받아온 게 없습니다'
  const eok = value / 100_000_000
  if (eok >= 10_000) return `${(eok / 10_000).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}조원`
  return `${eok.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}억원`
}

function compactCount(value: number | undefined): string {
  return value == null ? '—' : Math.round(value).toLocaleString('ko-KR')
}

const KOREAN_STYLES = {
  candle: {
    bar: {
      upColor: RED,
      downColor: BLUE,
      upBorderColor: RED,
      downBorderColor: BLUE,
      upWickColor: RED,
      downWickColor: BLUE,
    },
  },
  // 거래량 막대도 캔들과 같은 색이어야 한다 — 엔진 기본은 초록/빨강이라
  // 상승이 초록으로 나와 캔들과 색이 어긋난다(Pro 는 자기 테마로 덮어주고 있었다).
  indicator: {
    bars: [{ upColor: RED, downColor: BLUE, noChangeColor: '#9aa4b2' }],
    // 이평선 색 — 증권사 앱(나무)과 같게 (오너 2026-08-22: "이평선 색상도").
    // 5 녹색 · 20 빨강 · 60 파랑 · 120 주황 · 200 보라. 순서가 곧 MA_PERIODS 순서다.
    lines: MA_COLORS.map((color) => ({ color })),
  },
}

/** 화면에 보여줄 봉 개수 선택지. 0 = 전체(가진 데이터 전부 화면에 맞춤). */
export const BAR_COUNTS = [200, 300, 500, 1000, 0] as const
export type BarCount = (typeof BAR_COUNTS)[number]

/** 화면에 그릴 수 있는 그리기 도구 — 엔진 내장 이름과 한국어 라벨.
 *  Pro 툴바가 사라지면서 우리가 다는 것이고, 기능 자체는 엔진에 원래 있다. */
export const DRAW_TOOLS: readonly (readonly [string, string])[] = [
  ['fibonacciLine', '피보나치'],
  ['segment', '추세선'],
  ['horizontalStraightLine', '수평선'],
  ['verticalStraightLine', '수직선'],
  ['priceChannelLine', '가격채널'],
  ['rect', '사각형'],
] as const

/** 보조지표(아래 창) — 엔진 내장 + 우리가 등록한 거래대금. */
export const SUB_INDICATORS: readonly (readonly [string, string])[] = [
  ['VOL', '거래량'],
  ['TURNOVER', '거래대금'],
  ['MACD', 'MACD'],
  ['RSI', 'RSI'],
] as const

// 전략 적용 payload — bus.StrategyPick 과 같은 형태(구조적 타이핑).
// ProChart 는 hts 셸에 의존하지 않도록 자체 선언한다. 파라미터 값은 항상 여기 담겨
// 서버 요청으로만 나간다(ADR-0009 — 프런트·서버 어디에도 전략 숫자 하드코딩 없음).
export type StrategyPayload = {
  key: string
  params: Record<string, number | string>
  signals: boolean // POST /api/signals → ▲▼ 마커
  overlay: boolean // POST /api/overlay → 수평선 오버레이
}

/** 시뮬레이션 그리기 입력 — 계산은 전부 파이썬(POST /api/simulate)이 한다. */
export type SimulationDraw = {
  lines: OverlayLine[] // 분할 매수/매도 목표가 + 앵커
  fills: OverlayFill[] // 체결됐을 지점 (▲▼ + 차수)
  series: OverlaySeries[] // 앵커 VWAP 등 곡선
}

export type ProChartHandle = {
  /** 조건검색 결과 클릭 → 차트 종목 전환 (적용중 전략은 새 종목으로 재조회) */
  showSymbol: (code: string, name: string, market: string) => void
  /** 전략 적용(null = 해제). 신호·오버레이 계산은 전부 파이썬 — 시각 전용, 매매 판단 아님. */
  applyStrategy: (payload: StrategyPayload | null) => Promise<void>
  /** 시뮬레이션 결과 그리기(null = 해제). 전략 오버레이와 달리 차트가 스스로 조회하지
   *  않는다 — 파라미터를 쥔 화면(③ 시뮬레이션)이 계산해서 넘긴다. */
  applySimulation: (sim: SimulationDraw | null) => void
  /** 요소별 표시 필터 — 데이터 재적재 없이 다시 그리기만 한다(줌 유지). */
  setOverlayVisibility: (v: OverlayVisibility) => void
  /** 화면에 보이는 봉 개수를 맞춘다. 0 = 전체. (오너 요청 2026-08-07: "500봉만 보고 싶다") */
  setVisibleBars: (n: BarCount) => void
  /** 이 날짜가 화면 왼쪽 끝에 오도록 맞춘다 — 파동 시작점이 화면 밖일 때 쓴다. */
  showFrom: (date: string) => void
  /** 두 날짜 사이만 화면에 담는다 — 오른쪽 끝이 `to`. 백테스트 표에서 매매 하나를
   *  눌렀을 때 그 매매 구간으로 좁혀 보여주는 데 쓴다(오너 2026-08-18). */
  showSpan: (from: string, to: string) => Promise<void>
  /** 이 날짜가 화면 **오른쪽 끝**에 오도록 옮긴다 — 기준일을 손으로 고쳤을 때 차트를 맞춘다.
   *  받아둔 데이터보다 과거면 앞쪽을 더 받아온 뒤 옮긴다. */
  showUntil: (date: string) => Promise<void>
}

type ProChartProps = {
  /** 전략 오버레이 조회 실패(400/404 한국어 메시지)를 화면에 알릴 곳. 안 주면 콘솔만.
   *  실패가 조용하면 "차트에 아무것도 안 뜨는데 왜?"가 된다 (오너 지적 2026-08-06). */
  onOverlayError?: (msg: string | null) => void
  /** 최초 로드 종목. 마운트 직후 showSymbol 을 부르면 초기 로드와 경합해 늦게 온 이전
   *  종목 응답이 새 종목을 덮는다(실측 2026-08-06: 제목은 SK하이닉스, 캔들은 삼성전자 —
   *  y축이 어긋나 오버레이가 전부 축 밖으로 사라짐). 초기 종목은 여기로 넘긴다. */
  initialSymbol?: { code: string; name: string; market: string }
  /** 위쪽 도구 막대를 숨긴다 — ③ 시뮬 화면처럼 조작을 왼쪽 사이드가 다 가진 경우. */
  hideToolbar?: boolean
  /** 차트 왼쪽 위에 겹치기 켜고끄기 버튼을 띄운다. 사이드 패널이 없는 화면에서
   *  선이 겹쳐 캔들이 안 보일 때 끌 방법이 필요하다 (오너 2026-08-10). */
  layerToggles?: boolean
  /** 처음 보여줄 봉 개수. 기본 500 (오너가 차트를 보는 단위). */
  initialBars?: BarCount
  /** 화면 오른쪽 끝 봉이 바뀔 때마다 알려준다 — 스크롤·확대·주기 전환·종목 전환 전부.
   *  ③ 시뮬레이션이 이걸 기준일로 쓴다 (오너 요청 2026-08-08). */
  onBaseBar?: (base: BaseBar) => void
}

/** 화면에 정확히 `want` 개의 봉이 보이도록 봉 간격을 맞춘다.
 *
 *  엔진에 "몇 봉 보여줘"가 없고 **봉 하나의 폭**을 정하는 `setBarSpace` 만 있다.
 *  폭 = 그리는 영역 ÷ 봉 개수인데, 그리는 영역은 y축 너비를 뺀 값이라 밖에서 정확히 모른다.
 *  그래서 한 번 넣어 보고 실제로 몇 개가 보이는지 물어(`getVisibleRange`) 비율만큼 고친다.
 *  세 번이면 오차가 1봉 밑으로 떨어진다. 반복 상한을 둬 절대 안 도는 일이 없게 한다.
 */
function fitBars(chart: Chart, el: HTMLElement, want: number): void {
  const total = chart.getDataList().length
  if (total === 0) return
  const n = want > 0 ? Math.min(want, total) : total
  // 오른쪽 여백을 **재기 전에** 없앤다. 여백이 남아 있으면 화면 자리 일부가 빈 공간이라
  // "보이는 봉"이 실제보다 적게 세어지고, 그만큼 봉을 넓혀서 결국 더 많이 보이게 된다
  // (실측 2026-08-07: 500봉을 넣었는데 640봉이 보였다).
  chart.setOffsetRightDistance(0)
  let space = Math.max(0.5, el.clientWidth / n)
  for (let i = 0; i < 4; i++) {
    chart.setBarSpace(space)
    const r = chart.getVisibleRange()
    const shown = r.to - r.from
    if (shown <= 0 || Math.abs(shown - n) <= 1) break
    space = Math.max(0.5, (space * shown) / n)
  }
  // **오른쪽 끝으로 되돌린다.** 봉 폭만 바꾸고 말면, 앞서 스크롤해 둔 자리(showUntil·
  // 손스크롤)가 그대로 남아 창이 첫 봉보다 왼쪽까지 뻗는다 — 그 자리가 빈 칸으로 보인다
  // (오너 지적 2026-08-18: "일반 차트로 볼 때 왜 이전 캔들 안 보이냐 왜 또 짤라먹었어").
  // 구간을 따로 맞추는 쪽(showUntil·showSpan)은 이 뒤에 다시 스크롤하므로 영향이 없다.
  chart.scrollToDataIndex(total - 1)
}

/** `showUntil` 의 스크롤 핵심만 떼어낸 것 — 리사이즈로 다시 맞출 때도 같은 계산을 쓴다. */
function scrollToDate(chart: Chart, date: string): void {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return
  const ts = dayTs(date)
  const list = chart.getDataList()
  let i = -1
  for (let k = list.length - 1; k >= 0; k--) {
    if (list[k].timestamp <= ts) {
      i = k
      break
    }
  }
  if (i < 0) return
  chart.scrollToDataIndex(i)
  chart.scrollByDistance(chart.getBarSpace() * 0.01)
}

export const ProChart = forwardRef<ProChartHandle, ProChartProps>(function ProChart(props, ref) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Chart | null>(null)
  // 이 차트 전용 신호·오버레이 저장소·지표 (인스턴스당 1회 생성)
  const signalsRef = useRef<SignalStore | null>(null)
  if (!signalsRef.current) signalsRef.current = createSignalIndicator()
  const overlayRef = useRef<OverlayStore | null>(null)
  if (!overlayRef.current) overlayRef.current = createOverlayIndicator()
  // 현재 적용중 전략 payload — 종목 전환 시 이 값으로 재조회한다.
  const strategyRef = useRef<StrategyPayload | null>(null)
  const first = props.initialSymbol ?? { code: '005930', name: '삼성전자', market: 'KOSPI' }
  const symbolRef = useRef({ ...first })
  const periodRef = useRef<BarPeriod>('day')
  const marketRef = useRef<BarMarket>('unt')
  const barsRef = useRef<BarCount>(props.initialBars ?? 500)
  // 늦게 온 이전 종목 응답이 새 종목을 덮지 않게 하는 순번 (Pro 데이터피드가 하던 일).
  const loadSeq = useRef(0)
  // 이 종목의 과거 이력이 실제로 들어왔는가 — reload() 가 끝나야 true. 장중 실시간 폴링이
  // 이보다 먼저 응답하면 빈 차트에 오늘 봉 하나만 올라가고, 그 한 봉으로 emitBase 가
  // 발화해 행 차트(RowChart)의 1회성 그리기 잠금이 소진돼 버린다 — 진짜 이력이 뒤늦게
  // 들어와도 다시 안 그려져 "엉뚱한 화면"으로 굳는다(오너 지적 2026-08-21).
  const historyLoadedRef = useRef(false)
  // showUntil 이 마지막으로 맞춘 날짜 — 리사이즈(모달 레이아웃 확정 등)로 `fitBars` 가
  // 다시 불려도 이 자리로 되돌린다. `fitBars` 는 항상 맨 오른쪽(최신 봉)으로 스크롤하므로,
  // 안 그러면 showSpan 으로 맞춰 둔 매매 구간이 리사이즈 한 번에 최신 화면으로 튄다
  // (오너 지적 2026-08-21 — 차트로 보기가 자꾸 엉뚱한 화면으로 이동).
  const pinDateRef = useRef<string | null>(null)
  // 지금 받아둔 데이터가 감당하는 봉 수. 0 = 전체 이력. 이보다 많이 보려 하면 다시 받는다.
  // 앞쪽(과거)으로 넓혀 받은 시작일. 기준일을 과거로 옮겼을 때만 채워진다.
  const minStartRef = useRef<string | undefined>(undefined)
  // 부모 콜백을 ref 로 들고 있는다 — 구독은 마운트 때 한 번만 걸고(deps []), 그 클로저가
  // 첫 렌더의 props 를 붙잡고 있으면 나중에 바뀐 콜백이 안 불린다.
  const onBaseRef = useRef(props.onBaseBar)
  onBaseRef.current = props.onBaseBar
  // **지금** 화면 오른쪽 끝에 있는 봉. 도구(지지저항·오더블록·빈틈)가 이걸 기준일로 써서
  // "화면에 보이는 구간"만 계산한다 — 부모 콜백이 있든 없든 항상 갱신한다.
  const lastBaseRef = useRef<number | null>(null)
  // 부모에게 **마지막으로 올려 보낸** 봉. 같은 봉이면 다시 안 보낸다 — 이게 없으면
  // 스크롤 한 번에 프레임마다 부모가 다시 그려지고, 되돌려 맞추기(showUntil)와
  // 서로를 부르는 고리가 생긴다.
  const lastSentRef = useRef<number | null>(null)

  const [sym, setSym] = useState({ ...first })
  // 장중 실시간 오늘 봉의 밑봉(주봉·월봉은 어제까지 합성된 진행 봉에 오늘을 합친다).
  const liveBaseRef = useRef<KLineData | null>(null)
  // 이 봉이 어디서 왔나 — 상장 종목은 나무 수집본, 상장폐지·미수집은 marcap 보정본.
  // 두 소스를 같이 쓰므로 화면이 그걸 말해 줘야 한다 (오너 2026-08-16).
  const [source, setSource] = useState('none')
  const [period, setPeriodState] = useState<BarPeriod>('day')
  const [market, setMarketState] = useState<BarMarket>('unt')
  const [bars, setBarsState] = useState<BarCount>(props.initialBars ?? 500)
  const [subs, setSubs] = useState<string[]>(['VOL'])
  // 지지저항·오더블록·가격 빈틈은 **차트 기능**이다 (오너 2026-08-09: "애초에 지지저항을
  // 기법으로 원한 게 아니라 차트 기능으로 생각한 건데"). 거래량처럼 켜고 끈다 —
  // 전략과 무관하고, 셋이 서로 다른 자리를 짚어서 **따로** 켜고 끈다.
  const [tools, setTools] = useState<Record<ToolLayer, boolean>>({
    지지저항: false,
    오더블록: false,
    '가격 빈틈': false,
  })
  const toolsRef = useRef(tools)
  // 그린 수평선의 가격을 항상 띄울지 — 기본은 꺼짐(예전 그대로). 실제 값은 모듈
  // 변수가 들고 있다(그리기 도구 등록이 전역 1회라서). 여기는 버튼 표시용이다.
  const [showLinePrice, setShowLinePrice] = useState(false)
  const [busy, setBusy] = useState(false)
  const [picked, setPicked] = useState<PickedCandle | null>(null)
  // 차트가 스스로 들고 있는 겹치기 상태 — `layerToggles` 를 켠 화면에서만 쓴다.
  // 바깥에서 setOverlayVisibility 로 덮어써도 되지만, 그건 사이드 패널이 있는 ③ 얘기다.
  const [layerVis, setLayerVis] = useState<OverlayVisibility>(allVisible)

  /** 화면을 다시 그린다. 데이터를 다시 안 받으므로 줌·스크롤이 유지된다.
   *  (Pro 를 쓸 땐 window resize 를 쏘는 우회가 필요했는데, 엔진은 resize() 를 직접 준다.) */
  function repaint(): void {
    chartRef.current?.resize()
  }

  /** 종목·주기가 바뀔 때 봉을 다시 받아 넣는다. */
  async function reload(): Promise<void> {
    const chart = chartRef.current
    const el = elRef.current
    if (!chart || !el) return
    const seq = ++loadSeq.current
    historyLoadedRef.current = false // 새로 받는 중 — 실시간 폴링을 멈춰 둔다
    pinDateRef.current = null // 데이터가 통째로 바뀐다 — 옛 고정 자리는 더 이상 안 맞는다
    setPicked(null)
    setBusy(true)
    try {
      const want = barsRef.current
      const { bars: data, source } = await fetchCandles(
        symbolRef.current.code,
        periodRef.current,
        want,
        minStartRef.current,
        marketRef.current,
      )
      if (seq !== loadSeq.current) return // 그 사이 종목·주기가 또 바뀌었다 — 이 응답은 버린다
      setSource(source)
      lastBaseRef.current = null // 데이터가 통째로 바뀌었다 — 오른쪽 끝을 다시 알린다
      lastSentRef.current = null
      // `more=true` — 왼쪽 끝에 닿으면 엔진이 더 달라고 부른다(setLoadDataCallback).
      chart.applyNewData(data, true)
      // 장중 실시간 봉을 얹을 밑봉 = 서버가 준 마지막 봉(오늘이 아직 안 들어간 것).
      const last = data.length ? data[data.length - 1] : null
      liveBaseRef.current = last && last.timestamp < todayStart() ? last : null
      fitBars(chart, el, barsRef.current)
      historyLoadedRef.current = true // 이제부터 실시간 폴링이 이 데이터 위에 얹어도 안전하다
    } finally {
      if (seq === loadSeq.current) setBusy(false)
    }
  }

  /** 화면 오른쪽 끝 봉을 부모에게 알린다(기준일).
   *
   *  `getVisibleRange().to` 는 **끝 다음** 자리라 -1 이 오른쪽 끝 봉이다. 오른쪽 여백까지
   *  스크롤하면 `to` 가 데이터 개수를 넘길 수 있어 한 번 더 잘라 준다.
   *  드래그 중엔 프레임마다 불려서, 한 프레임에 하나로 합치고 같은 봉이면 건너뛴다. */
  const basePending = useRef(0)
  // 마지막으로 도구를 그린 상태 = `오른쪽 끝 봉 : 보이는 봉 수`. 둘 중 하나라도 바뀌면
  // 다시 계산한다. 끝 봉만 봤을 땐 **줌만 바꿔도 안 바뀌어** 보이는 구간과 어긋났다.
  const lastToolKey = useRef('')
  const toolTimer = useRef(0)

  /** 스크롤·줌이 멎은 뒤 한 번만 다시 계산한다. 드래그 중 매 프레임 서버를 때리면
   *  200봉 기준 16ms 짜리 계산이라도 요청이 쌓여 화면이 밀린다. */
  function scheduleTools(key: string): void {
    if (key === lastToolKey.current) return
    lastToolKey.current = key
    window.clearTimeout(toolTimer.current)
    toolTimer.current = window.setTimeout(refreshTools, 250)
  }

  function emitBase(): void {
    if (basePending.current) return
    basePending.current = requestAnimationFrame(() => {
      basePending.current = 0
      const chart = chartRef.current
      if (!chart) return
      const list = chart.getDataList()
      const r = chart.getVisibleRange()
      // `to` 는 **끝 다음** 자리이고, 캔들 없는 오른쪽 여백까지 밀면 데이터 개수를
      // 넘는다 — 잘라서 **마지막 실제 봉**을 쓴다. 그래서 여백으로 아무리 밀어도
      // 기준일은 마지막 봉에서 멈춘다 (오너 2026-08-09 확인 요청).
      const bar = list[Math.min(r.to, list.length) - 1]
      // 오른쪽 끝 봉과 도구 갱신은 부모 콜백과 **무관하게** 한다 — 차트 탭엔 onBaseBar 가
      // 없어서, 아래 조기 반환에 걸리면 기준일이 영영 비고 과거로 밀어도 서버가 최신
      // 거래일까지 봤다(오너 지적 2026-08-09: "딱 줌한 부분만 뜨도록").
      if (bar) lastBaseRef.current = bar.timestamp
      scheduleTools(`${bar?.timestamp ?? 0}:${r.to - r.from}`)
      const cb = onBaseRef.current
      if (!cb || !bar || bar.timestamp === lastSentRef.current) return
      lastSentRef.current = bar.timestamp
      const period = periodRef.current
      cb({ time: baseStampOf(bar.timestamp, period), timestamp: bar.timestamp, period })
    })
  }

  // 전략 데이터 재조회 — 적용/해제/종목 전환 공용.
  // 응답 대기 중 종목·전략이 바뀌었으면 버린다(늦게 온 이전 응답이 새 차트에 그려지는 경합 방지).
  async function refreshStrategy(): Promise<void> {
    const payload = strategyRef.current
    const code = symbolRef.current.code
    const sig = signalsRef.current!
    const ov = overlayRef.current!
    sig.set([])
    ov.clear()
    props.onOverlayError?.(null)
    if (payload) {
      const stale = () => symbolRef.current.code !== code || strategyRef.current !== payload
      try {
        if (payload.signals) {
          const res = await postSignals({ code, strategy: payload.key, params: payload.params })
          if (stale()) return
          sig.set(res.signals)
        }
        if (payload.overlay) {
          const res = await postOverlay({ code, strategy: payload.key, params: payload.params })
          if (stale()) return
          ov.set(res.lines, res.touches)
        }
      } catch (e) {
        // 400(파라미터·구간 오류)·404(종목 없음) — 이 종목엔 그릴 게 없다. 화면에 알린다.
        if (stale()) return
        const msg = e instanceof Error ? e.message : '전략 조회 실패'
        console.warn('[전략 조회 실패]', msg)
        props.onOverlayError?.(msg)
      }
    }
    repaint()
  }

  /** 봉 개수 전환. 받아둔 데이터로 충분하면 화면만 맞추고(즉시), 모자라면 다시 받는다. */
  function applyBars(n: BarCount): void {
    barsRef.current = n
    setBarsState(n)
    pinDateRef.current = null // 봉 수를 직접 바꾸면 고정 자리를 버리고 새로 맞춘다(최신 봉 기준)
    // 봉 수는 **화면 배율**일 뿐이다 — 받아올 범위가 아니다. 모자라면 왼쪽으로 밀 때
    // 엔진이 알아서 더 받아온다(setLoadDataCallback). 예전엔 여기서 다시 받아오느라
    // 봉 수가 곧 데이터 범위가 됐고, 그 앞은 밀어도 안 나왔다(오너 지적 2026-08-18).
    const chart = chartRef.current
    const el = elRef.current
    if (chart && el) fitBars(chart, el, n)
    refreshTools()
  }

  useImperativeHandle(ref, () => ({
    showSymbol(code, name, market) {
      symbolRef.current = { code, name, market }
      setSym({ code, name, market })
      // 종목이 바뀌면 이전 종목의 신호·오버레이는 무효 — 즉시 지우고 다시 받는다.
      signalsRef.current?.set([])
      overlayRef.current?.clear()
      void reload()
      if (strategyRef.current) void refreshStrategy()
      refreshTools()
    },
    async applyStrategy(payload) {
      strategyRef.current = payload
      await refreshStrategy()
    },
    applySimulation(sim) {
      const ov = overlayRef.current!
      if (sim) {
        ov.set(sim.lines, [])
        ov.setSim(sim.fills, sim.series)
      } else {
        ov.clear()
      }
      repaint()
    },
    setOverlayVisibility(v) {
      overlayRef.current!.setVisibility(v)
      repaint()
    },
    setVisibleBars(n) {
      applyBars(n)
    },
    showFrom(date) {
      const chart = chartRef.current
      const el = elRef.current
      if (!chart || !el) return
      const ts = new Date(`${date}T00:00:00`).getTime()
      const list = chart.getDataList()
      const i = list.findIndex((d) => d.timestamp >= ts)
      if (i < 0) return
      // 그 날짜부터 마지막 봉까지가 화면에 들어오게 — 양옆 여유 5%.
      fitBars(chart, el, Math.ceil((list.length - i) * 1.05))
    },
    async showSpan(from, to) {
      // 먼저 `from` 까지 실제로 받아 온다 — 안 받아왔으면 셀 봉이 없다.
      await this.showUntil(from)
      const chart = chartRef.current
      const el = elRef.current
      if (!chart || !el) return
      const list = chart.getDataList()
      const a = dayTs(from)
      const b = dayTs(to)
      let n = 0
      for (const d of list) if (d.timestamp >= a && d.timestamp <= b) n++
      if (n <= 0) return
      // 양옆 여유 — 매매 앞뒤 흐름이 보여야 판단이 된다. 하루짜리 매매도 최소 60봉.
      // 받아온 봉 수로 자르는 일은 `fitBars` 가 한다 — 여기서 또 하지 않는다(정본 하나).
      fitBars(chart, el, Math.max(60, Math.ceil(n * 1.4) + 20))
      await this.showUntil(to)
    },
    async showUntil(date) {
      const chart = chartRef.current
      if (!chart || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return
      const ts = dayTs(date)
      // 아직 안 받아온 과거면 앞쪽을 넓혀 다시 받는다. minStartRef 에 남겨 둬야
      // 이후 종목·주기를 바꿔도 그 구간이 계속 잡힌다.
      if ((chart.getDataList()[0]?.timestamp ?? 0) > ts) {
        minStartRef.current = startFor(periodRef.current, barsRef.current, ts) ?? undefined
        await reload()
      }
      // 그 날짜 **이하** 중 가장 마지막 봉 = 그 시점의 오른쪽 끝. 휴장일을 넣으면
      // 자동으로 직전 거래일이 된다(서버 기준일 규칙과 같다). 반 봉의 1/50 만큼 더 미는 건
      // klinecharts `adjustVisibleRange`(round(위치+0.5))가 정수 위치에서만 한 칸 더
      // 세기 때문 — 없으면 기준일이 하루 뒤로 밀린다.
      scrollToDate(chart, date)
      pinDateRef.current = date // 리사이즈로 다시 맞출 때 이 자리로 되돌아온다
    },
  }))

  useEffect(() => {
    const el = elRef.current
    if (!el) return
    registerKoreanLocale()
    ensureIndicators()

    const chart = init(el, { locale: 'ko-KR', styles: KOREAN_STYLES })
    if (!chart) return
    chartRef.current = chart
    // 메인 창: 이평선 + 이 차트 전용 전략 신호 마커·오버레이 수평선(데이터 없으면 안 그림)
    chart.createIndicator(
      { name: 'MA', calcParams: [...MA_PERIODS] },
      true,
      { id: 'candle_pane' },
    )
    chart.createIndicator(signalsRef.current!.indicatorName, true, { id: 'candle_pane' })
    chart.createIndicator(overlayRef.current!.indicatorName, true, { id: 'candle_pane' })
    chart.createIndicator('VOL', false, { id: 'pane_VOL' })
    const pickCandle = (clicked?: CandleClick) => {
      const list = chart.getDataList()
      const index = clicked?.dataIndex
      if (index == null || index < 0 || index >= list.length) return
      const bar = clicked?.data ?? list[index]
      const averages: PickedCandle['averages'] = {}
      for (const days of MA_PERIODS) {
        const from = index - days + 1
        if (from < 0) continue
        let total = 0
        for (let i = from; i <= index; i += 1) total += list[i].close
        averages[days] = total / days
      }
      setPicked({ bar, previous: list[index - 1], averages })
    }
    // 화면 오른쪽 끝이 바뀔 때마다(스크롤·확대·데이터 교체) 기준일을 올려 보낸다.
    // 줌은 `OnVisibleRangeChange` 를 안 쏜다(실측 2026-08-09: 휠로 확대해도 요청이 안
    // 나갔다) — `OnZoom` 을 따로 구독해야 "딱 줌한 부분만" 다시 계산된다.
    chart.subscribeAction(ActionType.OnVisibleRangeChange, emitBase)
    chart.subscribeAction(ActionType.OnZoom, emitBase)
    chart.subscribeAction(ActionType.OnCandleBarClick, pickCandle)
    // 왼쪽 끝까지 밀면 **그 앞 구간을 이어 받는다** — 증권사 차트와 같은 동작이고,
    // 엔진에 이미 있는 기능이다. 예전엔 봉 수(=화면 배율)가 받아올 범위까지 정해서,
    // 500봉을 고르면 그 앞이 아예 없어 아무리 밀어도 안 나왔다
    // (오너 지적 2026-08-18: "500봉으로 캔들 보이는 게 조절되게 하라고 했지
    //  언제 500개로 캔들 짤라먹으라고 했냐").
    chart.setLoadDataCallback(({ type, data, callback }) => {
      if (type !== LoadDataType.Forward || !data) {
        callback([], type !== LoadDataType.Backward)
        return
      }
      const oldest = new Date(data.timestamp - 86_400_000).toISOString().slice(0, 10)
      void fetchCandles(
        symbolRef.current.code,
        periodRef.current,
        barsRef.current || 500,
        undefined,
        marketRef.current,
        oldest,
      ).then(({ bars }) => {
        // 받은 게 있으면 아직 더 있을 수 있다 — 빈 응답이 오면 거기가 상장일이다.
        callback(bars, bars.length > 0)
      })
    })
    void reload()

    // 패널(dockview) 크기가 바뀌면 차트에 알려준다. 엔진은 resize() 를 직접 준다 —
    // Pro 시절엔 window resize 이벤트를 쏘는 우회가 필요했다(Pro 가 그것만 들어서).
    let pending = 0
    const ro = new ResizeObserver(() => {
      if (pending) return // 드래그 중 연속 호출은 한 프레임에 하나로 합친다
      pending = requestAnimationFrame(() => {
        pending = 0
        // 탭 전환으로 패널이 숨겨지면 크기가 0 이 된다 — 이때 맞추면 0px 기준으로 봉 수를
        // 다시 잡아 돌아올 때마다 확대돼 보인다.
        const r = el.getBoundingClientRect()
        if (r.width < 2 || r.height < 2) return
        chart.resize()
        fitBars(chart, el, barsRef.current)
        // fitBars 는 항상 맨 오른쪽(최신 봉)으로 되돌린다 — showSpan/showUntil 로 맞춰 둔
        // 자리(예: 행 차트의 매매 구간)가 있으면 리사이즈 한 번에 날아간다. 되돌린다.
        if (pinDateRef.current) scrollToDate(chart, pinDateRef.current)
      })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      if (pending) cancelAnimationFrame(pending)
      if (basePending.current) cancelAnimationFrame(basePending.current)
      window.clearTimeout(toolTimer.current)
      chart.unsubscribeAction(ActionType.OnVisibleRangeChange, emitBase)
      chart.unsubscribeAction(ActionType.OnZoom, emitBase)
      chart.unsubscribeAction(ActionType.OnCandleBarClick, pickCandle)
      disposeKLineChart(el)
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 장중 실시간 — **이 차트가 보고 있는 종목만** 2초마다 오늘 봉을 받아 오른쪽 끝에 얹는다
  // (오너 결정 2026-08-18: 장중엔 전 종목 갱신 ❌, 차트 연 종목만 실시간). 서버가 나무
  // 웹소켓 구독을 들고 있고, 이 폴링이 멈추면(차트 닫힘) 30초 뒤 구독도 풀린다.
  // 분봉은 나무 수집본 그대로라 여기서 건드리지 않는다.
  useEffect(() => {
    if (period !== 'day' && period !== 'week' && period !== 'month') return
    let stopped = false
    let timer = 0
    let lastBarKey = '' // 같은 값이면 다시 안 그린다 — 2초마다 그리면 화면이 밀린다
    const q = `code=${encodeURIComponent(sym.code)}&market=${market}`
    const schedule = (ms: number): void => {
      if (stopped) return
      window.clearTimeout(timer)
      timer = window.setTimeout(() => void tick(), ms)
    }
    const tick = async (): Promise<void> => {
      if (stopped) return
      const chart = chartRef.current
      // 탭이 뒤로 갔거나 차트가 화면 밖이면 부르지 않는다 — 구독은 서버 TTL 이 풀어 준다
      const el = elRef.current
      const onScreen = !!el && el.getClientRects().length > 0
      if (document.hidden || !chart || !onScreen) return schedule(LIVE_POLL_IDLE_MS)
      // 과거 이력이 아직 안 들어왔으면 건너뛴다 — reload() 와의 경합 방지(위 historyLoadedRef 참고).
      if (!historyLoadedRef.current) return schedule(LIVE_POLL_IDLE_MS)
      let res: LiveBarResponse
      try {
        const r = await fetch(`/api/live/bar?${q}`)
        if (!r.ok) return schedule(LIVE_POLL_IDLE_MS)
        res = (await r.json()) as LiveBarResponse
      } catch {
        return schedule(LIVE_POLL_IDLE_MS) // 서버가 잠깐 없어도 차트는 그대로 둔다
      }
      if (stopped) return
      if (!res.market_open) return schedule(LIVE_POLL_CLOSED_MS) // 장 밖 — 드물게만 확인
      if (shouldApply(res) && res.bar) {
        const key = `${res.bar.time}:${res.bar.close}:${res.bar.volume}`
        if (key !== lastBarKey) {
          lastBarKey = key
          chart.updateData(mergeLive(period, liveBaseRef.current, res.bar))
        }
      }
      schedule(LIVE_POLL_MS)
    }
    void tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
      // 차트를 닫았거나 종목·시장을 바꿨다 — 서버 구독을 바로 푼다(TTL 을 기다리지 않는다)
      void fetch(`/api/live/bar?${q}`, { method: 'DELETE', keepalive: true }).catch(() => undefined)
    }
  }, [sym.code, period, market])

  function pickMarket(m: BarMarket): void {
    marketRef.current = m
    setMarketState(m)
    // 시장이 바뀌면 봉 값이 통째로 바뀐다(통합 거래량 ≠ KRX 거래량) — 주기 전환과 같은 절차.
    lastToolKey.current = ''
    void reload().then(refreshTools)
  }

  function pickPeriod(p: BarPeriod): void {
    periodRef.current = p
    setPeriodState(p)
    // 봉을 다시 받은 **뒤에** 도구를 다시 계산한다. 전엔 이 줄이 없어서 일 → 주 → 월 로
    // 바꿔도 지지저항·오더블록이 이전 주기 값 그대로 남았다 (오너 2026-08-09:
    // "지지저항 고장났네. 동작 안하네"). 봉이 통째로 바뀌면 구간도 바뀐다.
    lastToolKey.current = ''
    void reload().then(refreshTools)
  }

  /** 도구 한 겹을 다시 받아 그린다. 끄면 지운다.
   *  보는 구간 = 화면 **왼쪽 끝 봉 ~ 오른쪽 끝 봉** (오너 2026-08-09: "화면에 봉 갯수
   *  설정하는 거 있잖아 딱 그정도 까지만"). 봉 개수가 아니라 날짜로 보낸다 — 주봉·월봉
   *  에서 개수를 보내면 서버(일봉 계산)와 단위가 달라 구간이 어긋난다. */
  async function refreshTool(layer: ToolLayer, on: boolean): Promise<void> {
    const ov = overlayRef.current!
    if (!on) {
      ov.setTool(layer, [])
      repaint()
      return
    }
    const code = symbolRef.current.code
    const period = periodRef.current
    const end = lastBaseRef.current ? baseStampOf(lastBaseRef.current, period) : undefined
    const chart = chartRef.current
    const r = chart?.getVisibleRange()
    // 왼쪽 끝 봉의 **날짜**를 보낸다. 계산은 언제나 일봉이라, 주봉·월봉에서 봉 개수를
    // 그대로 보내면 구간이 통째로 어긋난다 — 2010~2026 이 보이는 월봉 화면에 최근 200일
    // 자리가 그려졌다(오너 지적 2026-08-09: "지지저항 고장났네").
    const left = r ? chart?.getDataList()[Math.max(0, r.from)] : undefined
    const win = {
      bars: r ? r.to - r.from : (barsRef.current || 300),
      start: left ? baseStampOf(left.timestamp, period) : undefined,
      end,
    }
    try {
      const res =
        layer === '지지저항'
          ? await fetchSupportResistance(code, win)
          : await fetchPriceZones(code, layer, win)
      if (symbolRef.current.code !== code) return // 그 사이 종목이 바뀌었다
      ov.setTool(layer, res.lines)
      props.onOverlayError?.(null)
    } catch (e) {
      ov.setTool(layer, [])
      props.onOverlayError?.(e instanceof Error ? e.message : `${layer} 조회 실패`)
    }
    repaint()
  }

  /** 켜져 있는 도구를 전부 다시 그린다 — 종목·기간·줌이 바뀌면 다 새로 계산해야 한다. */
  function refreshTools(): void {
    for (const [layer, on] of Object.entries(toolsRef.current)) {
      if (on) void refreshTool(layer as ToolLayer, true)
    }
  }

  function toggleTool(layer: ToolLayer): void {
    // 켜짐 상태는 **ref 가 정본**이다. `tools`(그린 뒤에야 갱신되는 값)로 계산하면 버튼
    // 두 개를 연달아 누를 때 두 번째 클릭이 첫 번째를 되돌린다 — 실측 2026-08-09.
    const next = { ...toolsRef.current, [layer]: !toolsRef.current[layer] }
    toolsRef.current = next
    setTools(next)
    lastToolKey.current = '' // 다음 스크롤에서 다시 계산되게 (버튼은 기다리지 않고 즉시)
    void refreshTool(layer, next[layer])
  }

  function toggleSub(name: string): void {
    const chart = chartRef.current
    if (!chart) return
    const paneId = `pane_${name}`
    if (subs.includes(name)) {
      chart.removeIndicator(paneId)
      setSubs((s) => s.filter((x) => x !== name))
    } else {
      chart.createIndicator(name, false, { id: paneId })
      setSubs((s) => [...s, name])
    }
  }

  return (
    <div className="pro-chart">
      {!props.hideToolbar && (
        <div className="pro-bar">
          <b className="sym">
            {sym.name} <span className="code">{sym.code}</span>
            {source !== 'none' && (
              <span
                className={`src ${source}`}
                title={
                  source === 'namuh'
                    ? '나무증권 수집본 — 증권사가 보정한 수정주가입니다. 액면분할·병합이 이미 반영돼 있습니다.'
                    : 'marcap + 자체 보정 — 상장폐지되었거나 아직 수집되지 않은 종목입니다. 액면병합이 안 잡힐 수 있습니다.'
                }
              >
                {source === 'namuh' ? '나무' : 'marcap'}
              </span>
            )}
          </b>
          <span className="grp">
            <select
              className={MINUTE_PERIODS.includes(period) ? 'on' : ''}
              value={MINUTE_PERIODS.includes(period) ? period : ''}
              onChange={(e) => e.target.value && pickPeriod(e.target.value as BarPeriod)}
              title="분봉 — 나무증권 수집본. 1~15분은 최근 6주, 60분 이상은 몇 년치"
            >
              <option value="">분</option>
              {MINUTE_PERIODS.map((p) => (
                <option key={p} value={p}>
                  {PERIOD_LABEL[p]}
                </option>
              ))}
            </select>
            {CALENDAR_PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                className={period === p ? 'on' : ''}
                onClick={() => pickPeriod(p)}
              >
                {PERIOD_LABEL[p]}
              </button>
            ))}
          </span>
          <span className="grp" title="어느 거래소 체결 기준으로 볼지 — 통합 = KRX+넥스트레이드 전체">
            {(Object.keys(MARKET_LABEL) as BarMarket[]).map((m) => (
              <button
                key={m}
                type="button"
                className={market === m ? 'on' : ''}
                onClick={() => pickMarket(m)}
              >
                {MARKET_LABEL[m]}
              </button>
            ))}
          </span>
          <span className="grp" title="화면에 보이는 봉 개수">
            {BAR_COUNTS.map((n) => (
              <button
                key={n}
                type="button"
                className={bars === n ? 'on' : ''}
                onClick={() => applyBars(n)}
              >
                {n === 0 ? '전체' : `${n}봉`}
              </button>
            ))}
          </span>
          {/* 차트 기능 세 겹 — 서로 다른 자리를 짚어서 따로 켜고 끈다 (오너 2026-08-09).
              지지저항 = 여러 번 닿은 자리 / 오더블록 = 세게 민 봉 직전 반대색 봉 /
              가격 빈틈 = 세 봉이 안 겹쳐 생긴 구간 */}
          <span className="grp" title="전략과 무관한 차트 기능 — 따로 켜고 끈다">
            {TOOL_LAYERS.map(([layer, title]) => (
              <button
                key={layer}
                type="button"
                title={title}
                className={tools[layer] ? 'on' : ''}
                onClick={() => toggleTool(layer)}
              >
                {layer}
              </button>
            ))}
          </span>
          <span className="grp">
            {SUB_INDICATORS.map(([name, label]) => (
              <button
                key={name}
                type="button"
                className={subs.includes(name) ? 'on' : ''}
                onClick={() => toggleSub(name)}
              >
                {label}
              </button>
            ))}
          </span>
          <span className="grp">
            {DRAW_TOOLS.map(([name, label]) => (
              <button key={name} type="button" onClick={() => chartRef.current?.createOverlay(name)}>
                {label}
              </button>
            ))}
            {/* 그린 수평선의 가격을 항상 띄울지. 끄면 예전처럼 **누른 선만** 보인다
                (klinecharts 가 Y축 딱지를 고른 오버레이에만 그린다). 여러 개 그으면
                오른쪽 끝 되돌림·지지저항 글자와 겹쳐서, 켤지는 오너가 정한다. */}
            <button
              type="button"
              className={showLinePrice ? 'on' : ''}
              title="그린 수평선의 가격을 항상 보이게 (끄면 누른 선만)"
              onClick={() => {
                const next = !showLinePrice
                setShowLinePrice(next)
                setAlwaysShowLinePrice(next)
                repaint()
              }}
            >
              값 항상 보기
            </button>
            <button
              type="button"
              title="그린 것 전부 지우기"
              onClick={() => chartRef.current?.removeOverlay()}
            >
              지우기
            </button>
          </span>
          {/* 겹치기 칩 — 도구 막대가 있으면 **여기**에 둔다. 차트 위에 띄우면 캔들을
              가린다(오너 지적 2026-08-18). 막대를 숨긴 화면에서는 아래 떠 있는 쪽을 쓴다. */}
          {props.layerToggles && (
            <span className="grp" title="차트에 얹은 선을 하나씩 켜고 끈다">
              {OVERLAY_LAYERS.map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={layerVis[k] ? 'on' : ''}
                  onClick={() => {
                    const next = { ...layerVis, [k]: !layerVis[k] }
                    setLayerVis(next)
                    overlayRef.current?.setVisibility(next)
                    repaint()
                  }}
                >
                  {label}
                </button>
              ))}
            </span>
          )}
          {busy && <span className="dim">불러오는 중…</span>}
        </div>
      )}
      <div className="pro-canvas" ref={elRef}>
        {/* 겹치기 켜고 끄기 — **차트 왼쪽 위에** 띄운다 (오너 2026-08-10: "차트 왼쪽에
            각각 오버레이 키고 끌수 있는 버튼 추가해"). 선이 여러 겹 깔리면 캔들이 안
            보이는데, 사이드 패널이 없는 화면(백테스트 결과 차트)에서는 끌 방법이
            아예 없었다. 차트가 스스로 들고 있어야 어느 화면에 놓아도 따라온다. */}
        {props.layerToggles && props.hideToolbar && (
          <div className="pro-layers">
            {OVERLAY_LAYERS.map(([k, label]) => (
              <button
                key={k}
                type="button"
                className={layerVis[k] ? 'on' : ''}
                title={`${label} 켜기/끄기`}
                onClick={() => {
                  const next = { ...layerVis, [k]: !layerVis[k] }
                  setLayerVis(next)
                  overlayRef.current?.setVisibility(next)
                  repaint()
                }}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {picked && (
          <div className="candle-card" role="dialog" aria-label="고른 봉 상세 정보">
            <button
              type="button"
              className="candle-card-close"
              aria-label="봉 상세 정보 닫기"
              onClick={() => setPicked(null)}
            >
              ×
            </button>
            <strong>
              {new Date(picked.bar.timestamp).toLocaleDateString('ko-KR', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
              })}
            </strong>
            {(
              [
                ['시가', picked.bar.open],
                ['고가', picked.bar.high],
                ['저가', picked.bar.low],
                ['종가', picked.bar.close],
              ] as const
            ).map(([label, value]) => (
              <div className="candle-card-row" key={label}>
                <span>{label}</span>
                <b>{value.toLocaleString('ko-KR')}</b>
                <small>{signedPct(value, picked.previous?.close)}</small>
              </div>
            ))}
            <div className="candle-card-row">
              <span>거래량</span>
              <b>{compactCount(picked.bar.volume)}</b>
              <small>{signedPct(picked.bar.volume, picked.previous?.volume)}</small>
            </div>
            <div className="candle-card-row">
              <span>거래대금</span>
              <b>{compactWon(picked.bar.turnover)}</b>
              <small>{signedPct(picked.bar.turnover, picked.previous?.turnover)}</small>
            </div>
            <div className="candle-card-row marcap">
              <span>시가총액</span>
              <b>{compactWon(picked.bar.marcap as number | null | undefined)}</b>
              <small>
                {signedPct(
                  picked.bar.marcap as number | undefined,
                  picked.previous?.marcap as number | undefined,
                )}
              </small>
            </div>
            {MA_PERIODS.map((days, index) => {
              const average = picked.averages[days]
              return (
                <div className="candle-card-row" key={days} style={{ color: MA_COLORS[index] }}>
                  <span>이평 {days}</span>
                  <b>{average == null ? '—' : average.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}</b>
                  <small>{signedPct(average, picked.bar.close)}</small>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
})
