import { createRoot } from 'react-dom/client'
import { HtsApp } from './HtsApp'
import { hydrate } from './store'
import './hts.css'

// 전략·검색식·관심종목은 로컬 DB(`data/app.db`)가 정본이다 (오너 지시 2026-08-09).
// **그리기 전에** 내려받는다 — 화면 코드가 localStorage 를 동기로 읽으므로, 먼저 안 채우면
// 빈 상태로 그려졌다가 사용자가 저장하는 순간 DB 를 빈 값으로 덮어쓴다.
// 서버가 죽어 있으면 hydrate 가 조용히 포기하고 localStorage 만으로 돈다.
const root = createRoot(document.getElementById('root')!)
void hydrate().finally(() => root.render(<HtsApp />))
