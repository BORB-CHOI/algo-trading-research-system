// ④ 백테스팅 결과를 **보기 좋게 추리는 계산**만 모은 곳 — 그리는 코드는 없다.
// 화면 컴포넌트에서 갈라 둬야 "정렬이 왜 저래" 같은 걸 눈이 아니라 테스트로 잡는다.
//
// 전 기간 검사는 줄이 수천 개다. 그냥 늘어놓으면 같은 종목이 여기저기 흩어져서
// 두산으로 찾아도 뭐가 뭔지 모른다(오너 2026-08-10: "두산 검색하면 두산으로 시작하는
// 걸로 다 뜨는데 정렬 안되서 개판이고", "2중 그룹화라도 하든가").
// 그래서 **종목으로 묶고(1단) → 그 안에 라운드(2단)** 로 본다.

import type { BacktestResponse, BacktestRow } from '../../../api'

// ── 거르기 ────────────────────────────────────────────────────
export type RowFilter = 'all' | 'win' | 'lose' | 'stopped' | 'open' | 'closed'

export const FILTER_LABEL: Record<RowFilter, string> = {
  all: '전부',
  win: '번 것',
  lose: '잃은 것',
  stopped: '손절로 끝난 것',
  open: '아직 안 팔린 것',
  closed: '다 판 것',
}

export function matchesFilter(r: BacktestRow, f: RowFilter): boolean {
  switch (f) {
    case 'win':
      return (r.net_return ?? 0) > 0
    case 'lose':
      return (r.net_return ?? 0) <= 0
    case 'stopped':
      return !!r.stopped
    case 'open':
      return !!r.open
    case 'closed':
      return !r.open
    default:
      return true
  }
}

/** 찾기 — 종목명·코드. 앞부분이 맞으면 더 위로 올린다(두산 → 두산에너빌리티가 먼저). */
export function matchesText(r: BacktestRow, needle: string): boolean {
  if (!needle) return true
  return `${r.name ?? ''} ${r.code}`.toLowerCase().includes(needle)
}

// ── 종목 한 덩어리 (1단) ──────────────────────────────────────
export type CodeGroup = {
  code: string
  name: string
  rounds: BacktestRow[] // 2단 — 산 날 순
  n: number // 매매 횟수
  wins: number
  winRate: number
  avgNet: number // 라운드 평균 순수익률
  bestNet: number
  worstNet: number
  openCount: number // 아직 안 판 라운드
  stoppedCount: number
  firstDate: string // 제일 이른 '고른 날'
  lastDate: string
}

function dateOf(r: BacktestRow): string {
  return r.plan_date ?? r.first_fill ?? ''
}

/** 종목별로 묶는다. 라운드는 항상 **산 날 오름차순** — 한 종목 안에서는 시간 순서가 정본이다. */
export function groupByCode(rows: BacktestRow[]): CodeGroup[] {
  const by = new Map<string, BacktestRow[]>()
  for (const r of rows) by.set(r.code, [...(by.get(r.code) ?? []), r])

  return [...by.entries()].map(([code, list]) => {
    const rounds = [...list].sort((a, b) => dateOf(a).localeCompare(dateOf(b)))
    const nets = rounds.map((r) => r.net_return ?? 0)
    const wins = nets.filter((x) => x > 0).length
    return {
      code,
      name: rounds[0]?.name ?? '',
      rounds,
      n: rounds.length,
      wins,
      winRate: rounds.length ? wins / rounds.length : 0,
      avgNet: nets.length ? nets.reduce((a, b) => a + b, 0) / nets.length : 0,
      bestNet: nets.length ? Math.max(...nets) : 0,
      worstNet: nets.length ? Math.min(...nets) : 0,
      openCount: rounds.filter((r) => r.open).length,
      stoppedCount: rounds.filter((r) => r.stopped).length,
      firstDate: dateOf(rounds[0] ?? ({} as BacktestRow)),
      lastDate: dateOf(rounds.at(-1) ?? ({} as BacktestRow)),
    }
  })
}

// ── 정렬 (열 머리를 누르면 바뀐다) ────────────────────────────
export type SortCol = 'name' | 'n' | 'winRate' | 'avgNet' | 'bestNet' | 'lastDate'
export type SortDir = 'asc' | 'desc'

/** 열마다 처음 누를 때의 방향 — 이름은 가나다순, 숫자는 큰 것부터가 자연스럽다. */
export const FIRST_DIR: Record<SortCol, SortDir> = {
  name: 'asc',
  n: 'desc',
  winRate: 'desc',
  avgNet: 'desc',
  bestNet: 'desc',
  lastDate: 'desc',
}

export function sortGroups(groups: CodeGroup[], col: SortCol, dir: SortDir): CodeGroup[] {
  const sign = dir === 'asc' ? 1 : -1
  const value = (g: CodeGroup): number | string => {
    switch (col) {
      case 'name':
        return `${g.name || g.code}`
      case 'n':
        return g.n
      case 'winRate':
        return g.winRate
      case 'bestNet':
        return g.bestNet
      case 'lastDate':
        return g.lastDate
      default:
        return g.avgNet
    }
  }
  return [...groups].sort((a, b) => {
    const x = value(a)
    const y = value(b)
    const c = typeof x === 'string' ? x.localeCompare(y as string) : x - (y as number)
    // 같으면 종목코드로 — 같은 순서가 매번 다르게 나오면 눈이 못 따라간다.
    return c !== 0 ? c * sign : a.code.localeCompare(b.code)
  })
}

/** 거르고 → 묶고 → 정렬. 화면은 이 하나만 부른다. */
export function prepare(
  rows: BacktestRow[],
  opts: { q: string; filter: RowFilter; col: SortCol; dir: SortDir },
): CodeGroup[] {
  const needle = opts.q.trim().toLowerCase()
  const kept = rows.filter((r) => matchesFilter(r, opts.filter) && matchesText(r, needle))
  return sortGroups(groupByCode(kept), opts.col, opts.dir)
}

// ── 성적 (표 위에 먼저 보여줄 것) ─────────────────────────────
export type YearStat = {
  year: string
  n: number
  winRate: number
  avgNet: number
}

/** 산 해로 묶은 성적 — "언제 산 게 좋았나". 안 산 해는 아예 안 만든다. */
export function byYear(rows: BacktestRow[]): YearStat[] {
  const by = new Map<string, number[]>()
  for (const r of rows) {
    const d = r.first_fill ?? r.plan_date
    if (!d || r.net_return == null) continue
    const y = d.slice(0, 4)
    by.set(y, [...(by.get(y) ?? []), r.net_return])
  }
  return [...by.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([year, xs]) => ({
      year,
      n: xs.length,
      winRate: xs.filter((x) => x > 0).length / xs.length,
      avgNet: xs.reduce((a, b) => a + b, 0) / xs.length,
    }))
}

export type Bin = { label: string; from: number; to: number; n: number }

/** 수익률이 어떻게 흩어졌나. 평균 하나만 보면 몇 건의 대박에 가려진다.
 *  경계는 사람이 읽는 단위(±20% · ±50% · ±100%)로 박는다 — 자동 구간은 매번 달라져서
 *  두 검사를 나란히 못 본다. */
const BIN_EDGES: readonly (readonly [string, number, number])[] = [
  ['-50% 아래', -Infinity, -0.5],
  ['-50 ~ -20%', -0.5, -0.2],
  ['-20 ~ 0%', -0.2, 0],
  ['0 ~ +20%', 0, 0.2],
  ['+20 ~ +50%', 0.2, 0.5],
  ['+50 ~ +100%', 0.5, 1],
  ['+100% 위', 1, Infinity],
]

export function distribution(rows: BacktestRow[]): Bin[] {
  const bins: Bin[] = BIN_EDGES.map(([label, from, to]) => ({ label, from, to, n: 0 }))
  for (const r of rows) {
    if (r.net_return == null) continue
    const v = r.net_return
    // 0 은 '잃은 쪽'에 넣는다 — 본전은 수수료를 못 건진 것이라 이긴 게 아니다.
    const i = bins.findIndex((b) => (v > b.from || b.from === -Infinity) && v <= b.to)
    bins[i < 0 ? bins.length - 1 : i].n += 1
  }
  return bins
}

/** 요약 문장에 쓸 값들 — 화면이 계산하지 않게 여기서 뽑는다. */
export function headline(res: BacktestResponse) {
  const m = res.metrics
  const closed = res.closed_metrics
  return {
    isWalkForward: res.base_date == null,
    nTrades: m.n_trades,
    winRate: m.win_rate,
    expectancy: m.expectancy,
    reliable: m.reliable,
    openRounds: res.open_rounds ?? 0,
    closedTrades: closed?.n_trades ?? null,
    closedWinRate: closed?.win_rate ?? null,
    closedExpectancy: closed?.expectancy ?? null,
    codes: res.codes ?? null,
    tradingDays: res.trading_days ?? null,
  }
}
