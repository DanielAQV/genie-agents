"""폴더 하나가 에이전트 하나 — 정의가 실제로 도는가.

여기서 증명하려는 것은 하나다. **파이썬을 안 쓰고 파일 넷만 두면 도는가.**
그게 안 되면 이 골격은 여전히 개발자만 쓸 수 있는 것이고, 나눠 쓸 이유가
절반 사라진다.

프리픽스가 프로세스에 걸리므로(`env.use`) 여기 시험은 프로세스를 밟는다.
그래서 환경을 매번 되돌린다 — 안 그러면 먼저 돈 시험이 정한 프리픽스를 다음
시험이 물려받아, 통과가 **순서에 딸리게** 된다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from genie_agents import env, runner
from genie_agents.policy import Policy
from genie_agents.spec import FILE, BadSpec, load


# ── 흉내 ─────────────────────────────────────────────────────────────
@dataclass
class Text:
    text: str
    type: str = "text"


@dataclass
class Use:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Resp:
    content: list
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


class FakeClient:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.seen = []
        self.messages = self

    def create(self, **kw):
        self.seen.append(kw)
        return self.queue.pop(0) if self.queue else Resp([Text("끝")])


TOML = """
[agent]
id = "scribe"
adapter = "anthropic"
model = "test-model"
timezone = "Asia/Seoul"

[prompt]
instructions = "prompt.md"
identity = "identity.md"
"""


@pytest.fixture
def clean(monkeypatch):
    """프로세스를 되돌린다. 프리픽스는 한 번 걸리면 안 풀린다."""
    monkeypatch.delenv(env.VAR, raising=False)
    for k in list(os.environ):
        if k.endswith("_ROOT"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def folder(tmp_path, toml=TOML, tools=None, identity="나는 서기다."):
    d = tmp_path / "scribe"
    d.mkdir()
    (d / FILE).write_text(toml, encoding="utf-8")
    (d / "prompt.md").write_text("짧게 답한다.", encoding="utf-8")
    if identity is not None:
        (d / "identity.md").write_text(identity, encoding="utf-8")
    if tools is not None:
        (d / "tools.py").write_text(tools, encoding="utf-8")
    return d


# ── 읽기 ─────────────────────────────────────────────────────────────
def test_폴더_하나를_읽는다(tmp_path):
    s = load(folder(tmp_path))
    assert s.id == "scribe"
    assert s.prefix == "SCRIBE"  # 안 적으면 id 를 대문자로
    assert s.instructions == "짧게 답한다."
    assert s.identity == "나는 서기다."


def test_안_적은_것은_골격_기본값이다(tmp_path):
    assert load(folder(tmp_path)).policy == Policy()


def test_적은_것만_갈린다(tmp_path):
    s = load(folder(tmp_path, TOML + "\n[policy]\nmax_turns = 3\n"))
    assert s.policy.max_turns == 3
    assert s.policy.max_tokens == Policy().max_tokens


def test_상태_자리는_에이전트마다_갈린다(tmp_path):
    assert load(folder(tmp_path)).state_root.name == ".scribe"


# ── 어긋난 정의는 뜨기 전에 걸린다 ───────────────────────────────────
def test_모르는_정책_칸은_뜨기_전에_걸린다(tmp_path):
    with pytest.raises(BadSpec, match="모르는 정책 칸"):
        load(folder(tmp_path, TOML + "\n[policy]\nmax_trun = 3\n"))


def test_코드를_넘겨야_하는_칸은_파일로_못_적는다(tmp_path):
    with pytest.raises(BadSpec, match="코드를 넘겨야"):
        load(folder(tmp_path, TOML + '\n[policy]\nsanitizers = ["x"]\n'))


def test_모르는_어댑터(tmp_path):
    with pytest.raises(BadSpec, match="모르는 어댑터"):
        load(folder(tmp_path, TOML.replace('"anthropic"', '"openai"')))


def test_배역에_자기가_없으면_걸린다(tmp_path):
    bad = TOML + (
        '\n[cast]\nagents = ["warden"]\nhumans = ["owner"]\n'
        '[cast.rooms]\n"a" = ["warden"]\n'
    )
    with pytest.raises(BadSpec, match="자기 자신"):
        load(folder(tmp_path, bad))


def test_지침이_없으면_걸린다(tmp_path):
    d = folder(tmp_path)
    (d / "prompt.md").unlink()
    with pytest.raises(BadSpec, match="지침"):
        load(d)


def test_정체성은_안_적으면_없어도_된다(tmp_path):
    """줄을 지우면 지침만으로 돈다."""
    without = TOML.replace('identity = "identity.md"\n', "")
    assert load(folder(tmp_path, without, identity=None)).identity == ""


def test_가리켰는데_없으면_걸린다(tmp_path):
    """오타를 조용히 넘기면 정체성 없이 도는 것을 아무도 모른다."""
    with pytest.raises(BadSpec, match="그 줄을 지워라"):
        load(folder(tmp_path, identity=None))


# ── 실제로 도는가 ────────────────────────────────────────────────────
def test_파이썬_한_줄_없이_도는_에이전트(clean, tmp_path):
    """파일 셋만 두고 말이 나온다. 이게 이 단계 전부다."""
    agent = runner.Agent(load(folder(tmp_path)), client=FakeClient(Resp([Text("네.")])))
    turn = agent.run("있나")
    assert turn.text == "네."
    assert turn.model == "test-model"


def test_정체성이_지침보다_앞이다(clean, tmp_path):
    """캐시가 접두사로 걸린다. 안 변하는 것이 앞이어야 뒤가 안 다시 나간다."""
    system = runner.Agent(load(folder(tmp_path)), client=FakeClient()).system()
    assert system[0]["text"] == "나는 서기다."
    assert system[1]["text"] == "짧게 답한다."


def test_폴더의_도구가_불린다(clean, tmp_path):
    d = folder(
        tmp_path,
        TOML + '\n[tools]\nmodule = "tools.py"\n',
        tools=(
            "def tools(scope):\n"
            "    return [{'name': 'count',\n"
            "             'input_schema': {'type': 'object', 'properties': {}}}]\n\n"
            "def call(name, **args):\n"
            "    return {'count': 3}\n"
        ),
    )
    client = FakeClient(
        Resp([Use("u1", "count", {})], stop_reason="tool_use"),
        Resp([Text("셋이다.")]),
    )
    turn = runner.Agent(load(d), client=client).run("몇이냐")
    assert turn.text == "셋이다."
    assert [c["name"] for c in turn.tool_calls] == ["count"]
    assert client.seen[0]["tools"][0]["name"] == "count"


def test_도구가_없어도_돈다(clean, tmp_path):
    """말만 하는 에이전트도 에이전트다."""
    agent = runner.Agent(load(folder(tmp_path)), client=FakeClient(Resp([Text("음.")])))
    assert agent.session.tools("") == []
    assert agent.run("있나").text == "음."


def test_상태는_그_폴더_안에_앉는다(clean, tmp_path):
    from genie_agents import store

    d = folder(tmp_path)
    runner.Agent(load(d), client=FakeClient())
    assert store.default_root() == d / ".scribe"


def test_한_프로세스에_둘은_안_뜬다(clean, tmp_path):
    """조용히 계속 돌면 이 에이전트의 원장이 남의 폴더에 앉는다."""
    runner.Agent(load(folder(tmp_path)), client=FakeClient())
    other = tmp_path / "warden"
    other.mkdir()
    (other / FILE).write_text(TOML.replace('"scribe"', '"warden"'), encoding="utf-8")
    (other / "prompt.md").write_text("x", encoding="utf-8")
    (other / "identity.md").write_text("x", encoding="utf-8")
    with pytest.raises(BadSpec, match="한 프로세스에 하나"):
        runner.Agent(load(other), client=FakeClient())


# ── 띄우기 전에 본다 ─────────────────────────────────────────────────
def test_check_는_키가_없어도_정의를_본다(clean, tmp_path):
    clean.delenv("ANTHROPIC_API_KEY", raising=False)
    problems = runner.check(folder(tmp_path))
    assert any("키가 없다" in p for p in problems)


def test_check_는_도구가_죽으면_알려준다(clean, tmp_path):
    d = folder(tmp_path, TOML + '\n[tools]\nmodule = "tools.py"\n', tools="1/0\n")
    assert any("도구를 읽다 죽었다" in p for p in runner.check(d))


def test_check_는_도구_모양이_아니면_알려준다(clean, tmp_path):
    d = folder(tmp_path, TOML + '\n[tools]\nmodule = "tools.py"\n', tools="x = 1\n")
    assert any("루프가 요구하는 것은" in p for p in runner.check(d))


def test_check_는_걸린_것을_전부_돌려준다(clean, tmp_path):
    """하나씩 고치게 하면 세 번 돌려야 하는 것을 세 번 실행해야 안다."""
    clean.delenv("ANTHROPIC_API_KEY", raising=False)
    without = TOML.replace('identity = "identity.md"\n', "")
    d = folder(
        tmp_path,
        without + '\n[tools]\nmodule = "tools.py"\n',
        tools="1/0\n",
        identity=None,
    )
    problems = runner.check(d)
    assert len(problems) == 3, problems


# ── 틀 ───────────────────────────────────────────────────────────────
def test_만든_틀은_그대로_읽힌다(clean, tmp_path):
    """`new` 가 낸 것이 그대로 안 읽히면 첫 걸음에서 막힌다."""
    from genie_agents.__main__ import main

    d = tmp_path / "warden"
    assert main(["new", str(d)]) == 0
    s = load(d)
    assert s.id == "warden" and s.adapter == "anthropic"
    agent = runner.Agent(s, client=FakeClient(Resp([Text("예.")])))
    assert agent.run("있나").text == "예."


def test_모르는_칸은_절마다_걸린다(tmp_path):
    """`timezon` 하나 잘못 적으면 조용히 UTC 로 돈다. 몇 주 뒤에 발견된다."""
    with pytest.raises(BadSpec, match=r"\[agent\] 이 모르는 칸"):
        load(folder(tmp_path, TOML.replace("timezone =", "timezon =")))
