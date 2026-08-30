"""에이전트 정의 — 폴더 하나가 에이전트 하나다.

    myagent/
      agent.toml     누구인지 · 무엇 위에서 도는지 · 루프를 어떻게 도는지
      prompt.md      지침
      identity.md    정체성 (없어도 된다)
      tools.py       이 에이전트가 할 수 있는 일 (없어도 된다)

━━ 왜 코드가 아니라 파일인가 ━━

인격을 파이썬으로 쓰게 하면 **에이전트를 만들 수 있는 사람이 파이썬을 쓰는
사람으로 좁아진다.** 이 골격을 여럿이 나눠 쓰는 이유가 그 반대편에 있다.

그리고 파일이면 **무엇이 선언이고 무엇이 코드인지**가 눈에 보인다. 지침을
코드 안에 두면 다음 사람이 그걸 고쳐도 되는 것으로 읽는다.

━━ 안 정하면 남과 같게 돈다 ━━

`agent.toml` 에 적지 않은 것은 골격의 기본값이다. **적힌 것만이 이 에이전트가
남과 다른 전부**이고, 그래서 한 파일만 읽으면 차이를 안다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .policy import Policy

FILE = "agent.toml"


class BadSpec(ValueError):
    """정의가 잘못됐다. **뜨기 전에** 걸린다 — 반쯤 뜬 에이전트가 제일 나쁘다."""


@dataclass(frozen=True)
class Spec:
    root: Path
    id: str
    prefix: str
    adapter: str
    model: str
    timezone: str
    utc_offset: float
    instructions: str
    identity: str
    policy: Policy
    gated: tuple[str, ...]
    tools_module: str
    cast: dict | None = field(default=None)

    @property
    def state_root(self) -> Path:
        """이 에이전트의 상태 디렉토리. `<폴더>/.<id>/`.

        **에이전트마다 갈린다.** 섞이면 두 존재가 아니라 지갑을 같이 쓰는
        하나가 된다 — 실제로 한 번 그렇게 새어서 잔고 원장이 남의 자리에 앉았다.
        """
        return self.root / f".{self.id}"


def _text(root: Path, name: str, what: str, required: bool) -> str:
    if not name:
        if required:
            raise BadSpec(f"{what} 파일이 안 적혀 있다")
        return ""
    p = root / name
    if not p.exists():
        # 이름을 대 놓고 가리켰는데 없다. 오타이거나 안 옮긴 파일이다 —
        # 안 쓸 거면 그 줄을 지우면 된다.
        raise BadSpec(f"{what} 가 없다: {p} (안 쓸 거면 {FILE} 에서 그 줄을 지워라)")
    return p.read_text(encoding="utf-8").strip()


def _policy(raw: dict) -> Policy:
    """적힌 것만 바꾼다. 나머지는 골격 기본값이고, 기본값끼리는 서로 같다."""
    known = {f for f in Policy.__dataclass_fields__}
    # 손으로 못 적는 것들 — 코드를 넘겨야 하는 자리다.
    bycode = {"sanitizers", "result_blocks", "meter", "extra"}
    unknown = set(raw) - known
    if unknown:
        raise BadSpec(f"모르는 정책 칸이다: {sorted(unknown)}")
    hand = set(raw) & bycode
    if hand:
        raise BadSpec(f"이 칸은 파일로 못 적는다(코드를 넘겨야 한다): {sorted(hand)}")

    got = dict(raw)
    for k in ("decision_tools", "decision_scopes"):
        if k in got:
            got[k] = frozenset(got[k])
    return Policy(**got)


# 각 절이 아는 칸. **모르는 칸은 걸린다** — `timezon` 하나 잘못 적으면 조용히
# UTC 로 돌고, 그건 몇 주 뒤 시간이 이상한 걸로 발견된다.
_SECTIONS = {
    "agent": {"id", "prefix", "adapter", "model", "timezone", "utc_offset"},
    "prompt": {"instructions", "identity"},
    "tools": {"module", "gated"},
}


def _only(raw: dict, allowed: set[str], section: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise BadSpec(
            f"[{section}] 이 모르는 칸이다: {sorted(unknown)} "
            f"(아는 것: {', '.join(sorted(allowed))})"
        )


def load(root: Path | str) -> Spec:
    """폴더 하나를 읽는다. 어긋난 곳은 **여기서** 걸린다."""
    root = Path(root)
    f = root / FILE
    if not f.exists():
        raise BadSpec(f"{FILE} 이 없다: {root}")
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise BadSpec(f"{FILE} 을 못 읽었다: {e}") from None

    for section, allowed in _SECTIONS.items():
        _only(raw.get(section) or {}, allowed, section)

    a = raw.get("agent") or {}
    ident = str(a.get("id") or "").strip()
    if not ident:
        raise BadSpec("[agent] id 가 없다. 이 에이전트를 뭐라고 부를지가 먼저다")
    if not ident.replace("_", "").replace("-", "").isalnum():
        raise BadSpec(f"id 는 글자·숫자·-·_ 만 쓴다: {ident!r}")

    adapter = str(a.get("adapter") or "").strip()
    if adapter not in ("anthropic", "gemini"):
        raise BadSpec(f"모르는 어댑터다: {adapter!r} (anthropic | gemini)")

    p = raw.get("prompt") or {}
    cast = raw.get("cast")
    if cast is not None:
        for k in ("agents", "humans", "rooms"):
            if k not in cast:
                raise BadSpec(f"[cast] 에 {k} 가 없다")
        if ident not in cast["agents"]:
            raise BadSpec(f"[cast] agents 에 자기 자신({ident})이 없다")

    t = raw.get("tools") or {}
    return Spec(
        root=root,
        id=ident,
        # 환경변수 프리픽스. 한 호스트에 여럿이 살면서 서로의 설정과 상태를
        # 안 밟는다. 안 적으면 id 를 대문자로 쓴다.
        prefix=str(a.get("prefix") or ident).upper().replace("-", "_"),
        adapter=adapter,
        model=str(a.get("model") or ""),
        timezone=str(a.get("timezone") or "UTC"),
        utc_offset=float(a.get("utc_offset") or 0),
        instructions=_text(root, str(p.get("instructions") or ""), "지침", required=True),
        identity=_text(root, str(p.get("identity") or ""), "정체성", required=False),
        policy=_policy(raw.get("policy") or {}),
        gated=tuple(t.get("gated") or ()),
        tools_module=str(t.get("module") or ""),
        cast=cast,
    )


TEMPLATE = '''# 이 폴더 하나가 에이전트 하나다.
# 여기 **안 적은 것은 골격의 기본값**이다 — 적힌 것만이 이 에이전트가 남과
# 다른 전부고, 그래서 이 파일만 읽으면 차이를 안다.

[agent]
id = "{id}"
adapter = "{adapter}"        # anthropic | gemini
# model    = ""              # 안 적으면 어댑터 기본값
# prefix   = "{prefix}"      # 환경변수 앞머리. 안 적으면 id 를 대문자로
timezone = "Asia/Seoul"

[prompt]
instructions = "prompt.md"   # 지침 — 구조로 강제할 수 없는 것만 적는다
identity     = "identity.md" # 정체성 — 이 존재가 누구인지 (없어도 된다)

[tools]
module = "tools.py"          # 이 폴더의 파이썬. 없으면 도구 없이 돈다
# gated = ["principle_revise"]   # 잔고가 마르면 목록에서 빠지는 도구

# 루프가 갈리는 자리. 안 적으면 다른 에이전트와 똑같이 작동한다.
[policy]
# max_turns       = 8
# max_tokens      = 4096
# decision_tools  = ["speak", "stay_silent"]   # 불리면 거기서 끝낸다
# max_pauses      = 0                          # 서버 도구가 턴을 끊을 때
# retry_when_empty = true

# 여럿이 서로 말할 때만 적는다. 혼자 도는 에이전트는 이 절이 없어도 된다.
# [cast]
# agents = ["{id}"]
# humans = ["owner"]
# [cast.rooms]
# "{id}-owner" = ["{id}", "owner"]
'''
