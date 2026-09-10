"""재무제표 다중회사 수집 — 전체본을 안 덮는지, 안 바뀐 건 안 쓰는지."""

from __future__ import annotations

import pandas as pd


def _multi_rows(code: str, rcept: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock_code": [code, code],
            "rcept_no": [rcept, rcept],
            "sj_div": ["BS", "IS"],
            "account_nm": ["자산총계", "매출액"],
            "thstrm_amount": ["100", "200"],
            "fs_div": ["CFS", "CFS"],
            "_src": ["fnlttMultiAcnt", "fnlttMultiAcnt"],
        }
    )


def test_full_statements_are_never_overwritten(monkeypatch, tmp_path) -> None:
    """전체 재무제표는 계정이 훨씬 많다 — 주요계정으로 덮으면 잃는다."""
    import scripts.backfill_dart_multi as multi

    monkeypatch.setattr(multi.single, "OUT_DIR", tmp_path)
    path = tmp_path / "005930" / "2024Q4.parquet"
    path.parent.mkdir(parents=True)
    # 전체본에는 `_src` 가 없다 — 그게 두 쪽을 가르는 표시다
    pd.DataFrame({"account_id": ["ifrs-full_Assets"], "thstrm_amount": ["999"]}).to_parquet(
        path, index=False
    )

    saved, kept = multi.save_period(_multi_rows("005930", "A"), 2024, 4)

    assert (saved, kept) == (0, 1)
    assert pd.read_parquet(path)["thstrm_amount"].tolist() == ["999"], "전체본이 그대로여야 한다"


def test_unchanged_report_is_not_rewritten(monkeypatch, tmp_path) -> None:
    """접수번호가 같으면 내용이 그대로다 — 회차마다 파일 수천 개를 헛되이 건드리지 않는다."""
    import scripts.backfill_dart_multi as multi

    monkeypatch.setattr(multi.single, "OUT_DIR", tmp_path)
    assert multi.save_period(_multi_rows("000020", "A"), 2024, 4) == (1, 0)
    assert multi.save_period(_multi_rows("000020", "A"), 2024, 4) == (0, 1), "같은 접수번호"
    assert multi.save_period(_multi_rows("000020", "B"), 2024, 4) == (1, 0), "정정되면 덮는다"


def test_amounts_lose_their_commas_and_keep_the_filing_date(monkeypatch) -> None:
    """쉼표가 남으면 읽개가 숫자로 못 읽고, 접수일이 없으면 미래 데이터 훔쳐보기를 못 막는다."""
    import scripts.backfill_dart_multi as multi

    class Fake:
        @staticmethod
        def get(*_a, **_k):
            class R:
                @staticmethod
                def json():
                    return {
                        "status": "000",
                        "list": [
                            {
                                "stock_code": "5930",
                                "rcept_no": "20250310000123",
                                "thstrm_amount": "556,691,161,000,000",
                                "account_nm": "자산총계",
                            }
                        ],
                    }

            return R()

    monkeypatch.setattr(multi.single, "session", lambda: Fake())
    monkeypatch.setattr(multi.single.THROTTLE, "wait", lambda: None)
    monkeypatch.setattr(multi.single, "note_result", lambda _ok: None)

    status, frame = multi.ask("k", ["00126380"], 2024, "11011")

    assert status == "000"
    assert frame["thstrm_amount"].iloc[0] == "556691161000000"
    assert frame["rcept_dt"].iloc[0] == "20250310"
    assert frame["stock_code"].iloc[0] == "005930", "여섯 자리로 채워야 한다"
