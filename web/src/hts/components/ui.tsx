import type { CSSProperties, ReactNode } from 'react'

// 공용 UI 조각 — StrategyPanel 계열에서 반복되던 마크업을 모았다 (구조 리팩토링 2026-08-06,
// 오너 승인: "공용 UI 컴포넌트 추출"). 스타일은 전부 기존 hts.css 클래스를 그대로 쓴다 —
// 마크업 결과물이 바뀌면 안 된다. 다른 패널(HomePanel 등)은 이번엔 손대지 않았다.

export function Card(props: {
  title: string
  sub?: string
  right?: ReactNode
  flush?: boolean
  children: ReactNode
}) {
  return (
    <section className="card">
      <div className="hd">
        {props.title}
        {props.sub && <span className="sub">{props.sub}</span>}
        {props.right && <span className="right">{props.right}</span>}
      </div>
      <div className={`bd ${props.flush ? 'flush' : ''}`}>{props.children}</div>
    </section>
  )
}

/** kv 한 줄 — 왼쪽 라벨(k) + 오른쪽 값(v). */
export function KV(props: { label: ReactNode; style?: CSSProperties; children: ReactNode }) {
  return (
    <div className="kv" style={props.style}>
      <span className="k">{props.label}</span>
      <span className="v">{props.children}</span>
    </div>
  )
}

/** 메시지 줄 — 항상 한 줄을 차지한다(빈 값이면 공백 유지). 메시지가 뜰 때만 생기면 화면이 튄다. */
export function MsgLine(props: { text: string; warn?: boolean }) {
  return <p className={`msgline ${props.warn ? 'warn' : ''}`}>{props.text || ' '}</p>
}

/** 칩 목록 컨테이너 — `<div className="chips">`. 추가 클래스(sim-layers 등)는 className 으로. */
export function Chips(props: { className?: string; style?: CSSProperties; children: ReactNode }) {
  return (
    <div className={`chips${props.className ? ` ${props.className}` : ''}`} style={props.style}>
      {props.children}
    </div>
  )
}

/** 개별 칩 — on 이면 `chip on` 토글. */
export function Chip(props: { on: boolean; title?: string; onClick: () => void; children: ReactNode }) {
  return (
    <button className={`chip ${props.on ? 'on' : ''}`} title={props.title} onClick={props.onClick}>
      {props.children}
    </button>
  )
}
