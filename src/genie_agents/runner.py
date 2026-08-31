"""정의 하나를 실제로 도는 에이전트로.

이 파일이 **베끼던 배선을 대신한다.** 전에는 에이전트마다 어댑터를 붙이고,
프리픽스를 걸고, 시간대를 등록하고, 배역을 알리고, 도구를 모아 루프에
넘기는 코드를 한 벌씩 갖고 있었다. 하는 일이 같은데 파일이 여럿이라
한쪽을 고치면 다른 쪽이 뒤처졌다.

━━ 여기서 안 하는 것 ━━

**무엇을 말할지, 언제 말을 걸지는 안 정한다.** 그건 그 에이전트가 누구인지에
딸린 것이라 `prompt.md` 와 `tools.py` 가 든다. 이 파일이 하는 일은 그 둘을
루프에 **틀리지 않게 이어 주는** 것뿐이다.

━━ 한 프로세스에 하나 ━━

프리픽스가 프로세스 환경에 걸린다(`env.use`). 그래서 **한 프로세스는 한
에이전트**다. 여럿 돌리려면 프로세스를 나눠라 — 유닛 템플릿 하나면 된다.
같은 프로세스에서 둘을 띄우면 나중 것이 앞 것의 설정을 밟는다.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from . import clock, env, loop
from .loop import Turn
from .spec import BadSpec, Spec, load
from .tools import MissingContext, UnknownTool


def local_url() -> str:
    from .adapters.local import url

    return url()


class NoTools:
    """도구 없이 도는 에이전트. 말만 한다."""

    def tools(self, scope: str) -> list[dict]:
        return []

    def call(self, name: str, **args: Any) -> dict:
        return {"error": f"이 에이전트에는 도구가 없다: {name}"}


class KitSession:
    """`agent.toml` 에서 이름으로 켠 것만 내놓는다.

    상태(리마인더·메모·바깥 소식)는 이 에이전트 자리에 앉는다. 도구가
    무엇을 요구하는지는 도구가 스스로 적어 두고(`Tool.needs`), 못 채우면
    **켤 때** 죽는다 — 부를 때 죽으면 그 턴이 통째로 날아간다.
    """

    def __init__(self, spec: Spec) -> None:
        from . import (
            loops as _loops,
            notes as _notes,
            reminders as _reminders,
            world as _world,
        )
        from .kit import CATALOG
        from .tools import Toolbox

        root = spec.state_root
        self.reminders = _reminders.ReminderStore(root)
        self.notes = _notes.NoteStore(root)
        self.world = _world.WorldFeed(root)
        self.loops = _loops.LoopBook(root)
        self.box = Toolbox(CATALOG, spec.enabled, describe=spec.describe)
        self.box.bind(self)

    def tools(self, scope: str) -> list[dict]:
        return self.box.specs(scope)

    def call(self, name: str, **args: Any) -> dict:
        return self.box.call(self, name, **args)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BadSpec(f"{path} 를 못 읽었다")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Agent:
    """정의 + 클라이언트 + 도구를 들고 한 턴을 돈다."""

    def __init__(self, spec: Spec, client: Any = None, session: Any = None) -> None:
        self.spec = spec

        # ★ **누구인지부터 정한다.** 프리픽스가 걸려야 상태 디렉토리도 설정도
        #   이 에이전트 자리를 본다. 늦게 걸면 그 전에 만들어진 것이 남의
        #   자리에 앉는다 — 실제로 그렇게 샌 적이 있다.
        env.use(spec.prefix)
        if env.prefix() != spec.prefix:
            # 이 프로세스는 이미 남이다. 조용히 그 자리에서 계속 돌면 이 에이전트의
            # 원장이 남의 폴더에 앉는다 — 그게 정확히 한 번 일어났던 일이다.
            raise BadSpec(
                f"이 프로세스는 이미 {env.prefix()} 다. {spec.id}({spec.prefix}) 는 "
                f"따로 띄워라 — 한 프로세스에 하나다"
            )
        clock.set_default(spec.prefix, spec.timezone, spec.utc_offset)
        os.environ.setdefault(env.key("ROOT"), str(spec.state_root))

        if spec.cast:
            from .messaging import peers

            cast = peers.Cast(
                agents=tuple(spec.cast["agents"]),
                humans=tuple(spec.cast["humans"]),
                rooms={k: tuple(v) for k, v in spec.cast["rooms"].items()},
                speakers=dict(spec.cast.get("speakers") or {}),
            )
            peers.identify(spec.prefix, spec.id, cast)

        self.client = client if client is not None else _client_for(spec)
        self.session = session if session is not None else _session_for(spec)
        self.model = spec.model or _default_model(spec)

    # --- 시스템 프롬프트 ---

    def system(self) -> list[dict]:
        """정체성이 앞, 지침이 뒤. **안 변하는 것을 앞에 둔다** — 캐시가
        접두사로 걸려서, 앞이 흔들리면 그 뒤가 통째로 다시 나간다."""
        out = []
        if self.spec.identity:
            out.append({"type": "text", "text": self.spec.identity})
        out.append({"type": "text", "text": self.spec.instructions})
        return out

    # --- 한 턴 ---

    def run(self, text: str, scope: str = "", turn: Turn | None = None) -> Turn:
        messages = [{"role": "user", "content": text}]
        return loop.run(
            self.client,
            self.session,
            messages,
            model=self.model,
            system=self.system(),
            scope=scope,
            policy=self.spec.policy,
            turn=turn,
        )


def _default_model(spec: Spec) -> str:
    mod = _adapter(spec.adapter)
    return mod.default_model()


def _adapter(name: str):
    from .adapters import anthropic, gemini, local

    return {"anthropic": anthropic, "gemini": gemini, "local": local}[name]


def _client_for(spec: Spec):
    """어댑터를 **늦게** 부른다. SDK 가 없어도 `check` 는 돌아야 한다."""
    return _adapter(spec.adapter).client()


def _session_for(spec: Spec):
    """도구는 그 폴더의 `tools.py` 가 든다.

    `Session` 클래스가 있으면 그걸 쓰고, 없으면 모듈에 있는 `tools`/`call`
    함수를 쓴다. 둘 다 없으면 도구 없이 돈다 — 말만 하는 에이전트도 에이전트다.
    """
    if spec.enabled and spec.tools_module:
        raise BadSpec(
            "[tools] 에 enable 과 module 이 같이 있다. 둘 중 하나만 골라라 — "
            "골격 도구를 켜든지, 자기 도구를 들고 오든지. 섞으면 같은 이름이 "
            "둘이 될 때 어느 쪽이 도는지 코드를 읽어야 안다"
        )
    if spec.enabled:
        return KitSession(spec)
    if not spec.tools_module:
        return NoTools()
    path = spec.root / spec.tools_module
    if not path.exists():
        raise BadSpec(f"도구 파일이 없다: {path}")
    mod = _load_module(path, f"_agent_tools_{spec.id}")
    if hasattr(mod, "Session"):
        return mod.Session(spec)
    if hasattr(mod, "tools") and hasattr(mod, "call"):
        return mod
    raise BadSpec(
        f"{path} 에 `Session` 도 `tools`/`call` 도 없다. "
        "루프가 요구하는 것은 그 둘뿐이다."
    )


def check(root: Path | str) -> list[str]:
    """띄우기 전에 본다. 무엇이 걸렸는지 **전부** 돌려준다 — 하나씩 고치게
    하면 세 번 돌려야 하는 것을 세 번 실행해야 안다."""
    problems: list[str] = []
    try:
        spec = load(root)
    except BadSpec as e:
        return [str(e)]

    if spec.tools_module or spec.enabled:
        try:
            _session_for(spec)
        except (BadSpec, UnknownTool, MissingContext) as e:
            # 우리가 낸 말이다. 그대로 보여 준다 — 덧씌우면 무엇이 문제인지
            # 적어 둔 안내가 예외 이름 뒤로 숨는다.
            problems.append(str(e))
        except Exception as e:  # noqa: BLE001 — 남의 코드다. 무엇이든 알려준다
            problems.append(f"도구를 읽다 죽었다: {type(e).__name__}: {e}")

    mod = _adapter(spec.adapter)
    if not mod.available():
        # 로컬은 키가 아니라 **프로세스**가 없는 것이다. 고치는 길이 달라서
        # 말도 달라야 한다 — "키가 없다" 는 여기서 사람을 엉뚱한 데로 보낸다.
        problems.append(
            f"로컬 모델이 안 떠 있다 ({local_url()}) — llama.cpp 서버를 띄워라"
            if spec.adapter == "local"
            else f"{spec.adapter} 키가 없다 — 정의는 성하지만 못 뜬다"
        )

    if spec.watch and not spec.watch.get("slack"):
        # ★ 켜 놓고 방이 없으면 **아무 말 없이 아무것도 안 한다.** 매시 깨어나서
        #   빈 손으로 돌아오는 것을 사람은 몇 주 뒤에나 눈치챈다.
        problems.append("(참고) [watch] 가 있는데 보는 방이 없다 — `rooms` 로 id 를 찾아 넣어라")

    if spec.watch.get("slack"):
        # ★ 키가 없어도 `check` 는 돈다. 그런데 **읽는 자리가 안 열리는 것은
        #   조용한 고장**이다 — 아무 말도 안 하고 원장만 안 찬다. 그래서 여기서
        #   한 번 짚는다.
        # ★ 프리픽스를 손으로 준다. 여기는 `env.use` 를 안 부르는 자리라
        #   그냥 `env.get` 을 쓰면 접두사 없는 이름을 찾고, 있는 토큰을 없다고 말한다.
        이름 = env.key("SLACK_USER_TOKEN", spec.prefix)
        토큰 = env.get("SLACK_USER_TOKEN", 프리픽스=spec.prefix)
        if not 토큰:
            problems.append(
                f"{이름} 이 없다 — [watch] slack 을 못 읽는다\n"
                f"     {spec.root / '.env'} 에 한 줄: {이름}=xoxp-..."
            )
        elif not 토큰.startswith("xoxp-"):
            # ★ **제일 하기 쉬운 실수다.** Slack CLI 로 앱을 만들면 손에 먼저
            #   잡히는 것이 봇 토큰(xoxb-)이고, 그걸로는 팀원 DM 이 한 줄도 안
            #   보인다. 에러도 안 난다 — 빈 방으로 보일 뿐이다. 그게 제일 나쁘다.
            어떤것 = "봇 토큰(xoxb-)이다" if 토큰.startswith("xoxb-") else "모르는 꼴이다"
            problems.append(
                f"{이름} 이 사용자 토큰이 아니다 — {어떤것}. "
                "사용자 토큰으로 읽고 봇 토큰으로 말한다(wiring.md 3절)"
            )
        # ★ 봇 토큰은 아직 아무 데서도 안 읽는다(5단계). 그래도 지금 본다 —
        #   그때가 되면 이 값은 **몇 주 전에 넣어 둔 것**이라 아무도 다시 안 본다.
        #   봇 칸에 사용자 토큰이 들어가면 봇이 본인 이름으로 말하고,
        #   팀원이 사람과 봇을 구분 못 하게 된다. 되돌릴 수 없는 종류다(§1).
        봇 = env.get("SLACK_BOT_TOKEN", 프리픽스=spec.prefix)
        if 봇 and not 봇.startswith("xoxb-"):
            문제 = ("사용자 토큰(xoxp-)이다 — 이걸로 말하면 본인이 친 것으로 보인다"
                  if 봇.startswith("xoxp-") else "모르는 꼴이다")
            problems.append(
                f"{env.key('SLACK_BOT_TOKEN', spec.prefix)} 이 봇 토큰이 아니다 — {문제}"
            )

        나쁜방 = [r for r in spec.watch["slack"]
                if not (isinstance(r, str) and r[:1] in "CDG" and r[1:].isalnum())]
        if 나쁜방:
            problems.append(
                f"[watch] slack 은 채널 **id** 다(C·D·G 로 시작): {나쁜방}"
            )

    if not spec.identity:
        problems.append("(참고) 정체성 파일이 없다. 지침만으로 돈다")
    return problems
