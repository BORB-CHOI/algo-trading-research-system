import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { dispose as disposeKLineChart, registerIndicator, type KLineData } from 'klinecharts'
import {
  KLineChartPro,
  type Datafeed,
  type SymbolInfo,
  type Period,
} from '@klinecharts/pro'
import '@klinecharts/pro/dist/klinecharts-pro.css'
import { registerKoreanLocale } from './locales'
import { allVisible, type SimVisibility } from './simVisibility'
import {
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
// 라이트 테마 대비색: fib 주황 실선 / round 회색 점선 / anchor 파랑 점선.
const OVERLAY_COLORS: Record<OverlayLine['kind'], string> = {
  fib: '#d97706', // 피보나치 레벨 — 실선
  round: '#6b7280', // 라운드 피겨(호가 눈금) — 점선
  anchor: '#2f6fed', // 베이스/신고가 기준선 — 점선
  buy: '#dd3c44', // 분할 매수 목표가 — 한국식 상승색(빨강)과 맞춘다
  sell: '#0062df', // 분할 매도 목표가 — 하락색(파랑)
  stop: '#111827', // 손절선 — 매매선과 확실히 구분되는 진회색
}
// 매수·매도·손절은 "여기서 실제로 주문이 나간다"는 선이라 더 굵게 그린다.
const THICK_KINDS = new Set<OverlayLine['kind']>(['buy', 'sell', 'stop'])
const TOUCH_COLOR = '#d97706' // 레벨 근접(◆) 마커
const VWAP_COLOR = '#7c3aed' // 앵커 VWAP — 피보나치/매매선과 겹치지 않는 보라

type OverlayStore = {
  indicatorName: string
  set: (lines: OverlayLine[], touches: OverlayTouch[]) => void
  /** 시뮬레이션 결과 — 체결 마커와 곡선(앵커 VWAP). 없으면 빈 배열로 지운다. */
  setSim: (fills: OverlayFill[], series: OverlaySeries[]) => void
  setVisibility: (v: SimVisibility) => void
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

// 가격을 라벨에 병기할 선 종류 — "매수, 매도 지점 가로선으로 가격과 함께" (오너 지시).
// fib/round 는 서버 라벨에 이미 비율·라운드값이 있어 붙이면 중복이다.
const PRICE_LABEL_KINDS = new Set<OverlayLine['kind']>(['buy', 'sell', 'stop', 'anchor'])

/** 수평선 + 우측 라벨. y축 범위 밖 레벨은 건너뛴다. */
function drawLines(c: DrawCtx, list: OverlayLine[]): void {
  const { ctx, bounding, yAxis } = c
  for (const ln of list) {
    const y = Math.round(yAxis.convertToPixel(ln.price)) + 0.5 // +0.5 = 1px 라인 선명하게
    if (y < 0 || y > bounding.height) continue
    const color = OVERLAY_COLORS[ln.kind]
    const thick = THICK_KINDS.has(ln.kind)
    ctx.strokeStyle = color
    ctx.lineWidth = thick ? 2 : 1
    ctx.setLineDash(ln.kind === 'fib' || thick ? [] : [4, 3])
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(bounding.width, y)
    ctx.stroke()
    // 우측 라벨 — 캔들과 겹쳐도 읽히게 반투명 흰 바탕(라이트 테마) 위에 그린다.
    // 선 **아래**에 둔다 — 위에 얹으면 바로 위 선·라벨과 포개져 숫자가 안 읽힌다
    // (오너 지적 2026-08-06: "퍼센테이지랑 숫자값을 선 아래에").
    ctx.setLineDash([])
    ctx.lineWidth = 1
    const label = PRICE_LABEL_KINDS.has(ln.kind)
      ? `${ln.label} ${ln.price.toLocaleString('ko-KR')}`
      : ln.label
    const pad = 3
    const w = ctx.measureText(label).width
    const x = bounding.width - w - pad * 2 - 2
    ctx.fillStyle = 'rgba(255,255,255,0.85)'
    ctx.fillRect(x, y + 2, w + pad * 2, 14)
    ctx.fillStyle = color
    ctx.textAlign = 'left'
    ctx.fillText(label, x + pad, y + 13)
  }
}

/** 봉마다 찍히는 마커를 공통으로 순회한다 (터치 ◆ / 체결 ▲▼). */
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
        touchesByTime.size === 0 &&
        fillsByTime.size === 0 &&
        seriesList.length === 0
      if (empty) return true
      const { ctx, yAxis } = c
      ctx.save()
      ctx.font = '11px sans-serif'
      ctx.lineWidth = 1

      // 'round' 는 필터 대상이 아니다(케이스 검사기 전용) — vis 에 없는 kind 는 항상 그린다.
      const visOf = vis as Partial<Record<OverlayLine['kind'], boolean>>
      if (vis.vwap) drawSeries(c, seriesList) // 곡선을 먼저 — 수평선 아래에 깔린다
      drawLines(c, lines.filter((ln) => visOf[ln.kind] ?? true))

      ctx.textAlign = 'center'
      ctx.fillStyle = TOUCH_COLOR
      forEachBarMark(c, touchesByTime, (t, x) => {
        ctx.fillText('◆', x, yAxis.convertToPixel(t.price) + 4)
      })

      // 체결 마커는 "실제로 체결됐을 지점"이라 차수 숫자까지 찍어 어느 분할인지 보이게 한다.
      if (vis.fills) forEachBarMark(c, fillsByTime, (f, x) => {
        const y = yAxis.convertToPixel(f.price)
        const buy = f.side === 'buy'
        const isStop = !buy && f.stage === 0 // stage 0 = 손절 체결
        ctx.fillStyle = isStop ? OVERLAY_COLORS.stop : OVERLAY_COLORS[f.side]
        ctx.fillText(buy ? '▲' : '▼', x, buy ? y + 14 : y - 6)
        ctx.font = 'bold 9px sans-serif'
        ctx.fillText(isStop ? '손' : String(f.stage), x, buy ? y + 23 : y - 15)
        ctx.font = '11px sans-serif'
      })

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
      touchesByTime.clear()
      fillsByTime.clear()
      seriesList = []
    },
  }
}

function toDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

// 우리 FastAPI 백엔드를 Pro 데이터피드로 연결. 실시간 없음(과거 데이터만).
class ApiDatafeed implements Datafeed {
  async searchSymbols(search?: string): Promise<SymbolInfo[]> {
    const res = await fetch(`/api/symbols?q=${encodeURIComponent(search ?? '')}`)
    if (!res.ok) return []
    const { symbols } = (await res.json()) as {
      symbols: { ticker: string; name: string; market: string }[]
    }
    return symbols.map((s) => ({
      ticker: s.ticker,
      name: s.name,
      shortName: s.name,
      exchange: s.market,
      market: 'stocks',
      pricePrecision: 0,
      volumePrecision: 0,
      priceCurrency: 'krw',
    }))
  }

  async getHistoryKLineData(
    symbol: SymbolInfo,
    period: Period,
    from: number,
    to: number,
  ): Promise<KLineData[]> {
    const params = new URLSearchParams({
      code: symbol.ticker,
      start: toDate(from),
      end: toDate(to),
      period: period.timespan, // day | week | month — 서버가 일봉을 리샘플한다
    })
    const res = await fetch(`/api/candles?${params.toString()}`)
    if (!res.ok) return [] // 404(구간 데이터 없음) 등은 빈 배열
    const { candles } = (await res.json()) as {
      candles: {
        time: string
        open: number
        high: number
        low: number
        close: number
        volume: number
        amount: number
      }[]
    }
    return candles.map((c) => ({
      timestamp: new Date(`${c.time}T00:00:00`).getTime(),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
      turnover: c.amount,
    }))
  }

  // 과거 데이터만 — 실시간 구독 없음.
  subscribe(): void {
    // no-op
  }

  unsubscribe(): void {
    // no-op
  }
}

const DAY: Period = { multiplier: 1, timespan: 'day', text: '일' }
const WEEK: Period = { multiplier: 1, timespan: 'week', text: '주' }
const MONTH: Period = { multiplier: 1, timespan: 'month', text: '월' }

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
}

// 전략 적용 payload — bus.StrategyPick 과 같은 형태(구조적 타이핑).
// ProChart 는 hts 셸에 의존하지 않도록 자체 선언한다. 파라미터 값은 항상 여기 담겨
// 서버 요청으로만 나간다(ADR-0009 — 프런트·서버 어디에도 전략 숫자 하드코딩 없음).
export type StrategyPayload = {
  key: string
  params: Record<string, number>
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
  setSimVisibility: (v: SimVisibility) => void
}

export const ProChart = forwardRef<ProChartHandle>(function ProChart(_props, ref) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<KLineChartPro | null>(null)
  // 이 차트 전용 신호·오버레이 저장소·지표 (인스턴스당 1회 생성)
  const signalsRef = useRef<SignalStore | null>(null)
  if (!signalsRef.current) signalsRef.current = createSignalIndicator()
  const overlayRef = useRef<OverlayStore | null>(null)
  if (!overlayRef.current) overlayRef.current = createOverlayIndicator()
  // 현재 적용중 전략 payload — 종목 전환 시 이 값으로 재조회한다.
  const strategyRef = useRef<StrategyPayload | null>(null)
  const symbolRef = useRef<SymbolInfo>({
    ticker: '005930',
    name: '삼성전자',
    shortName: '삼성전자',
    exchange: 'KOSPI',
    market: 'stocks',
    pricePrecision: 0,
    volumePrecision: 0,
    priceCurrency: 'krw',
  })

  // 전략 데이터 재조회 — 적용/해제/종목 전환 공용.
  // 응답 대기 중 종목·전략이 바뀌었으면 버린다(늦게 온 이전 응답이 새 차트에 그려지는 경합 방지).
  async function refreshStrategy(): Promise<void> {
    const payload = strategyRef.current
    const ticker = symbolRef.current.ticker
    const sig = signalsRef.current!
    const ov = overlayRef.current!
    sig.set([])
    ov.clear()
    if (payload) {
      const stale = () => symbolRef.current.ticker !== ticker || strategyRef.current !== payload
      try {
        if (payload.signals) {
          const res = await postSignals({ code: ticker, strategy: payload.key, params: payload.params })
          if (stale()) return
          sig.set(res.signals)
        }
        if (payload.overlay) {
          const res = await postOverlay({ code: ticker, strategy: payload.key, params: payload.params })
          if (stale()) return
          ov.set(res.lines, res.touches)
        }
      } catch (e) {
        // 400(베이스 못 찾음 등)·404(종목 없음) — 이 종목엔 그릴 게 없다. 콘솔로만 남긴다.
        if (stale()) return
        console.warn('[전략 조회 실패]', e instanceof Error ? e.message : e)
      }
    }
    // Pro 는 지표 재계산 API 를 노출하지 않아 setSymbol 로 데이터 재적재를 유도한다.
    chartRef.current?.setSymbol(symbolRef.current)
  }

  useImperativeHandle(ref, () => ({
    showSymbol(code, name, market) {
      symbolRef.current = {
        ticker: code,
        name,
        shortName: name,
        exchange: market,
        market: 'stocks',
        pricePrecision: 0,
        volumePrecision: 0,
        priceCurrency: 'krw',
      }
      // 종목이 바뀌면 이전 종목의 신호·오버레이는 무효 — 즉시 지우고 차트를 전환한다.
      signalsRef.current?.set([])
      overlayRef.current?.clear()
      chartRef.current?.setSymbol(symbolRef.current)
      // 적용중 전략이 있으면 새 종목 기준으로 재조회(경합 가드는 refreshStrategy 내부).
      if (strategyRef.current) void refreshStrategy()
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
      // Pro 가 지표 재계산 API 를 안 열어둬서 setSymbol 로 다시 그리게 한다(기존 방식과 동일).
      chartRef.current?.setSymbol(symbolRef.current)
    },
    setSimVisibility(v) {
      overlayRef.current!.setVisibility(v)
      // setSymbol(데이터 재적재·줌 초기화) 대신 window resize 를 쏜다 — Pro 가 이 이벤트에서
      // chart.resize() 를 부르고, resize() 는 크기가 같아도 전체 페인트를 다시 탄다(소스 확인).
      window.dispatchEvent(new Event('resize'))
    },
  }))

  useEffect(() => {
    const el = elRef.current
    if (!el) return
    registerKoreanLocale()
    ensureIndicators()

    chartRef.current = new KLineChartPro({
      container: el,
      theme: 'light', // 오너 지시: 화이트 계열 (다크 ❌)
      watermark: '', // 배경 KLineChart 로고 제거 (오너 지시)
      styles: KOREAN_STYLES,
      locale: 'ko-KR',
      drawingBarVisible: true, // 추세선·피보나치 등 그리기 툴바
      symbol: symbolRef.current,
      period: DAY,
      periods: [DAY, WEEK, MONTH],
      // 이평선 + 이 차트 전용 전략 신호 마커·오버레이 수평선(데이터 없으면 안 그림)
      mainIndicators: ['MA', signalsRef.current!.indicatorName, overlayRef.current!.indicatorName],
      subIndicators: ['VOL'], // 거래량 창
      datafeed: new ApiDatafeed(),
    })

    // 패널(dockview) 크기가 바뀌어도 차트가 따라오지 않는 문제 해결.
    // KLineChart Pro 는 **window resize 만** 듣고 컨테이너 크기 변화는 감지하지 않는다
    // (Pro 소스 확인: addEventListener("resize", …), resize() 공개 API 없음).
    // 그래서 컨테이너를 직접 관찰해 Pro 자신의 리사이즈 경로(window resize)를 깨운다.
    let pending = 0
    const ro = new ResizeObserver(() => {
      if (pending) return // 드래그 중 연속 호출은 한 프레임에 하나로 합친다
      pending = requestAnimationFrame(() => {
        pending = 0
        // 탭 전환으로 패널이 숨겨지면 크기가 0 이 된다 — 이때 리사이즈를 태우면
        // 차트가 0px 기준으로 보이는 봉 수를 다시 잡아, 돌아올 때마다 확대돼 보인다.
        const r = el.getBoundingClientRect()
        if (r.width < 2 || r.height < 2) return
        window.dispatchEvent(new Event('resize'))
      })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      if (pending) cancelAnimationFrame(pending)
      // Pro 는 dispose 를 노출하지 않지만, 내부 klinecharts 는 전역 인스턴스 맵에
      // 등록돼 있어 dispose() 없이는 패널을 닫을 때마다 차트가 잔류한다(메모리 누수).
      // init 이 컨테이너에 남긴 k-line-chart-id 속성으로 찾아 직접 해제한다.
      const inner = el.querySelector('[k-line-chart-id]')
      if (inner) disposeKLineChart(inner as HTMLElement)
      el.innerHTML = ''
      chartRef.current = null
    }
  }, [])

  return <div ref={elRef} style={{ width: '100%', height: '100%' }} />
})
