# -*- coding: utf-8 -*-
"""`JsonlStore` — 줄이 만 단위로 쌓이는 파일을 위한 자리.

★ 왜 만들었는지는 `store.JsonlStore` 첫머리에 있다. 한 줄로: 18.7MB 를
  `json.load` 로 읽으면 피크가 151MB 인데 줄 단위로는 56MB 다. 2GB 서버에
  프로세스가 여섯이면 그 차이가 올릴 수 있냐 없냐를 가른다.
"""

import json

from genie_agents.store import JsonlStore


def test_없는_파일은_빈_것이다(tmp_path):
    s = JsonlStore(tmp_path / "없다.jsonl")
    assert not s.exists()
    assert list(s.stream()) == []
    assert s.tail(10) == []


def test_덧붙이고_읽는다(tmp_path):
    s = JsonlStore(tmp_path / "a.jsonl")
    s.append({"id": "1", "text": "첫 줄"})
    s.append({"id": "2", "text": "둘째 줄"})

    assert [r["id"] for r in s.stream()] == ["1", "2"]


def test_덧붙이기는_앞을_안_건드린다(tmp_path):
    """★ 이게 이 자리의 요점이다 — 말 한 마디마다 전부를 다시 쓰지 않는다."""
    p = tmp_path / "a.jsonl"
    s = JsonlStore(p)
    s.append({"id": "1"})
    앞 = p.read_bytes()
    s.append({"id": "2"})

    assert p.read_bytes().startswith(앞), "앞부분이 그대로여야 한다"


def test_깨진_줄_하나가_나머지를_막지_않는다(tmp_path):
    """★ 덧붙이는 도중에 죽으면 마지막 줄이 반만 남을 수 있다. 그 한 줄 때문에
    기억 전체를 못 읽으면 안 된다 — `from_dict` 가 모르는 키를 버리는 것과 같다."""
    p = tmp_path / "a.jsonl"
    p.write_text('{"id": "1"}\n{"id": 반쪽\n{"id": "3"}\n', encoding="utf-8")

    assert [r["id"] for r in JsonlStore(p).stream()] == ["1", "3"]


def test_빈_줄은_건너뛴다(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"id": "1"}\n\n\n{"id": "2"}\n', encoding="utf-8")

    assert len(list(JsonlStore(p).stream())) == 2


def test_꼬리만_읽는다(tmp_path):
    s = JsonlStore(tmp_path / "a.jsonl")
    for i in range(100):
        s.append({"i": i})

    assert [r["i"] for r in s.tail(3)] == [97, 98, 99]
    assert s.tail(0) == []


def test_다시_쓰기는_원자적이고_전부를_바꾼다(tmp_path):
    """고치거나 지울 때만 부른다. 덧붙이는 자리가 아니다."""
    p = tmp_path / "a.jsonl"
    s = JsonlStore(p)
    s.append({"id": "1", "text": "옛것"})
    s.append({"id": "2", "text": "그대로"})

    s.rewrite([{"id": "1", "text": "고친 것"}, {"id": "2", "text": "그대로"}])

    rows = list(s.stream())
    assert [r["text"] for r in rows] == ["고친 것", "그대로"]
    assert not list(p.parent.glob("*.tmp")), "임시 파일이 남으면 안 된다"


def test_한글이_그대로_들어간다(tmp_path):
    """`ensure_ascii=False` 다 — 사람이 파일을 열어 볼 자리이기도 하다."""
    p = tmp_path / "a.jsonl"
    JsonlStore(p).append({"text": "오빠, 나 여기 있어"})

    assert "오빠, 나 여기 있어" in p.read_text(encoding="utf-8")


def test_줄마다_한_번의_write_다(tmp_path):
    """★ 나눠 쓰면 다른 프로세스가 읽는 중에 반쪽 줄을 본다.

    줄 수와 개행 수가 같은지로 본다 — 줄 안에 개행이 새면 스트림이 어긋난다.
    """
    p = tmp_path / "a.jsonl"
    s = JsonlStore(p)
    s.append({"text": "여러\n줄\n짜리"})
    s.append({"text": "다음"})

    raw = p.read_text(encoding="utf-8")
    assert raw.count("\n") == 2, "기록된 개행은 줄 끝 둘뿐이어야 한다"
    assert [r["text"] for r in s.stream()] == ["여러\n줄\n짜리", "다음"]
    assert json.loads(raw.splitlines()[0])["text"] == "여러\n줄\n짜리"
