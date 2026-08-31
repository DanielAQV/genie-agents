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


# ── 보는 자리 ────────────────────────────────────────────────────────
WATCH = '\n[watch]\nslack = ["C0123", "D0456"]\nkeep_hours = 48\n'


def test_보는_자리를_값으로_읽는다(tmp_path):
    """★ 코드에 방 id 가 박히면 *"읽는 범위를 좁힌 것이 결정이다"* 가
    어디서 났는지 아무도 못 찾는다."""
    s = load(folder(tmp_path, TOML + WATCH))
    assert s.watch["slack"] == ["C0123", "D0456"]
    assert s.watch["keep_hours"] == 48


def test_안_적으면_보는_자리가_없다(tmp_path):
    """읽기는 켜야 도는 것이다. 기본이 "다 읽는다" 면 안 된다."""
    assert load(folder(tmp_path)).watch == {}


def test_모르는_보는_자리_칸은_걸린다(tmp_path):
    with pytest.raises(BadSpec, match=r"\[watch\] 이 모르는 칸"):
        load(folder(tmp_path, TOML + '\n[watch]\nslak = ["C1"]\n'))


def test_토큰이_없으면_check_가_짚는다(clean, tmp_path):
    """★ 읽는 자리가 안 열리는 것은 **조용한 고장**이다 — 아무 말도 안 하고
    원장만 안 찬다. `check` 는 키가 없어도 도는 자리라 여기서 짚는다."""
    clean.delenv("SCRIBE_SLACK_USER_TOKEN", raising=False)
    got = runner.check(folder(tmp_path, TOML + WATCH))
    assert any("SLACK_USER_TOKEN" in p for p in got), got


def test_방을_이름으로_적으면_짚는다(clean, tmp_path):
    """`#개발` 로 적으면 `channel_not_found` 가 회사 PC 에서 밤에 난다."""
    clean.setenv("SCRIBE_SLACK_USER_TOKEN", "xoxp-x")
    got = runner.check(folder(tmp_path, TOML + '\n[watch]\nslack = ["#개발"]\n'))
    assert any("채널 **id**" in p for p in got), got


def test_프리픽스_붙은_토큰을_찾아낸다(clean, tmp_path):
    """★ `check` 는 `env.use` 를 안 부르는 자리다. 접두사 없이 찾으면 **있는
    토큰을 없다고 말한다** — `check` 가 한 번 거짓말하면 그 다음부터 사람은
    `check` 를 안 본다."""
    clean.setenv("SCRIBE_SLACK_USER_TOKEN", "xoxp-x")
    got = runner.check(folder(tmp_path, TOML + WATCH))
    assert not any("SLACK_USER_TOKEN" in p for p in got), got


def test_env_는_그_폴더에서_읽는다(clean, tmp_path):
    """★ cwd 가 아니라 `<폴더>/.env` 다. 한 호스트에 에이전트 여럿이 살고,
    cwd 로 읽으면 **어디서 불렀느냐에 따라 남의 토큰을 읽는다.**"""
    from genie_agents.config import load_env

    d = folder(tmp_path, TOML + WATCH)
    (d / ".env").write_text("SCRIBE_SLACK_USER_TOKEN=xoxp-여기있다\n", encoding="utf-8")
    clean.delenv("SCRIBE_SLACK_USER_TOKEN", raising=False)
    try:
        load_env(d / ".env")
        assert not any("SLACK_USER_TOKEN" in p for p in runner.check(d))
    finally:
        os.environ.pop("SCRIBE_SLACK_USER_TOKEN", None)


def test_봇_토큰을_넣으면_짚는다(clean, tmp_path):
    """★ **제일 하기 쉬운 실수다.** Slack CLI 로 앱을 만들면 손에 먼저 잡히는
    것이 봇 토큰(xoxb-)이고, 그걸로는 팀원 DM 이 한 줄도 안 보인다.
    에러도 안 난다 — **빈 방으로 보일 뿐이다.** 그게 제일 나쁜 고장이다."""
    clean.setenv("SCRIBE_SLACK_USER_TOKEN", "xoxb-봇이다")
    got = runner.check(folder(tmp_path, TOML + WATCH))
    assert any("봇 토큰" in p for p in got), got


def test_방이_비어_있으면_짚는다(clean, tmp_path):
    """★ 켜 놓고 방이 없으면 **아무 말 없이 아무것도 안 한다.** 매시 깨어나서
    빈 손으로 돌아오는 것을 사람은 몇 주 뒤에나 눈치챈다."""
    got = runner.check(folder(tmp_path, TOML + "\n[watch]\nslack = []\n"))
    assert any("보는 방이 없다" in p for p in got), got


def test_남기는_값_둘을_따로_읽는다(tmp_path):
    """★ 원문과 스레드 자국은 **다른 것을 남긴다.** 하나로 묶으면 "글은
    사흘, 자국은 한 달" 이라는 결정 자체를 적을 자리가 없어진다."""
    s = load(folder(tmp_path, TOML + '\n[watch]\nslack = ["C1"]\n'
                    'keep_hours = 48\nkeep_thread_days = 14\n'))
    assert s.watch["keep_hours"] == 48
    assert s.watch["keep_thread_days"] == 14


def test_봇_칸에_사용자_토큰이_들어가면_짚는다(clean, tmp_path):
    """★ 사용자 토큰으로 말하면 **본인이 친 것으로 보인다.** 팀원이 사람과
    봇을 구분 못 하게 되고, 그건 되돌릴 수 없다(wiring.md §1).

    5단계에 가서 보면 늦다 — 그때 이 값은 몇 주 전에 넣어 둔 것이다."""
    clean.setenv("SCRIBE_SLACK_USER_TOKEN", "xoxp-사용자")
    clean.setenv("SCRIBE_SLACK_BOT_TOKEN", "xoxp-이것도사용자")
    got = runner.check(folder(tmp_path, TOML + WATCH))
    assert any("본인이 친 것으로 보인다" in p for p in got), got


def test_봇_토큰이_성하면_아무_말_안_한다(clean, tmp_path):
    clean.setenv("SCRIBE_SLACK_USER_TOKEN", "xoxp-사용자")
    clean.setenv("SCRIBE_SLACK_BOT_TOKEN", "xoxb-봇")
    assert not any("BOT_TOKEN" in p for p in runner.check(folder(tmp_path, TOML + WATCH)))
