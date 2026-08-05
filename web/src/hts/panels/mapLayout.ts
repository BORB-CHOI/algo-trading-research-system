// 시장맵 레이아웃 계산 — React·DOM 무관 순수 함수 모음.
//
// 핵심 설계: 레이아웃(타일 위치·크기)은 컨테이너 픽셀 기준으로 한 번만 계산하고,
// 줌은 SVG transform(scale+translate)으로만 처리한다. finviz 방식 — 확대해도
// 타일 배치가 절대 재배치되지 않고, 작아서 안 보이던 글자가 나타나기만 한다.

export type Rect = { x: number; y: number; w: number; h: number }

export type HeatItem = { code: string; name: string; marcap: number; chg: number }
export type HeatSector = { name: string; items: HeatItem[] }

export type SectorRect = {
  name: string
  x: number
  y: number
  w: number
  h: number
  headerH: number // 업종 헤더 띠 높이 (0 이면 띠 생략)
}

export type TileRect = HeatItem & Rect & { sector: string }

export type MapLayout = { sectors: SectorRect[]; tiles: TileRect[] }

// ── 색상 (MapPanel 의 ECharts 시절 값·로직 그대로 이관) ───────────────
// 등락률 → 타일 색: 0% 근처 중립 연회색(#c8ceda)에서 ±3% 로 갈수록
// 진한 빨강(#e01e1e = --hts-up)/파랑(#1668d0 = --hts-down)으로 부드럽게 보간. 화이트 셸 기준.
// (SVG 속성에 리터럴로 넣으므로 CSS var() 대신 토큰 값을 그대로 둔다)
export const NEUTRAL = [200, 206, 218] as const // #c8ceda 중립 (라이트)
export const UP_MAX = [224, 30, 30] as const // #e01e1e (--hts-up)
export const DOWN_MAX = [22, 104, 208] as const // #1668d0 (--hts-down)

export function tileColor(chg: number): string {
  const t = Math.min(Math.abs(chg), 3) / 3
  const [r, g, b] = chg >= 0 ? UP_MAX : DOWN_MAX
  const mix = (a: number, c: number) => Math.round(a + (c - a) * t)
  return `rgb(${mix(NEUTRAL[0], r)},${mix(NEUTRAL[1], g)},${mix(NEUTRAL[2], b)})`
}

// 중립(연회색) 근처 타일은 흰 글자가 안 보인다 — 등락이 약하면 어두운 글자로.
export function tileTextColor(chg: number): string {
  return Math.abs(chg) < 1 ? '#1f2430' : '#ffffff'
}

// finviz 식 범례 구간 (-3% ~ +3%)
export const LEGEND_STEPS = [-3, -2, -1, 0, 1, 2, 3] as const

// 음수·0 만이 아니라 NaN(API 응답에 marcap 이 빠진 경우)도 0 으로 —
// NaN 하나가 total 에 섞이면 scale 이 NaN 이 되어 맵 전체 좌표가 오염된다.
function safeValue(v: number): number {
  return Number.isFinite(v) ? Math.max(v, 0) : 0
}

// ── squarified treemap (Bruls, Huizing, van Wijk 2000) ────────────────
// 값 배열(내림차순 전제)을 주어진 사각형에 배치한다. 반환 순서 = 입력 순서.
// 짧은 변을 따라 줄(row)을 만들되, 종목을 하나 더 넣었을 때 줄 안 최악의
// 가로세로비가 나빠지는 순간 줄을 확정한다 → 타일이 최대한 정사각형에 가깝다.
export function squarify(values: readonly number[], rect: Rect): Rect[] {
  const n = values.length
  const out: Rect[] = new Array<Rect>(n)
  const total = values.reduce((a, v) => a + safeValue(v), 0)
  if (n === 0) return out
  if (total <= 0 || rect.w <= 0 || rect.h <= 0) {
    // 값이 전부 0이거나 영역이 없는 퇴화 케이스 — 전부 크기 0으로 채워 안전하게 반환
    for (let i = 0; i < n; i++) out[i] = { x: rect.x, y: rect.y, w: 0, h: 0 }
    return out
  }

  // 값 → 면적(px²) 환산
  const scale = (rect.w * rect.h) / total
  const areas = values.map((v) => safeValue(v) * scale)

  let { x, y, w, h } = rect
  let start = 0
  while (start < n) {
    // 현재 남은 영역의 짧은 변에 놓을 줄 [start, end) 을 탐욕적으로 확장
    const side = Math.min(w, h)
    let rowArea = areas[start]
    let end = start + 1
    let worst = worstRatio(areas, start, end, rowArea, side)
    while (end < n) {
      const nextArea = rowArea + areas[end]
      const nextWorst = worstRatio(areas, start, end + 1, nextArea, side)
      if (nextWorst > worst) break // 하나 더 넣으면 비율이 나빠진다 → 줄 확정
      rowArea = nextArea
      worst = nextWorst
      end++
    }

    // 확정된 줄을 짧은 변 방향으로 배치하고 남은 영역을 줄인다
    const thick = side > 0 ? rowArea / side : 0
    if (w >= h) {
      // 가로가 길다 → 왼쪽에 세로 줄
      let cy = y
      for (let i = start; i < end; i++) {
        const ih = rowArea > 0 ? (areas[i] / rowArea) * h : 0
        out[i] = { x, y: cy, w: thick, h: ih }
        cy += ih
      }
      x += thick
      w -= thick
    } else {
      // 세로가 길다 → 위쪽에 가로 줄
      let cx = x
      for (let i = start; i < end; i++) {
        const iw = rowArea > 0 ? (areas[i] / rowArea) * w : 0
        out[i] = { x: cx, y, w: iw, h: thick }
        cx += iw
      }
      y += thick
      h -= thick
    }
    start = end
  }
  return out
}

// 줄 [start, end) 이 짧은 변 side 에 놓일 때 줄 안 최악(최대) 가로세로비.
function worstRatio(areas: readonly number[], start: number, end: number, rowArea: number, side: number): number {
  let maxA = 0
  let minA = Infinity
  for (let i = start; i < end; i++) {
    if (areas[i] > maxA) maxA = areas[i]
    if (areas[i] < minA) minA = areas[i]
  }
  const s2 = rowArea * rowArea
  const w2 = side * side
  if (s2 <= 0 || w2 <= 0 || minA <= 0) return Infinity
  return Math.max((w2 * maxA) / s2, s2 / (w2 * minA))
}

// ── 업종 헤더 띠 높이 ─────────────────────────────────────────────────
// 기본 16px. 업종 영역이 낮으면(48 미만) 30% 로 줄이고, 12 미만이면 아예 생략 —
// 띠가 종목 영역을 다 먹어버리는 것을 막는다.
export function headerHeight(h: number): number {
  if (h < 12) return 0
  if (h < 48) return h * 0.3
  return 16
}

// ── 2단계 레이아웃 ────────────────────────────────────────────────────
// 1단계: 업종별 시총합으로 업종 영역을 squarify.
// 2단계: 각 업종 영역에서 헤더 띠를 뺀 나머지에 종목을 squarify.
// sectors·items 모두 시총 내림차순 정렬 전제(API 계약) — squarify 가 순서를 보존한다.
export function layoutMap(sectors: readonly HeatSector[], width: number, height: number): MapLayout {
  const nonEmpty = sectors.filter((s) => s.items.length > 0)
  const sums = nonEmpty.map((s) => s.items.reduce((a, it) => a + safeValue(it.marcap), 0))
  const sectorRects = squarify(sums, { x: 0, y: 0, w: width, h: height })

  const outSectors: SectorRect[] = []
  const tiles: TileRect[] = []
  nonEmpty.forEach((s, i) => {
    const r = sectorRects[i]
    const headerH = headerHeight(r.h)
    outSectors.push({ name: s.name, x: r.x, y: r.y, w: r.w, h: r.h, headerH })

    const body: Rect = { x: r.x, y: r.y + headerH, w: r.w, h: r.h - headerH }
    const itemRects = squarify(
      s.items.map((it) => safeValue(it.marcap)),
      body,
    )
    s.items.forEach((it, j) => {
      const t = itemRects[j]
      tiles.push({ ...it, sector: s.name, x: t.x, y: t.y, w: t.w, h: t.h })
    })
  })
  return { sectors: outSectors, tiles }
}

// ── 라벨 도우미 ───────────────────────────────────────────────────────
// 타일 크기 → 라벨 폰트 크기(레이아웃 단위)와 폭에 들어가는 글자 수.
// 줌 배율을 곱하지 않는다 — 글자는 <g> scale 로 타일과 같이 커진다(이미지 확대처럼).
// 한글 1자 폭 ≈ 폰트 크기 × 1.0 근사 (고딕 계열 전각 기준).
export function tileLabel(w: number, h: number): { font: number; maxChars: number } {
  const font = Math.max(0, Math.min(13, w / 5, h / 2.4))
  const maxChars = font > 0 ? Math.max(1, Math.floor((w - font * 0.6) / font)) : 0
  return { font, maxChars }
}

// 글자 수 제한에 맞춰 자르고 말줄임표를 붙인다.
export function fitText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text
  if (maxChars <= 1) return text.slice(0, 1)
  return `${text.slice(0, maxChars - 1)}…`
}

// ── 히트테스트 ────────────────────────────────────────────────────────
// 레이아웃 좌표(줌 역변환 후)의 점이 속한 타일. 500개 선형 탐색이면 충분하다.
export function tileAt(layout: MapLayout, lx: number, ly: number): TileRect | null {
  for (const t of layout.tiles) {
    if (lx >= t.x && lx < t.x + t.w && ly >= t.y && ly < t.y + t.h) return t
  }
  return null
}
