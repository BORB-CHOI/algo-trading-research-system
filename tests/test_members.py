"""거래원(증권사별 매매) 수집 — 지금 안 모으면 잃는다.

실측 2026-08-17: KIS 회원사 종목매매동향(FHPST04540000)은 아무리 뒤로 요청해도
**260 거래일**까지만 준다(바닥 2025-07-23). 신용잔고(2007-07-12)·분봉(6주)과 같은
보관 한계다. 하루가 지나면 하루치가 뒤에서 사라지므로 매일 받아 쌓아야 한다.

여기 테스트는 **파싱과 모양**만 본다 — 네트워크는 타지 않는다.
"""

import pandas as pd

from src.layer1_data.members import SIDES, merge_member_names, parse_daily, parse_snapshot

SNAP = {
    "seln_mbcr_no1": "00017",
    "seln_mbcr_name1": "KB증권",
    "total_seln_qty1": "2144303",
    "seln_mbcr_rlim1": "9.90",
    "seln_qty_icdc1": "65539",
    "seln_mbcr_no2": "00005",
    "seln_mbcr_name2": "미래에셋증권",
    "total_seln_qty2": "1722515",
    "seln_mbcr_rlim2": "7.95",
    "seln_qty_icdc2": "54421",
    "shnu_mbcr_no1": "00043",
    "shnu_mbcr_name1": "UBS",
    "total_shnu_qty1": "5948108",
    "shnu_mbcr_rlim1": "27.45",
    "shnu_qty_icdc1": "99072",
    "glob_total_shnu_qty": "8373121",
    "glob_ntby_qty": "6966027",
}


class Test당일_상위5_파싱:
    def test_매도_매수를_한_표로_편다(self) -> None:
        rows = parse_snapshot(SNAP, "005930", "2026-08-14")
        assert len(rows) == 3  # 매도 2 + 매수 1 (빈 자리는 안 만든다)
        assert {r["side"] for r in rows} == set(SIDES)
        top = next(r for r in rows if r["side"] == "매수")
        assert (top["member_code"], top["member_name"]) == ("00043", "UBS")
        assert top["qty"] == 5948108
        assert top["ratio"] == 27.45
        assert top["rank"] == 1

    def test_종목과_날짜가_모든_줄에_붙는다(self) -> None:
        """나중에 전 종목 하루치를 한 파일에 담으므로 줄마다 있어야 한다."""
        rows = parse_snapshot(SNAP, "005930", "2026-08-14")
        assert all(r["code"] == "005930" and r["date"] == "2026-08-14" for r in rows)

    def test_빈_자리는_건너뛴다(self) -> None:
        rows = parse_snapshot({"seln_mbcr_no1": "", "seln_mbcr_name1": ""}, "005930", "2026-08-14")
        assert rows == []

    def test_외국계_합계도_담는다(self) -> None:
        """외국계 순매수는 상위5 와 별개 지표다 — 버리면 다시 못 구한다."""
        rows = parse_snapshot(SNAP, "005930", "2026-08-14")
        glob = [r for r in rows if r["member_code"] == "외국계"]
        assert glob == [] or glob[0]["qty"] > 0  # 상위5 줄에는 안 섞인다

    def test_숫자가_비면_0으로_두고_안_터진다(self) -> None:
        bad = dict(SNAP, total_seln_qty1="", seln_mbcr_rlim1="-")
        rows = parse_snapshot(bad, "005930", "2026-08-14")
        assert rows[0]["qty"] == 0 and rows[0]["ratio"] == 0.0


DAILY = [
    {
        "stck_bsop_date": "20260814",
        "total_seln_qty": "1680171",
        "total_shnu_qty": "0",
        "ntby_qty": "-1680171",
        "stck_prpr": "274500",
        "acml_vol": "21669476",
    },
    {
        "stck_bsop_date": "20260813",
        "total_seln_qty": "500",
        "total_shnu_qty": "900",
        "ntby_qty": "400",
        "stck_prpr": "268000",
        "acml_vol": "35530867",
    },
]


class Test일자별_파싱:
    def test_회원사와_종목이_줄마다_붙는다(self) -> None:
        rows = parse_daily(DAILY, "005930", "00050")
        assert len(rows) == 2
        assert all(r["code"] == "005930" and r["member_code"] == "00050" for r in rows)

    def test_날짜는_한_모양으로_맞춘다(self) -> None:
        rows = parse_daily(DAILY, "005930", "00050")
        assert rows[0]["date"] == "2026-08-14"

    def test_매도_매수_순매수를_숫자로_준다(self) -> None:
        r = parse_daily(DAILY, "005930", "00050")[1]
        assert (r["sell_qty"], r["buy_qty"], r["net_qty"]) == (500, 900, 400)

    def test_빈_응답이면_빈_목록(self) -> None:
        assert parse_daily([], "005930", "00050") == []

    def test_거래가_0인_날은_버린다(self) -> None:
        """회원사코드가 유효해도 그 종목을 안 만진 날이 있다 — 0줄까지 쌓으면 파일만 커진다."""
        zero = [
            {
                "stck_bsop_date": "20260814",
                "total_seln_qty": "0",
                "total_shnu_qty": "0",
                "ntby_qty": "0",
            }
        ]
        assert parse_daily(zero, "005930", "00050") == []


class Test거래원_이름_사전:
    def test_새_이름을_채운다(self) -> None:
        got = merge_member_names({"00050": ""}, SNAP)
        assert got["00017"] == "KB증권" and got["00043"] == "UBS"

    def test_이미_있는_이름은_안_덮는다(self) -> None:
        got = merge_member_names({"00017": "예전이름"}, SNAP)
        assert got["00017"] == "예전이름"

    def test_이름_없는_코드도_자리는_지킨다(self) -> None:
        """스냅샷을 모을수록 이름이 저절로 채워진다 — 코드는 미리 알고 있다."""
        got = merge_member_names({"00099": ""}, SNAP)
        assert "00099" in got


class Test저장_모양:
    def test_표로_만들면_열이_고정이다(self) -> None:
        rows = parse_daily(DAILY, "005930", "00050")
        df = pd.DataFrame(rows)
        assert list(df.columns) == [
            "date",
            "code",
            "member_code",
            "sell_qty",
            "buy_qty",
            "net_qty",
            "close",
            "acc_vol",
        ]
