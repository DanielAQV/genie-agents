"""커서 — 어디까지 읽었나.

작은 파일이지만 여기가 새면 **덜 읽고 다 읽은 척한다.** 그게 이 물건에서
제일 나쁜 고장이라 시험을 붙여 둔다.
"""

from __future__ import annotations

import pytest

from genie_agents.cursors import Cursors


@pytest.fixture
def at(tmp_path):
    return Cursors(tmp_path)


def test_없으면_빈_문자열이다(at):
    """★ 기본값을 여기서 안 정한다. 처음 도는 날 얼마나 거슬러 올라갈지는
    소스마다 다르고, 여기서 정하면 그 기본값들이 이 파일에 쌓인다."""
    assert at.get("slack:C01") == ""
    assert at.get("slack:C01", "1756.0") == "1756.0"


def test_옮긴_것이_남는다(tmp_path):
    a = Cursors(tmp_path)
    a.set("slack:C01", "1756.9")
    assert Cursors(tmp_path).get("slack:C01") == "1756.9"


def test_빈_값으로는_안_옮긴다(at):
    """★ 빈 방을 긁은 날 `max()` 가 빈 문자열을 준다. 그걸로 커서를 덮으면
    다음날 처음부터 다시 읽고, 그게 조용히 도는 동안 API 상한만 먹는다."""
    at.set("slack:C01", "1756.9")
    at.set("slack:C01", "")
    assert at.get("slack:C01") == "1756.9"


def test_같은_값이면_파일을_안_쓴다(at, tmp_path):
    at.set("slack:C01", "1756.9")
    쓴때 = (tmp_path / "cursors.json").stat().st_mtime_ns
    at.set("slack:C01", "1756.9")
    assert (tmp_path / "cursors.json").stat().st_mtime_ns == 쓴때


def test_값을_해석하지_않는다(at):
    """소스마다 커서의 종류가 다르다 — epoch 문자열 · historyId · deltaLink URL.
    여기서 크고 작음을 따지려 들면 소스마다 다른 비교가 이 파일에 쌓인다."""
    at.set("gmail", "9")
    at.set("gmail", "10")          # 문자열로는 "10" < "9" 다. 그래도 옮긴다
    assert at.get("gmail") == "10"


def test_지우면_처음부터_다시_읽는다(at):
    at.set("slack:C01", "1756.9")
    assert at.drop("slack:C01") is True
    assert at.drop("slack:C01") is False
    assert at.get("slack:C01") == ""


def test_자리끼리_안_섞인다(at):
    at.set("slack:C01", "1")
    at.set("slack:D02", "2")
    assert at.all() == {"slack:C01": "1", "slack:D02": "2"}
    assert len(at) == 2
