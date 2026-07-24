import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { pickSymbol } from '../bus'

// 시장맵 패널 — Apache ECharts treemap, marcap 데이터(/api/heatmap).
// finviz 관례: 타일 크기 = 시총, 색 = 등락률. 한국식 상승 빨강/하락 파랑.
type HeatItem = { code: string; name: string; marcap: number; chg: number }

// 등락률 → 타일 색: 0% 근처 중립 연회색(#c8ceda)에서 ±3% 로 갈수록
// 진한 빨강(#e01e1e = --hts-up)/파랑(#1668d0 = --hts-down)으로 부드럽게 보간. 화이트 셸 기준.
// (ECharts 캔버스는 CSS var() 를 못 읽으므로 토큰 값을 리터럴로 둔다)
const NEUTRAL = [200, 206, 218] as const // #c8ceda 중립 (라이트)
const UP_MAX = [224, 30, 30] as const // #e01e1e (--hts-up)
const DOWN_MAX = [22, 104, 208] as const // #1668d0 (--hts-down)

function tileColor(chg: number): string {
  const t = Math.min(Math.abs(chg), 3) / 3
  const [r, g, b] = chg >= 0 ? UP_MAX : DOWN_MAX
  const mix = (a: number, c: number) => Math.round(a + (c - a) * t)
  return `rgb(${mix(NEUTRAL[0], r)},${mix(NEUTRAL[1], g)},${mix(NEUTRAL[2], b)})`
}

// 중립(연회색) 근처 타일은 흰 글자가 안 보인다 — 등락이 약하면 어두운 글자로.
function tileTextColor(chg: number): string {
  return Math.abs(chg) < 1 ? '#1f2430' : '#ffffff'
}

// finviz 식 범례 구간 (-3% ~ +3%)
const LEGEND_STEPS = [-3, -2, -1, 0, 1, 2, 3] as const

export function MapPanel() {
  const elRef = useRef<HTMLDivElement>(null)
  const [topN, setTopN] = useState(300) // 상위 N 종목 (시총순)
  const [date, setDate] = useState('') // 데이터 기준일 (응답에서 채움)
  const [refreshKey, setRefreshKey] = useState(0) // 새로고침 트리거
  const [error, setError] = useState('')

  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const chart = echarts.init(el) // 라이트 셸 — 다크 테마 사용 ❌ (오너 지시)
    setError('')
    fetch(`/api/heatmap?top=${topN}`)
      .then(async (r) => {
        if (!r.ok) {
          const body = (await r.json().catch(() => ({}))) as { detail?: string }
          throw new Error(typeof body.detail === 'string' ? body.detail : `조회 실패 (${r.status})`)
        }
        return r.json()
      })
      .then(({ date, markets }: { date: string; markets: Record<string, HeatItem[]> }) => {
        if (chart.isDisposed()) return
        setDate(date)
        chart.setOption({
          backgroundColor: 'transparent',
          tooltip: {
            formatter: (p: { name: string; value: number; data: { chg?: number } }) =>
              p.data.chg === undefined
                ? p.name
                : `${p.name}<br/>시총 ${(p.value / 1e12).toFixed(2)}조 · 등락 ${p.data.chg}%`,
          },
          series: [
            {
              type: 'treemap',
              roam: true,
              nodeClick: 'zoomToNode',
              top: 4,
              left: 4,
              right: 4,
              bottom: 26, // breadcrumb 자리
              // breadcrumb(탐색 경로) — 라이트 톤
              breadcrumb: {
                show: true,
                left: 'center',
                bottom: 0,
                height: 20,
                itemStyle: {
                  color: '#eef0f3', // --hts-elev
                  borderColor: '#d8dce3', // --hts-border
                  textStyle: { color: '#5b6472', fontSize: 11 }, // --hts-text-2
                },
              },
              // 시장(KOSPI/KOSDAQ) 헤더 — finviz 섹터 헤더의 라이트 버전 (밝은 반투명 띠)
              upperLabel: {
                show: true,
                height: 22,
                backgroundColor: 'rgba(238,240,243,0.9)', // --hts-elev 반투명
                color: '#1f2430', // --hts-text
                fontSize: 11,
                fontWeight: 600,
                overflow: 'truncate',
              },
              itemStyle: { borderColor: '#f4f5f7', borderWidth: 1, gapWidth: 1 }, // --hts-bg 경계
              // 타일 라벨: 종목명 줄임 + 등락률. 글자색은 타일 진하기에 따라 개별 지정.
              label: { fontSize: 10, lineHeight: 13, overflow: 'truncate' },
              data: Object.entries(markets).map(([market, items]) => ({
                name: market,
                children: items.map((s) => ({
                  name: s.name,
                  value: s.marcap,
                  chg: s.chg,
                  code: s.code,
                  market,
                  label: {
                    formatter: `${s.name}\n${s.chg > 0 ? '+' : ''}${s.chg}%`,
                    color: tileTextColor(s.chg),
                  },
                  itemStyle: { color: tileColor(s.chg) },
                })),
              })),
            },
          ],
        })
        chart.on('click', (p) => {
          const d = p.data as { code?: string; name?: string; market?: string }
          if (d.code && d.name && d.market) pickSymbol({ code: d.code, name: d.name, market: d.market })
        })
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : '시장맵 조회 실패')
      })
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.dispose()
    }
  }, [topN, refreshKey])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 상단 툴바 — 좌: 제목·기준일 / 우: 상위 N 선택 + 새로고침 */}
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>
          시장맵{date ? ` · ${date}` : ''}
        </span>
        <span className="hint" style={{ margin: 0 }}>
          시총=크기 · 등락=색
        </span>
        <span style={{ marginLeft: 'auto' }} />
        <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} title="시총 상위 N 종목">
          <option value={150}>상위 150</option>
          <option value={300}>상위 300</option>
          <option value={500}>상위 500</option>
        </select>
        <button onClick={() => setRefreshKey((k) => k + 1)}>새로고침</button>
      </div>

      <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
        <div ref={elRef} style={{ width: '100%', height: '100%' }} />
        {error && (
          <p className="hint" style={{ position: 'absolute', top: 8, left: 8 }}>
            {error} — 새로고침으로 재시도할 수 있습니다.
          </p>
        )}
      </div>

      {/* 하단 finviz 식 범례 바 (-3% ~ +3%) */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          alignItems: 'center',
          padding: '3px 8px',
          background: 'var(--hts-panel)',
          borderTop: '1px solid var(--hts-border)',
        }}
      >
        {LEGEND_STEPS.map((v) => (
          <span
            key={v}
            style={{
              background: tileColor(v),
              color: tileTextColor(v),
              fontSize: 11,
              height: 20,
              lineHeight: '20px',
              minWidth: 38,
              textAlign: 'center',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {v > 0 ? `+${v}%` : `${v}%`}
          </span>
        ))}
      </div>
    </div>
  )
}
