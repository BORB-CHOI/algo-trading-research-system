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
import { fetchSignals, type Signal } from './api'

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

export type ProChartHandle = {
  /** 조건검색 결과 클릭 → 차트 종목 전환 */
  showSymbol: (code: string, name: string, market: string) => void
  /** 전략 오버레이 적용(null = 제거). 신호는 파이썬이 계산한다. */
  applyStrategy: (strategy: string | null) => Promise<number>
}

export const ProChart = forwardRef<ProChartHandle>(function ProChart(_props, ref) {
  const elRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<KLineChartPro | null>(null)
  // 이 차트 전용 신호 저장소·지표 (인스턴스당 1회 생성)
  const signalsRef = useRef<SignalStore | null>(null)
  if (!signalsRef.current) signalsRef.current = createSignalIndicator()
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
      signalsRef.current?.clear() // 종목이 바뀌면 이전 종목 신호는 무효
      chartRef.current?.setSymbol(symbolRef.current)
    },
    async applyStrategy(strategy) {
      const store = signalsRef.current!
      if (!strategy) {
        store.set([])
      } else {
        const ticker = symbolRef.current.ticker
        const { signals } = await fetchSignals(ticker, strategy)
        // 응답 대기 중 종목이 바뀌었으면 버린다 — 이전 종목 신호가 새 차트에 그려지는 경합 방지.
        if (symbolRef.current.ticker !== ticker) return store.size()
        store.set(signals)
      }
      // Pro 는 지표 재계산 API 를 노출하지 않아 setSymbol 로 데이터 재적재를 유도한다.
      chartRef.current?.setSymbol(symbolRef.current)
      return store.size()
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
      // 이평선 + 이 차트 전용 전략 신호 마커(신호 없으면 안 그림)
      mainIndicators: ['MA', signalsRef.current!.indicatorName],
      subIndicators: ['VOL'], // 거래량 창
      datafeed: new ApiDatafeed(),
    })

    return () => {
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
