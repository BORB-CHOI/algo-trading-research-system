import { useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import { pickSymbol } from '../bus'
import { fmtChg } from '../format'
import {
  LEGEND_STEPS,
  fitText,
  layoutMap,
  tileAt,
  tileColor,
  tileLabel,
  tileTextColor,
  type HeatSector,
} from './mapLayout'

// 시장맵 패널 — finviz 식 고정 레이아웃 treemap (자체 SVG 렌더, marcap 데이터 /api/heatmap).
// 타일 크기 = 시총, 색 = 등락률(한국식 상승 빨강/하락 파랑), 업종별 그룹핑 + 헤더 띠.
//
// ECharts treemap 을 버린 이유(오너 지적): zoomToNode 는 확대할 때마다 레이아웃을
// 다시 계산해 타일 순서가 꼬인다. 여기서는 레이아웃을 컨테이너 픽셀로 한 번 고정하고
// 줌은 <g> transform(scale+translate)만 바꾼다 → 확대해도 배치가 절대 안 변하고,
// 작아서 숨겼던 글자가 나타나기만 한다(LOD). 글자도 이미지처럼 배율 따라 커진다.

type Market = 'KOSPI' | 'KOSDAQ'
type HeatResp = { date: string; market: string; sectors_ready: boolean; sectors: HeatSector[] }
type Zoom = { k: number; tx: number; ty: number }

const ZOOM_MIN = 1
const ZOOM_MAX = 30
const ZOOM_RESET: Zoom = { k: 1, tx: 0, ty: 0 }

// 콘텐츠 밖 빈 공간이 안 보이게 이동량을 가둔다: tx ∈ [W−W·k, 0], ty ∈ [H−H·k, 0]
function clampZoom(z: Zoom, w: number, h: number): Zoom {
  const k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z.k))
  return {
    k,
    tx: Math.min(0, Math.max(w - w * k, z.tx)),
    ty: Math.min(0, Math.max(h - h * k, z.ty)),
  }
}

export function MapPanel() {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [market, setMarket] = useState<Market>('KOSPI')
  const [topN, setTopN] = useState(500) // 시총 상위 N 종목
  const [data, setData] = useState<HeatResp | null>(null)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [size, setSize] = useState({ w: 0, h: 0 }) // 컨테이너 픽셀 크기 (ResizeObserver)
  const [zoom, setZoom] = useState<Zoom>(ZOOM_RESET)
  const [hover, setHover] = useState<{ code: string; mx: number; my: number } | null>(null)

  // wheel 핸들러가 리렌더 없이 최신 크기를 읽도록 ref 로도 들고 있는다
  const sizeRef = useRef(size)
  sizeRef.current = size

  // 드래그 팬 상태 — 4px 이상 움직이면 팬으로 간주하고 이어지는 클릭은 무시한다
  const dragRef = useRef<{ sx: number; sy: number; tx0: number; ty0: number } | null>(null)
  const movedRef = useRef(false)

  // ── 데이터 로드 (+ 업종 준비 전이면 4초 간격 자동 재조회) ──────────
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const load = () => {
      fetch(`/api/heatmap?market=${market}&top=${topN}`)
        .then(async (r) => {
          if (!r.ok) {
            const body = (await r.json().catch(() => ({}))) as { detail?: string }
            throw new Error(typeof body.detail === 'string' ? body.detail : `조회 실패 (${r.status})`)
          }
          return r.json() as Promise<HeatResp>
        })
        .then((d) => {
          if (!alive) return
          setData(d)
          setError('')
          // 업종 수집이 아직이면(전부 "기타") ready 될 때까지 4초 간격으로 다시 부른다
          if (!d.sectors_ready) timer = window.setTimeout(load, 4000)
        })
        .catch((e: unknown) => {
          if (alive) setError(e instanceof Error ? e.message : '시장맵 조회 실패')
        })
    }
    load()
    return () => {
      alive = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [market, topN, refreshKey])

  // ── 컨테이너 크기 측정 ─────────────────────────────────────────────
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect
      setSize({ w: Math.round(r.width), h: Math.round(r.height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── 레이아웃 계산 — 줌 상태는 절대 여기 안 들어간다(핵심 요구) ─────
  const layout = useMemo(() => {
    if (!data || size.w < 10 || size.h < 10) return null
    return layoutMap(data.sectors, size.w, size.h)
  }, [data, size.w, size.h])

  // 시장 전환·업종 준비 완료·리사이즈 → 줌 원위치 (배율만 남으면 좌표가 어긋난다).
  // data 객체 자체를 의존성에 넣으면 업종 준비 전 4초 폴링마다(내용이 같아도 새 객체)
  // 사용자가 확대해 둔 뷰가 튕겨 나간다 — 배치가 실제로 바뀌는 신호만 키로 묶는다.
  const layoutKey = data ? `${data.market}|${data.date}|${data.sectors_ready}|${topN}` : ''
  useEffect(() => {
    setZoom(ZOOM_RESET)
  }, [layoutKey, size.w, size.h])

  // ── 휠 줌 (커서 앵커) — 패널 스크롤을 막아야 해서 passive:false 직접 등록 ──
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      setZoom((z) => {
        const { w, h } = sizeRef.current
        const k2 = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z.k * Math.pow(2, -e.deltaY * 0.002)))
        if (k2 === z.k) return z
        // 표준 커서 앵커 공식: 커서 아래 콘텐츠 점이 줌 후에도 같은 화면 위치에 오게
        const tx2 = mx - (mx - z.tx) * (k2 / z.k)
        const ty2 = my - (my - z.ty) * (k2 / z.k)
        return clampZoom({ k: k2, tx: tx2, ty: ty2 }, w, h)
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // ── 드래그 팬 + 툴팁 히트테스트 ────────────────────────────────────
  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return
    movedRef.current = false
    dragRef.current = { sx: e.clientX, sy: e.clientY, tx0: zoom.tx, ty0: zoom.ty }
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const wrap = wrapRef.current
    if (!wrap) return
    const rect = wrap.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    // 툴팁: 줌 역변환한 레이아웃 좌표로 타일 선형 탐색
    if (layout) {
      const t = tileAt(layout, (mx - zoom.tx) / zoom.k, (my - zoom.ty) / zoom.k)
      setHover(t ? { code: t.code, mx, my } : null)
    }

    const d = dragRef.current
    if (!d) return
    if ((e.buttons & 1) === 0) {
      // pointercancel 등으로 버튼이 이미 떨어졌는데 드래그 상태가 남은 경우 —
      // 그대로 두면 호버 이동이 팬으로 오인돼 뷰가 튄다.
      dragRef.current = null
      return
    }
    const dx = e.clientX - d.sx
    const dy = e.clientY - d.sy
    if (!movedRef.current && Math.abs(dx) + Math.abs(dy) < 4) return
    movedRef.current = true
    setZoom((z) => clampZoom({ k: z.k, tx: d.tx0 + dx, ty: d.ty0 + dy }, size.w, size.h))
  }

  const onPointerUp = () => {
    dragRef.current = null
  }

  // 더블클릭(원위치)에 앞서 브라우저는 click 을 두 번 쏜다 — 종목 선택을 잠깐 미뤄서
  // 더블클릭이면 취소한다. 250ms 지연은 서랍이 열리는 체감엔 영향이 없다.
  const clickTimerRef = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(clickTimerRef.current), [])

  const onClick = (e: ReactMouseEvent<SVGSVGElement>) => {
    if (movedRef.current) {
      movedRef.current = false // 팬이었다 — 클릭으로 치지 않는다
      return
    }
    if (e.detail > 1) return // 더블클릭의 두 번째 클릭 — 첫 클릭의 예약도 아래 dblclick 이 취소한다
    const wrap = wrapRef.current
    if (!layout || !wrap) return
    const rect = wrap.getBoundingClientRect()
    const t = tileAt(layout, (e.clientX - rect.left - zoom.tx) / zoom.k, (e.clientY - rect.top - zoom.ty) / zoom.k)
    if (!t) return
    window.clearTimeout(clickTimerRef.current)
    clickTimerRef.current = window.setTimeout(() => pickSymbol({ code: t.code, name: t.name, market }), 250)
  }

  const onDoubleClick = () => {
    window.clearTimeout(clickTimerRef.current) // 첫 클릭이 예약한 종목 선택 취소
    setZoom(ZOOM_RESET)
  }

  // ── SVG 장면 — hover·팬 리렌더에 500개 노드를 다시 만들지 않게 메모 ───
  // LOD 판정은 배율(k)에만 걸린다 — 이동(tx·ty)은 바깥 <g> transform 만 바꾸면 되므로
  // 의존성에서 뺀다. 드래그 팬 중 매 프레임 타일 JSX 재생성을 막는 게 목적.
  const zoomK = zoom.k
  const scene = useMemo(() => {
    if (!layout) return null
    const k = zoomK
    return (
      <>
        {/* 타일 — 경계는 얇은 배경색 선, 줌해도 두꺼워지지 않게 non-scaling */}
        {layout.tiles.map((t) => (
          <rect
            key={t.code}
            x={t.x}
            y={t.y}
            width={t.w}
            height={t.h}
            fill={tileColor(t.chg)}
            stroke="#f4f5f7"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {/* 타일 라벨 — LOD: 화면상 크기(레이아웃×배율)가 읽을 만할 때만 나타난다 */}
        {layout.tiles.map((t) => {
          const { font, maxChars } = tileLabel(t.w, t.h)
          if (font * k < 7 || t.w * k < 26 || t.h * k < 14) return null
          const showChg = t.h >= font * 2.8 // 등락률 줄은 세로 여유가 있을 때만
          const cx = t.x + t.w / 2
          const cy = t.y + t.h / 2
          return (
            <text
              key={t.code}
              x={cx}
              y={showChg ? cy - font * 0.2 : cy + font * 0.35}
              textAnchor="middle"
              fontSize={font}
              fontWeight={600}
              fill={tileTextColor(t.chg)}
              pointerEvents="none"
            >
              {fitText(t.name, maxChars)}
              {showChg && (
                <tspan x={cx} y={cy + font * 1.05} fontSize={font * 0.85} fontWeight={500}>
                  {fmtChg(t.chg)}
                </tspan>
              )}
            </text>
          )
        })}
        {/* 업종 헤더 띠 + 외곽선 — finviz 섹터 헤더의 라이트 버전 */}
        {layout.sectors.map((s) => {
          const hFont = Math.min(11, s.headerH * 0.7)
          return (
            <g key={s.name}>
              {s.headerH > 0 && (
                <rect x={s.x} y={s.y} width={s.w} height={s.headerH} fill="rgba(238,240,243,0.92)" />
              )}
              {s.headerH * k >= 9 && hFont > 0 && (
                <text
                  x={s.x + 4}
                  y={s.y + s.headerH * 0.78}
                  fontSize={hFont}
                  fontWeight={600}
                  fill="#3a4250"
                  pointerEvents="none"
                >
                  {fitText(s.name, Math.max(1, Math.floor((s.w - 8) / hFont)))}
                </text>
              )}
              <rect
                x={s.x}
                y={s.y}
                width={s.w}
                height={s.h}
                fill="none"
                stroke="#d8dce3"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              />
            </g>
          )
        })}
      </>
    )
  }, [layout, zoomK])

  const hoverTile = hover && layout ? (layout.tiles.find((t) => t.code === hover.code) ?? null) : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 상단 툴바 — 제목·기준일 / 시장 전환 / 상위 N / 새로고침 */}
      <div className="toolbar">
        <span className="panel-title" style={{ margin: 0 }}>
          시장맵{data ? ` · ${data.date}` : ''}
        </span>
        <div className="chips" style={{ flexWrap: 'nowrap' }}>
          <button className={`chip${market === 'KOSPI' ? ' on' : ''}`} onClick={() => setMarket('KOSPI')}>
            코스피
          </button>
          <button className={`chip${market === 'KOSDAQ' ? ' on' : ''}`} onClick={() => setMarket('KOSDAQ')}>
            코스닥
          </button>
        </div>
        <span style={{ marginLeft: 'auto' }} />
        <span className="hint" style={{ margin: 0 }}>
          시총=크기 · 등락=색 · 휠=확대 · 더블클릭=원래대로
        </span>
        <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} title="시총 상위 N 종목">
          <option value={150}>상위 150</option>
          <option value={300}>상위 300</option>
          <option value={500}>상위 500</option>
        </select>
        <button onClick={() => setRefreshKey((v) => v + 1)}>새로고침</button>
      </div>

      <div ref={wrapRef} style={{ position: 'relative', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {layout && (
          <svg
            width="100%"
            height="100%"
            viewBox={`0 0 ${size.w} ${size.h}`}
            style={{ display: 'block', touchAction: 'none', cursor: zoom.k > 1 ? 'grab' : 'default' }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onPointerLeave={() => setHover(null)}
            onClick={onClick}
            onDoubleClick={onDoubleClick}
          >
            <g transform={`translate(${zoom.tx},${zoom.ty}) scale(${zoom.k})`}>{scene}</g>
          </svg>
        )}

        {/* 상태 안내 — 에러 / 업종 수집 중 */}
        <div style={{ position: 'absolute', top: 8, left: 8, pointerEvents: 'none' }}>
          {error && (
            <p className="hint" style={{ margin: 0 }}>
              {error} — 새로고침으로 재시도할 수 있습니다.
            </p>
          )}
          {!error && data && !data.sectors_ready && (
            <p className="hint" style={{ margin: 0 }}>
              업종 분류 수집 중 — 준비되면 자동으로 다시 불러옵니다.
            </p>
          )}
          {!error && layout && layout.tiles.length === 0 && (
            <p className="hint" style={{ margin: 0 }}>
              표시할 종목이 없습니다.
            </p>
          )}
        </div>

        {/* 확대 중일 때만 — 원위치 버튼 */}
        {zoom.k > 1 && (
          <button style={{ position: 'absolute', top: 8, right: 8 }} onClick={() => setZoom(ZOOM_RESET)}>
            원래대로
          </button>
        )}

        {/* 툴팁 — 커서 옆 절대배치, 가장자리에서는 안쪽으로 밀어 넣는다 */}
        {hoverTile && hover && (
          <div
            style={{
              position: 'absolute',
              left: Math.min(hover.mx + 12, Math.max(0, size.w - 200)),
              top: Math.min(hover.my + 14, Math.max(0, size.h - 76)),
              pointerEvents: 'none',
              background: 'var(--hts-panel)',
              border: '1px solid var(--hts-border)',
              borderRadius: 8,
              boxShadow: '0 4px 14px rgba(20,26,38,0.12)',
              padding: '7px 10px',
              fontSize: 12,
              color: 'var(--hts-text)',
              lineHeight: 1.5,
              whiteSpace: 'nowrap',
            }}
          >
            <b>{hoverTile.name}</b>
            <div className="hint" style={{ margin: 0 }}>
              {hoverTile.sector}
            </div>
            <div style={{ fontVariantNumeric: 'tabular-nums' }}>
              시총 {(hoverTile.marcap / 1e12).toFixed(2)}조 · 등락 {fmtChg(hoverTile.chg)}
            </div>
          </div>
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
