// 조건검색식 저장소 — 전략과 분리해서 여러 개를 만들고 고쳐 쓴다.
// 전략(2단계)은 여기 저장된 검색식 하나를 골라 매매기법을 붙인다.

import { migrateConditions, type SavedCondition, type ScreenLogic } from './strategyStore'

export const SCREEN_KEY = 'hts-screens'

export type ScreenDef = { logic: ScreenLogic; conditions: SavedCondition[] }
export type Screens = Record<string, ScreenDef>

export function loadScreens(): Screens {
  try {
    const raw = JSON.parse(localStorage.getItem(SCREEN_KEY) ?? '{}') as Screens
    if (!raw || typeof raw !== 'object') return {}
    const out: Screens = {}
    for (const [name, s] of Object.entries(raw)) {
      if (s && Array.isArray(s.conditions)) {
        out[name] = { logic: s.logic ?? 'and', conditions: migrateConditions(s.conditions) }
      }
    }
    return out
  } catch {
    return {}
  }
}

export function saveScreen(all: Screens, name: string, s: ScreenDef): Screens {
  const next = { ...all, [name]: s }
  localStorage.setItem(SCREEN_KEY, JSON.stringify(next))
  return next
}

export function deleteScreen(all: Screens, name: string): Screens {
  const next = { ...all }
  delete next[name]
  localStorage.setItem(SCREEN_KEY, JSON.stringify(next))
  return next
}

/** 자주 쓰는 조건 — 값은 비워둔 채 폼만 열어준다(임계값 하드코딩 금지, ADR-0009). */
export const QUICK_CONDITIONS: { key: string; label: string; hint?: string }[] = [
  { key: 'marcap_range', label: '시가총액' },
  { key: 'amount_range', label: '거래대금' },
  { key: 'new_high', label: '52주 신고가', hint: '기간 250 = 약 52주' },
  { key: 'price_range', label: '주가범위' },
  { key: 'change_range', label: '당일등락률' },
  { key: 'volume_range', label: '거래량' },
  { key: 'above_ma', label: '이평 위' },
  { key: 'golden_cross', label: '골든크로스' },
]
