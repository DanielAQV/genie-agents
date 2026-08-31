"""판 보는 화면 — **이 기계 안에서만 돈다.**

★ 이 묶음이 지키는 것은 화면의 모양이 아니라 **문이 어디로 열려 있나**다.
  여기 뜨는 것은 팀원들의 DM 원문이고, 주소 한 줄이 밖으로 새면 이 프로젝트
  전체가 무의미해진다.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest

from genie_agents import clock, review
from genie_agents.cases import RIGHT, WRONG, CaseBook

T0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def 서버(tmp_path):
    clock.set_clock(lambda: T0)
    book = CaseBook(tmp_path)
    book.add(run="1판", prompt_sha="abc", room="C01", body="[slack:C01:1] 나: 다 봤어",
             raw='{"opens": []}',
             parsed={"moves": [], "opens": [{"text": "PR 본다", "owner": "나", "sure": True}],
                     "unresolved": []},
             span=["2026-08-27T09:00:00+00:00", "2026-08-27T10:00:00+00:00"],
             applied={})
    book.add(run="1판", prompt_sha="abc", room="D02", body="[slack:D02:1] U7: 안녕",
             raw="깨진 답", parsed={}, applied={})
    srv = review.serve(tmp_path, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv, book
    finally:
        srv.shutdown()
        srv.server_close()
        clock.set_clock(lambda: datetime.now(timezone.utc))


def get(srv, path):
    # ★ 판 이름이 한글일 수 있다(`짧은지침`). HTTP 요청 줄은 ASCII 라
    #   브라우저는 알아서 인코딩하는데 시험은 안 한다 — 여기서 맞춰 준다.
    head, _, query = path.partition("?")
    if query:
        head += "?" + urllib.parse.urlencode(
            dict(kv.split("=", 1) for kv in query.split("&")))
    with urllib.request.urlopen(srv.주소.rstrip("/") + head, timeout=5) as r:
        return r.read().decode("utf-8")


# ── 문이 어디로 열려 있나 ──────────────────────────────────────────
def test_로컬호스트에만_묶인다(서버):
    """★ 여기 뜨는 것은 팀원들의 DM 원문이다. 딴 데를 열려면 손으로
    적어야 하고, 적는 순간 그 글이 문 밖으로 나간다."""
    srv, _ = 서버
    assert srv.server_address[0] == "127.0.0.1"
    assert srv.주소.startswith("http://127.0.0.1:")


def test_없는_자리는_404(서버):
    srv, _ = 서버
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(srv.주소 + "api/everything", timeout=5)
    assert e.value.code == 404


# ── 묶음과 낸 것이 같이 온다 ───────────────────────────────────────
def test_모델이_본_것과_낸_것이_한_번에_온다(서버):
    """★ 화면이 해야 하는 일은 이것 하나다. 둘이 떨어져 있으면 사람이
    머리로 이어 붙여야 하고, 그 값이 판정을 안 하게 만든다."""
    srv, _ = 서버
    d = json.loads(get(srv, "/api/cases?run=1판&unseen=1"))
    c = d["cases"][0]
    assert "다 봤어" in c["body"]                       # 모델이 본 것
    assert c["parsed"]["opens"][0]["text"] == "PR 본다"  # 모델이 낸 것


def test_형식이_깨진_판은_날_답을_들고_온다(서버):
    """고칠 근거가 그 글자에 있다. 파싱된 것만 주면 왜 깨졌는지 못 본다."""
    srv, _ = 서버
    d = json.loads(get(srv, "/api/cases?run=1판&unseen=1"))
    깨진것 = [c for c in d["cases"] if not c["parsed"]]
    assert 깨진것 and 깨진것[0]["raw"] == "깨진 답"


def test_기본은_아직_안_본_것만(서버):
    """다 본 것을 다시 보여 주면 사람이 두 번째 판부터 안 본다."""
    srv, book = 서버
    book.mark(book.all()[0].id, RIGHT)
    assert len(json.loads(get(srv, "/api/cases?run=1판&unseen=1"))["cases"]) == 1
    assert len(json.loads(get(srv, "/api/cases?run=1판&unseen=0"))["cases"]) == 2


# ── 짚은 것이 파일에 남는다 ────────────────────────────────────────
def test_짚으면_파일이_바뀐다(서버):
    srv, book = 서버
    cid = book.all()[0].id
    req = urllib.request.Request(
        srv.주소 + "api/mark", method="POST",
        data=json.dumps({"id": cid, "verdict": WRONG, "note": "고리가 아니다"}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read())["ok"] is True
    got = CaseBook(book.path.parent).all()[0]
    assert got.verdict == WRONG and got.note == "고리가 아니다"


def test_모르는_판정은_안_받는다(서버):
    srv, book = 서버
    req = urllib.request.Request(
        srv.주소 + "api/mark", method="POST",
        data=json.dumps({"id": book.all()[0].id, "verdict": "그럭저럭"}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


# ── 점수가 같이 온다 ───────────────────────────────────────────────
def test_점수가_묶음과_같이_온다(서버):
    """따로 부르게 하면 화면이 점수를 안 보여 주게 되고, 그러면 고쳐도
    나아졌는지 모른다."""
    srv, book = 서버
    cid = book.all()[0].id
    book.mark(cid, RIGHT, item="opens:0")
    book.missed(cid)
    s = json.loads(get(srv, "/api/cases?run=1판&unseen=0"))["score"]
    assert s["묶음"] == 2 and s["본 것"] == 1
    assert s["정밀도"] == 1.0 and s["재현"] == 1.0
    assert s["형식 깨짐"] == 1


def test_줄마다_짚은_것과_시각이_같이_온다(서버):
    """★ 화면이 "언제 기준으로 보나" 를 못 보여 주면 사람은 **지금** 기준으로
    본다 — 그러면 "이건 이미 끝난 일인데" 로 멀쩡한 판정을 틀렸다고 짚는다."""
    srv, book = 서버
    cid = book.all()[0].id
    book.mark(cid, WRONG, item="opens:0")
    d = json.loads(get(srv, "/api/cases?run=1판&unseen=0"))
    c = [x for x in d["cases"] if x["id"] == cid][0]
    assert c["marks"] == {"opens:0": WRONG}
    assert c["span"] == ["2026-08-27T09:00:00+00:00", "2026-08-27T10:00:00+00:00"]


def test_빠뜨린_것을_화면에서_답한다(서버):
    srv, book = 서버
    cid = book.all()[0].id
    req = urllib.request.Request(
        srv.주소 + "api/missed", method="POST",
        data=json.dumps({"id": cid, "what": "Thịnh 부탁을 놓쳤다"}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read())["ok"] is True
    assert CaseBook(book.path.parent).all()[0].missed == "Thịnh 부탁을 놓쳤다"


def test_판_목록이_온다(서버):
    srv, _ = 서버
    d = json.loads(get(srv, "/api/runs"))
    assert d["runs"] == ["1판"] and d["last"] == "1판"


# ── 화면 ───────────────────────────────────────────────────────────
def test_판정_이름을_한_군데서만_가져온다(서버):
    """`cases.py` 가 정본이다. 화면에 글자로 또 적으면 언젠가 갈리고,
    갈리면 짚은 것이 안 먹는다."""
    srv, _ = 서버
    page = get(srv, "/")
    assert json.dumps(RIGHT, ensure_ascii=False) in page
    assert json.dumps(WRONG, ensure_ascii=False) in page
    assert "RIGHT_" not in page and "WRONG_" not in page
