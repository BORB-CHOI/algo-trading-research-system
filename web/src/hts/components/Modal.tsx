import { useEffect } from 'react'
import { AnimatePresence, motion } from 'motion/react'

// 가운데 모달 공통. 열림/닫힘 애니메이션·Esc·배경 클릭·바디 스크롤 잠금을 여기서 한 번만 처리한다.
export function Modal(props: {
  open: boolean
  onClose: () => void
  title: string
  width?: number
  /** 모달 상자에 붙일 추가 클래스 — 'chart' 는 본문 여백을 없애 차트를 꽉 채운다. */
  className?: string
  footer?: React.ReactNode
  children: React.ReactNode
}) {
  useEffect(() => {
    if (!props.open) return
    function esc(e: KeyboardEvent) {
      if (e.key === 'Escape') props.onClose()
    }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [props.open, props.onClose])

  return (
    <AnimatePresence>
      {props.open && (
        <motion.div
          className="modal-mask center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onMouseDown={props.onClose}
        >
          <motion.div
            className={props.className ? `modal ${props.className}` : 'modal'}
            style={{ width: props.width ?? 560 }}
            initial={{ opacity: 0, scale: 0.97, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: 4 }}
            transition={{ duration: 0.18, ease: [0.22, 0.61, 0.36, 1] }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="hd">
              <b>{props.title}</b>
              <button className="ghost icon" onClick={props.onClose} title="닫기 (Esc)">
                ✕
              </button>
            </div>
            <div className="bd">{props.children}</div>
            {props.footer && <div className="actionbar">{props.footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
