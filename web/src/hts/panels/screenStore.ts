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
