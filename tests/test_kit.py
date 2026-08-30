"""도구를 이름으로 켠다 — 켠 것만 있고, 못 켜면 뜨기 전에 죽는다.

이 묶음이 있는 이유는 하나다. 도구를 에이전트 안에 두면 같은 이름의 도구가
**두 벌**이 되고, 한쪽만 고치면 이름은 같은데 다르게 작동하는 것이 남는다.
골격을 나눠 쓰는 이유를 정면으로 무너뜨린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from genie_agents import env, notes, reminders, runner, world
from genie_agents.kit import CATALOG
from genie_agents.spec import FILE, BadSpec, load
from genie_agents.tools import MissingContext, Tool, Toolbox, UnknownTool


class Ctx:
    """도구가 요구하는 것만 든 런타임."""

    def __init__(self, root):
        self.reminders = reminders.ReminderStore(root)
        self.notes = notes.NoteStore(root)
        self.world = world.WorldFeed(root)


@pytest.fixture
def ctx(tmp_path):
    return Ctx(tmp_path)


# ── 등록소 ───────────────────────────────────────────────────────────
def test_모르는_이름은_켤_때_걸린다():
    """부를 때 걸리면 그 턴을 날린다."""
    with pytest.raises(UnknownTool, match="reminder_st"):
        Toolbox(CATALOG, ["reminder_st"])


def test_켠_것만_있다(ctx):
    box = Toolbox(CATALOG, ["reminder_set"])
    assert [t["name"] for t in box.specs()] == ["reminder_set"]
    with pytest.raises(UnknownTool):
        box.call(ctx, "note_write", text="x")


def test_켠_순서를_지킨다():
    """목록 순서가 바뀌면 앞쪽 캐시가 통째로 무효다."""
    names = ["note_write", "reminder_set", "note_recall"]
    assert [t["name"] for t in Toolbox(CATALOG, names).specs()] == names


def test_런타임에_없는_것을_요구하면_켤_때_죽는다():
    """반쯤 켜진 에이전트가 제일 나쁘다."""
    box = Toolbox(CATALOG, ["reminder_set"])

    class 빈것:
        pass

    assert box.check(빈것()) == [
        "reminder_set 은(는) `reminders` 가 필요한데 런타임에 없다"
    ]
    with pytest.raises(MissingContext, match="reminders"):
        box.bind(빈것())


def test_설명문은_그_존재가_갈아_끼운다(ctx):
    """말투가 곧 인격이다. 무엇을 하는가는 골격이 정하고, 어떻게 설명되는가는
    그 존재가 정한다."""
    box = Toolbox(CATALOG, ["note_write"], describe={"note_write": "적어 둘 것."})
    spec = box.specs()[0]
    assert spec["description"] == "적어 둘 것."
    # 하는 일은 그대로다
    assert spec["input_schema"]["properties"].keys() == {"text", "tags"}


def test_없는_도구를_설명하려_들면_걸린다():
    with pytest.raises(UnknownTool, match="note_recall"):
        Toolbox(CATALOG, ["note_write"], describe={"note_recall": "x"})


# ── 자리와 게이트 ────────────────────────────────────────────────────
def test_자리로_거른다(ctx):
    only_wake = Tool(name="깨어남전용", description="d", run=lambda c: {},
                     scopes=frozenset({"wake"}))
    box = Toolbox({**CATALOG, "깨어남전용": only_wake}, ["reminder_set", "깨어남전용"])
    assert [t["name"] for t in box.specs("wake")] == ["reminder_set", "깨어남전용"]
    assert [t["name"] for t in box.specs("conversation")] == ["reminder_set"]


def test_잔고가_마르면_목록에서_빠진다(ctx):
    """목록을 흔드는 유일한 사유다 — "지금은 못 쓴다" 가 아니라 "지금은 가진
    게 아니다" 라서."""
    from genie_agents.gate import GateBlocked, TalentGate

    비싼 = Tool(name="비싼것", description="d", run=lambda c: {"했다": True}, gated=True)
    box = Toolbox({**CATALOG, "비싼것": 비싼}, ["reminder_set", "비싼것"])

    class 마른지갑:
        def balance(self): return 0

    gate = TalentGate(마른지갑(), box.gated)
    assert [t["name"] for t in box.specs(gate=gate)] == ["reminder_set"]
    # 목록을 우회한 호출도 막힌다
    with pytest.raises(GateBlocked):
        box.call(ctx, "비싼것", gate=gate)


# ── 실제로 도는가 ────────────────────────────────────────────────────
def test_리마인더를_걸고_보고_지운다(ctx):
    box = Toolbox(CATALOG, ["reminder_set", "reminder_list", "reminder_done"])
    got = box.call(ctx, "reminder_set", text="물어보기", when="2026-12-24")
    assert got["text"] == "물어보기"

    listed = box.call(ctx, "reminder_list")["reminders"]
    assert [r["text"] for r in listed] == ["물어보기"]

    assert box.call(ctx, "reminder_done", id=got["id"])["다음"] == "끝남"
    assert box.call(ctx, "reminder_list")["reminders"] == []


def test_없는_약속을_끝내려_하면_예외가_아니라_답이_온다(ctx):
    """막힌 것을 예외로 던지면 판단 루프가 거기서 끊긴다."""
    box = Toolbox(CATALOG, ["reminder_done"])
    assert box.call(ctx, "reminder_done", id="없다")["결과"] == "그런 약속이 없다"


def test_남기고_찾는다(ctx):
    box = Toolbox(CATALOG, ["note_write", "note_recall"])
    box.call(ctx, "note_write", text="사용자는 아침에 안 깨운다", tags=["리듬"])
    box.call(ctx, "note_write", text="관계 밖의 것도 본다")

    assert len(box.call(ctx, "note_recall")["notes"]) == 2
    got = box.call(ctx, "note_recall", query="아침")["notes"]
    assert [n["text"] for n in got] == ["사용자는 아침에 안 깨운다"]
    assert box.call(ctx, "note_recall", tag="리듬")["notes"][0]["tags"] == ["리듬"]


def test_못_찾은_것과_안_남긴_것을_가른다(ctx):
    """빈 목록만 주면 둘이 구분이 안 된다."""
    box = Toolbox(CATALOG, ["note_write", "note_recall"])
    assert box.call(ctx, "note_recall", query="x")["전체"] == 0
    box.call(ctx, "note_write", text="있다")
    out = box.call(ctx, "note_recall", query="없는말")
    assert out["notes"] == [] and out["전체"] == 1


# ── 폴더에서 켜기 ────────────────────────────────────────────────────
TOML = """
[agent]
id = "scribe"
adapter = "anthropic"
model = "test-model"

[prompt]
instructions = "prompt.md"

[tools]
enable = ["reminder_set", "note_write"]
[tools.describe]
note_write = "적어 둬라. 짧게."
"""


@dataclass
class Text:
    text: str
    type: str = "text"


@dataclass
class Usage:
    input_tokens: int = 1
    output_tokens: int = 1
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Resp:
    content: list
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


class FakeClient:
    def __init__(self):
        self.seen = []
        self.messages = self

    def create(self, **kw):
        self.seen.append(kw)
        return Resp([Text("네.")])


def folder(tmp_path, toml=TOML):
    d = tmp_path / "scribe"
    d.mkdir()
    (d / FILE).write_text(toml, encoding="utf-8")
    (d / "prompt.md").write_text("짧게.", encoding="utf-8")
    return d


@pytest.fixture
def clean(monkeypatch):
    import os

    monkeypatch.delenv(env.VAR, raising=False)
    for k in list(os.environ):
        if k.endswith("_ROOT"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_파일에_이름만_적으면_도구가_붙는다(clean, tmp_path):
    """파이썬 한 줄 없이 도구를 켠다. 이 단계 전부가 이 한 줄이다."""
    client = FakeClient()
    runner.Agent(load(folder(tmp_path)), client=client).run("있나")
    assert [t["name"] for t in client.seen[0]["tools"]] == ["reminder_set", "note_write"]
    assert client.seen[0]["tools"][1]["description"] == "적어 둬라. 짧게."


def test_상태는_그_에이전트_자리에_앉는다(clean, tmp_path):
    d = folder(tmp_path)
    agent = runner.Agent(load(d), client=FakeClient())
    agent.session.call("reminder_set", text="x", when="2026-12-24")
    assert (d / ".scribe" / "reminders.json").exists()


def test_켠_도구와_자기_도구를_섞을_수_없다(clean, tmp_path):
    """섞으면 같은 이름이 둘일 때 어느 쪽이 도는지 코드를 읽어야 안다."""
    d = folder(tmp_path, TOML.replace("[tools.describe]", 'module = "tools.py"\n[tools.describe]'))
    (d / "tools.py").write_text("def tools(s): return []\ndef call(n, **a): return {}\n",
                                encoding="utf-8")
    with pytest.raises(BadSpec, match="둘 중 하나만"):
        runner.Agent(load(d), client=FakeClient())


def test_check_가_모르는_도구를_잡는다(clean, tmp_path):
    d = folder(tmp_path, TOML.replace('"reminder_set"', '"reminder_st"'))
    assert any("reminder_st" in p for p in runner.check(d))
