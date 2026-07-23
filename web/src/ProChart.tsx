import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { registerIndicator, type KLineData } from 'klinecharts'
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

// ── 전략 신호 저장소 ──────────────────────────────────────────
// 신호 계산은 전부 파이썬(/api/signals). 여기는 받은 결과를 그리기만 한다.
// 지표 calc/draw 가 모듈 전역을 읽는 구조라 chart 인스턴스 없이도 갱신된다.
const signalByTime = new Map<number, Signal>()

function setSignals(signals: Signal[]): void {
  signalByTime.clear()
  for (const s of signals) {
    signalByTime.set(new Date(`${s.time}T00:00:00`).getTime(), s)
  }
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

  // 전략 신호 마커 — 캔들 위에 ▲(매수)/▼(매도)를 그린다. 일봉 기준.
  registerIndicator({
    name: 'SIGNALS',
    shortName: '전략신호',
    figures: [],
    calc: (dataList) => dataList.map(() => ({})),
    draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }) => {
      if (signalByTime.size === 0) return true
      ctx.save()
      ctx.font = '12px sans-serif'
      ctx.textAlign = 'center'
      for (let i = visibleRange.from; i < visibleRange.to; i++) {
        const bar = kLineDataList[i]
        if (!bar) continue
        const sig = signalByTime.get(bar.timestamp)
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
      signalByTime.clear() // 종목이 바뀌면 이전 종목 신호는 무효
      chartRef.current?.setSymbol(symbolRef.current)
    },
    async applyStrategy(strategy) {
      if (!strategy) {
        setSignals([])
      } else {
        const { signals } = await fetchSignals(symbolRef.current.ticker, strategy)
        setSignals(signals)
      }
      // Pro 는 지표 재계산 API 를 노출하지 않아 setSymbol 로 데이터 재적재를 유도한다.
      chartRef.current?.setSymbol(symbolRef.current)
      return signalByTime.size
    },
  }))

  useEffect(() => {
    const el = elRef.current
    if (!el) return
    registerKoreanLocale()
    ensureIndicators()

    chartRef.current = new KLineChartPro({
      container: el,
      theme: 'light',
      styles: KOREAN_STYLES,
      locale: 'ko-KR',
      drawingBarVisible: true, // 추세선·피보나치 등 그리기 툴바
      symbol: symbolRef.current,
      period: DAY,
      periods: [DAY, WEEK, MONTH],
      mainIndicators: ['MA', 'SIGNALS'], // 이평선 + 전략 신호 마커(신호 없으면 안 그림)
      subIndicators: ['VOL'], // 거래량 창
      datafeed: new ApiDatafeed(),
    })

    // Pro 는 dispose API 가 없어 컨테이너를 비워 정리한다(StrictMode 중복 방지).
    return () => {
      el.innerHTML = ''
      chartRef.current = null
    }
  }, [])

  return <div ref={elRef} style={{ width: '100%', height: '100%' }} />
})
