"""데이터가 얼마나 묵었나 — 워터마크(high watermark) 방식.

업계 표준: "마지막으로 어디까지 받았나"를 **작은 상태 파일 하나**에 적어 두고,
다음 실행 때 그 뒤부터만 받는다. 데이터 파일을 전부 열어 보며 알아내지 않는다.

왜 중요한가 (실측 2026-08-16): 나무 봉 일·주·월봉 증분이 만지는 파일이 16,530개다.
파일을 통째로 열어 마지막 날짜를 구하면 **API 호출이 0건이어도 4.2분**이 그냥 날아간다.
워터마크 파일 하나를 읽으면 1밀리초다.

화면 표시 기준(SLO)도 여기서 정한다 — 며칠 지났는지가 아니라 **등급**을 준다.
묵은 데이터는 화면이 멀쩡히 그려져서 눈으로는 안 보이기 때문이다.
"""

import json

import pandas as pd

from src.layer1_data.freshness import (
    STALE_AFTER_DAYS,
    WARN_AFTER_DAYS,
    days_behind,
    grade,
    read_marks,
    write_mark,
)


class Test워터마크_파일:
    def test_적었다_다시_읽으면_그대로(self, tmp_path) -> None:
        write_mark("supply", "20260803", n_symbols=5478, root=tmp_path)
        got = read_marks(root=tmp_path)
        assert got["supply"]["last_date"] == "2026-08-03"
        assert got["supply"]["n_symbols"] == 5478

    def test_날짜_형식이_뭐든_같은_모양으로_적는다(self, tmp_path) -> None:
        """수급은 '20260803', 일봉은 Timestamp — 저장은 한 모양이어야 비교가 된다."""
        write_mark("a", "20260803", root=tmp_path)
        write_mark("b", pd.Timestamp("2026-08-03"), root=tmp_path)
        marks = read_marks(root=tmp_path)
        assert marks["a"]["last_date"] == marks["b"]["last_date"] == "2026-08-03"

    def test_다른_소스를_지우지_않는다(self, tmp_path) -> None:
        write_mark("supply", "20260803", root=tmp_path)
        write_mark("credit", "20260730", root=tmp_path)
        assert set(read_marks(root=tmp_path)) == {"supply", "credit"}

    def test_파일이_없으면_빈_것으로_본다(self, tmp_path) -> None:
        assert read_marks(root=tmp_path) == {}

    def test_파일이_깨졌어도_안_터진다(self, tmp_path) -> None:
        """상태 파일 하나 때문에 화면 전체가 죽으면 안 된다."""
        (tmp_path / "_freshness.json").write_text("{망가짐", encoding="utf-8")
        assert read_marks(root=tmp_path) == {}

    def test_적을_때_시각도_같이_남는다(self, tmp_path) -> None:
        write_mark("supply", "20260803", root=tmp_path)
        raw = json.loads((tmp_path / "_freshness.json").read_text(encoding="utf-8"))
        assert raw["supply"]["checked_at"]


class Test등급:
    """등급은 **장이 열린 날**로 센다 — 달력으로 세면 월요일마다 헛경고가 뜬다."""

    def test_직전_거래일까지_받았으면_괜찮다(self) -> None:
        # 목요일까지 받았고 오늘은 금요일 — 빠진 게 없다
        assert grade("2026-08-13", today=pd.Timestamp("2026-08-14")) == "ok"

    def test_주말은_안_센다(self) -> None:
        """금요일 데이터로 월요일 아침에 경고가 뜨면 안 된다 — 장이 안 열렸을 뿐이다."""
        금 = "2026-08-14"
        월 = pd.Timestamp("2026-08-17")
        assert days_behind(금, today=월) == 0
        assert grade(금, today=월) == "ok"

    def test_이틀치_빠지면_주의(self) -> None:
        # 화요일까지 받았고 오늘 금요일 → 수·목 이틀치가 없다
        assert grade("2026-08-11", today=pd.Timestamp("2026-08-14")) == "warn"

    def test_나흘치_넘게_빠지면_묵음(self) -> None:
        assert grade("2026-08-03", today=pd.Timestamp("2026-08-14")) == "stale"

    def test_받은_적이_없으면_묵음(self) -> None:
        assert grade(None, today=pd.Timestamp("2026-08-15")) == "stale"

    def test_앞선_날짜여도_안_터진다(self) -> None:
        """공시는 접수 예정일이 앞설 수 있다 — 음수로 새면 등급이 이상해진다."""
        assert days_behind("2026-08-18", today=pd.Timestamp("2026-08-14")) == 0
        assert grade("2026-08-18", today=pd.Timestamp("2026-08-14")) == "ok"

    def test_경계값이_기준과_맞물린다(self) -> None:
        today = pd.Timestamp("2026-08-14")  # 금요일
        assert grade("2026-08-11", today=today) == "warn"  # 2거래일 빠짐
        assert grade("2026-08-07", today=today) == "stale"  # 4거래일 빠짐
        assert WARN_AFTER_DAYS < STALE_AFTER_DAYS


class Test소스_훑기:
    def test_폴더에서_가장_늦은_날짜를_찾는다(self, tmp_path) -> None:
        import pandas as pd

        d = tmp_path / "supply"
        d.mkdir()
        pd.DataFrame({"stck_bsop_date": ["20260801", "20260803"]}).to_parquet(d / "005930.parquet")
        pd.DataFrame({"stck_bsop_date": ["20260731"]}).to_parquet(d / "000660.parquet")
        from src.layer1_data.freshness import scan_last_date

        last, n = scan_last_date(d, "stck_bsop_date")
        assert last == "2026-08-03"
        assert n == 2

    def test_폴더가_없으면_모른다고_한다(self, tmp_path) -> None:
        from src.layer1_data.freshness import scan_last_date

        assert scan_last_date(tmp_path / "없음", "Date") == (None, 0)

    def test_읽을_수_없는_파일은_건너뛴다(self, tmp_path) -> None:
        """파일 하나가 깨져도 나머지로 최신일을 말할 수 있어야 한다."""
        import pandas as pd

        d = tmp_path / "supply"
        d.mkdir()
        pd.DataFrame({"stck_bsop_date": ["20260803"]}).to_parquet(d / "005930.parquet")
        (d / "깨짐.parquet").write_bytes(b"not parquet")
        from src.layer1_data.freshness import scan_last_date

        last, n = scan_last_date(d, "stck_bsop_date")
        assert last == "2026-08-03"
        assert n == 1


class Test화면_보고:
    def test_워터마크만_읽고_파일은_안_연다(self, tmp_path) -> None:
        """화면 요청마다 파일 수천 개를 열면 안 된다 — 워터마크 하나만 본다."""
        import pandas as pd

        from src.layer1_data.freshness import report

        write_mark("supply", "20260803", n_symbols=5478, root=tmp_path)
        rows = report(root=tmp_path, today=pd.Timestamp("2026-08-15"))
        hit = next(r for r in rows if r["key"] == "supply")
        assert hit["last_date"] == "2026-08-03"
        assert hit["days_behind"] == 9
        assert hit["grade"] == "stale"
        assert hit["label"] and hit["why"]  # 화면이 그대로 띄울 말

    def test_받은_적_없는_소스도_줄로_나온다(self, tmp_path) -> None:
        import pandas as pd

        from src.layer1_data.freshness import report

        rows = report(root=tmp_path, today=pd.Timestamp("2026-08-15"))
        assert rows and all(r["last_date"] is None for r in rows)
        assert all(r["grade"] == "stale" for r in rows)

    def test_가장_나쁜_등급을_같이_준다(self, tmp_path) -> None:
        import pandas as pd

        from src.layer1_data.freshness import worst_grade

        write_mark("marcap", "20260814", root=tmp_path)
        write_mark("supply", "20260803", root=tmp_path)
        assert worst_grade(root=tmp_path, today=pd.Timestamp("2026-08-15")) == "stale"


class Test워터마크_다시_만들기:
    def test_훑어서_워터마크를_적는다(self, tmp_path) -> None:
        import pandas as pd

        from src.layer1_data.freshness import read_marks, refresh_marks

        (tmp_path / "derived" / "supply").mkdir(parents=True)
        pd.DataFrame({"stck_bsop_date": ["20260803"]}).to_parquet(
            tmp_path / "derived" / "supply" / "005930.parquet"
        )
        (tmp_path / "marcap" / "data").mkdir(parents=True)
        pd.DataFrame({"Date": pd.to_datetime(["2026-08-13"])}).to_parquet(
            tmp_path / "marcap" / "data" / "marcap-2026.parquet"
        )

        refresh_marks(root=tmp_path / "derived", marcap_dir=tmp_path / "marcap" / "data")
        marks = read_marks(root=tmp_path / "derived")
        assert marks["supply"]["last_date"] == "2026-08-03"
        assert marks["supply"]["n_symbols"] == 1
        assert marks["marcap"]["last_date"] == "2026-08-13"

    def test_없는_소스는_적지_않는다(self, tmp_path) -> None:
        """폴더가 아예 없는 소스에 '모름'을 적으면 화면이 헷갈린다 — 그냥 비워 둔다."""
        from src.layer1_data.freshness import read_marks, refresh_marks

        (tmp_path / "derived").mkdir()
        refresh_marks(root=tmp_path / "derived", marcap_dir=tmp_path / "없음")
        assert read_marks(root=tmp_path / "derived") == {}

    def test_오늘_이미_훑었으면_다시_안_훑는다(self, tmp_path) -> None:
        """훑기는 수십 초짜리다 — 하루 한 번이면 충분하다."""
        import pandas as pd

        from src.layer1_data.freshness import needs_rescan, write_mark

        d = tmp_path / "derived"
        d.mkdir()
        assert needs_rescan(root=d) is True
        write_mark("supply", "20260803", root=d)
        assert needs_rescan(root=d, today=pd.Timestamp.today().normalize()) is False
