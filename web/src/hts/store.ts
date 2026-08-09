// 화면 설정 저장소 — 로컬 DB(`data/app.db`)가 정본, localStorage 는 캐시.
//
// 오너 지시 2026-08-09: "하.. 간단하게 로컬 DB 구현해라"
//
// 왜: 전략·검색식이 브라우저 localStorage 에만 있어서, 주소가 localhost ↔ 127.0.0.1 로
// 바뀌자 브라우저가 다른 사이트로 보고 저장한 게 통째로 안 보였다. 캐시를 지우거나 다른
// 브라우저로 열어도 마찬가지였다.
//
// 왜 localStorage 를 남겨 두나: 지금 화면 코드가 전부 **동기**로 읽는다
// (`loadStrategies()` 가 바로 값을 준다). 전부 비동기로 바꾸면 손댈 곳이 너무 많다.
// 그래서 앱이 뜰 때 서버 값을 localStorage 로 한 번 내려 받고(hydrate), 그 뒤로는
// 읽기는 localStorage, 쓰기는 **둘 다** 한다. 서버가 죽어 있어도 화면은 돌아간다.

/** 로컬 DB 로 함께 옮기는 키 — 화면이 쓰던 localStorage 키 그대로. */
export const SYNCED_KEYS = [
  'hts-strategies', // 전략
  'hts-screens', // 조건검색식
  'hts-watchlist', // 관심종목
  'hts-recent-symbols', // 최근 본 종목
  'hts-watchlist-collapsed', // 관심종목 접힘 상태
  'hts-layout-v2', // 화면 배치
] as const

export type SyncedKey = (typeof SYNCED_KEYS)[number]

/** 서버에 올리다 실패한 키. 화면이 "안 지켜졌다"를 알릴 수 있게 남긴다. */
const failed = new Set<string>()

export function unsavedKeys(): string[] {
  return [...failed]
}

async function putRemote(key: string, value: unknown): Promise<void> {
  const res = await fetch(`/api/store/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  })
  if (!res.ok) throw new Error(`저장 실패 (${res.status})`)
}

/** localStorage 에서 읽는다(동기). 서버 값은 hydrate 가 미리 내려 둔다. */
export function readLocal<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw == null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

/** localStorage 에 쓰고, 서버에도 올린다. 서버 실패는 화면을 막지 않는다. */
export function writeBoth(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 용량 초과 등 — 서버에는 그래도 올려 본다
  }
  void putRemote(key, value)
    .then(() => failed.delete(key))
    .catch(() => failed.add(key))
}

/** 앱이 뜰 때 한 번. 서버 값을 localStorage 로 내리고, **서버에 없는데 브라우저에만
 *  있는 값은 서버로 올린다**(첫 이관). 이미 저장해 둔 전략을 잃지 않게 하는 부분이다. */
export async function hydrate(): Promise<{ pulled: string[]; pushed: string[] }> {
  const pulled: string[] = []
  const pushed: string[] = []
  let items: Record<string, unknown> = {}
  try {
    const res = await fetch('/api/store')
    if (!res.ok) throw new Error(String(res.status))
    items = ((await res.json()) as { items?: Record<string, unknown> }).items ?? {}
  } catch {
    return { pulled, pushed } // 서버가 없으면 localStorage 만으로 돈다
  }

  for (const key of SYNCED_KEYS) {
    const remote = items[key]
    const localRaw = localStorage.getItem(key)
    if (remote !== undefined && remote !== null) {
      // 서버가 정본 — 내려 받는다.
      const text = JSON.stringify(remote)
      if (text !== localRaw) {
        localStorage.setItem(key, text)
        pulled.push(key)
      }
    } else if (localRaw != null) {
      // 서버엔 없고 브라우저에만 있다 = 아직 안 옮긴 값. 올린다.
      try {
        await putRemote(key, JSON.parse(localRaw))
        pushed.push(key)
      } catch {
        failed.add(key)
      }
    }
  }
  return { pulled, pushed }
}
