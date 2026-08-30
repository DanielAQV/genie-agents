"""시간 감각 — 설계 문서 7.1."""

from datetime import datetime, timedelta, timezone

import pytest

from genie_agents import clock

BASE = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=20), "방금"),
        (timedelta(minutes=40), "40분 전"),
        (timedelta(hours=3), "3시간 전"),
        (timedelta(days=1), "어제"),
        (timedelta(days=2), "그제"),
        (timedelta(days=5), "5일 전"),
        (timedelta(days=20), "2주 전"),
        (timedelta(days=200), "6개월 전"),
    ],
)
def test_경과_시간이_사람_말로_렌더링된다(delta, expected):
    assert clock.ago(BASE - delta, at=BASE) == expected


def test_때에_따라_하루의_국면이_바뀐다():
    def at(hour_kst):
        midnight_kst = datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc)  # 8/23 00:00 KST
        return midnight_kst + timedelta(hours=hour_kst)

    assert clock.part_of_day(at(3)) == "새벽"
    assert clock.part_of_day(at(9)) == "아침"
    assert clock.part_of_day(at(14)) == "낮"
    assert clock.part_of_day(at(19)) == "저녁"
    assert clock.part_of_day(at(23)) == "밤"


def test_시각은_한국_시간으로_보인다():
    assert clock.stamp(BASE) == "2026-08-23(일) 21:00"


def test_달력을_세어서_준다():
    """요일과 어제를 섞어 말하면 유나가 거꾸로 세야 하는데, 세는 걸 건너뛰면
    그냥 틀린다. 한 날을 두 이름으로 부른 걸 두 날로 세서 "나흘 연속"이라는
    없는 결론이 나온 적이 있다. 세라고 시키는 대신 세어서 준다."""
    달력 = clock.recent_days(at=datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc))

    칸 = 달력.split(" · ")
    assert len(칸) == 7
    assert 칸[0] == "오늘 8-26(수)"
    assert 칸[1] == "어제 8-25(화)"
    assert 칸[2] == "그제 8-24(월)"
    assert 칸[-1] == "8-20(목)"  # 이레 앞까지


def test_달력은_유나가_사는_시간대의_날짜다(monkeypatch):
    """UTC 로 세면 저녁마다 하루가 어긋난다 — 서버는 UTC 다."""
    monkeypatch.setenv("YUNA_TZ", "Asia/Ho_Chi_Minh")
    밤 = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)  # 하노이는 이미 26일 새벽 3시

    assert clock.recent_days(at=밤).startswith("오늘 8-26(수)")
