// 한국어 로케일 — 엔진(klinecharts)이 캔들 툴팁에 쓰는 라벨.
//
// 예전엔 KLineChart Pro 의 `loadLocales` 로 Pro UI(지표 메뉴·설정창·그리기 툴바) 문구도
// 같이 번역했는데, Pro 껍데기를 벗으면서(ADR-0005 개정 2026-08-07) 그 화면이 사라졌다.
// 지금 도구 막대는 ProChart 가 직접 그리고 라벨도 거기 한국어로 박혀 있다.
import { registerLocale } from 'klinecharts'

let registered = false

export function registerKoreanLocale(): void {
  if (registered) return
  registered = true

  // 코어(캔들 툴팁 라벨)
  registerLocale('ko-KR', {
    time: '시간',
    open: '시가',
    high: '고가',
    low: '저가',
    close: '종가',
    volume: '거래량',
    change: '등락',
    turnover: '거래대금',
  })

}
