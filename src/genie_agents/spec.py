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
from .wake import Nudge

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
    extract: str
    """추출 호출이 읽는 지침. **도구 루프가 아닌 자리**라 `instructions` 와 다르다.

    ★ 무엇을 고리로 볼지는 그 사람의 일에 딸린 것이라 골격이 안 정한다
      (`docs/wiring.md` 7절). 없으면 추출을 안 돈다.
    """
    policy: Policy
    nudge: Nudge
    """부르지 않았는데 말하는 것의 상한. 안 적으면 골격 기본값이다."""
    gated: tuple[str, ...]
    tools_module: str
    enabled: tuple[str, ...] = field(default_factory=tuple)
    """골격 도구 중 켠 것. `genie_agents.kit.CATALOG` 의 이름들."""
    describe: dict = field(default_factory=dict)
    """설명문을 갈아 끼운 것. **말투가 곧 인격이라** 그 존재가 정한다."""
    cast: dict | None = field(default=None)
    watch: dict = field(default_factory=dict)
    """**보는 자리** — 어느 방을 읽나. 대화하는 자리(`cast`)와 다른 것이다.

    ★ 섞으면 남의 말이 '나에게 온 메시지' 로 루프에 들어간다
      (`channels/__init__.py`). `cast.rooms` 는 봇이 말하는 방이고, 여기는
      봇이 **말하지 않고 읽기만** 하는 방이다.

    ★ 무엇을 읽을지를 값으로 올려 두는 것 자체가 결정이다 — *"읽는 범위를
      좁힌 것이 결정이다"*(`followup.md`). 코드에 방 id 가 박히면 그 결정이
      어디서 났는지 아무도 못 찾는다.
    """

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


def _nudge(raw: dict) -> Nudge:
    """적힌 것만 바꾼다. `quiet` 는 TOML 에서 배열로 오므로 짝으로 굳힌다."""
    known = set(Nudge.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise BadSpec(
            f"모르는 상한 칸이다: {sorted(unknown)} (아는 것: {', '.join(sorted(known))})"
        )
    got = dict(raw)
    if "quiet" in got:
        q = got["quiet"]
        if len(q) != 2:
            raise BadSpec(f"quiet 는 [시작, 끝] 둘이다: {q}")
        got["quiet"] = (str(q[0]), str(q[1]))
    try:
        return Nudge(**got)
    except (ValueError, TypeError) as e:
        raise BadSpec(f"[nudge] 가 잘못됐다: {e}") from None


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
    "prompt": {"instructions", "identity", "extract"},
    "tools": {"module", "gated", "enable", "describe"},
    "nudge": set(Nudge.__dataclass_fields__),
    "watch": {"slack", "first_days", "thread_days", "keep_hours", "keep_thread_days"},
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
        extract=_text(root, str(p.get("extract") or ""), "추출 지침", required=False),
        policy=_policy(raw.get("policy") or {}),
        nudge=_nudge(raw.get("nudge") or {}),
        gated=tuple(t.get("gated") or ()),
        tools_module=str(t.get("module") or ""),
        enabled=tuple(t.get("enable") or ()),
        describe=dict(t.get("describe") or {}),
        cast=cast,
        watch=dict(raw.get("watch") or {}),
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
# extract    = "extract.md"   # 추출이 읽는 지침. 도구 루프가 아닌 자리다.
#                             # 무엇을 고리로 볼지는 그 사람의 일에 딸린 것이다

# 할 수 있는 일. **골격 것을 켜든지, 자기 것을 들고 오든지 — 하나만 고른다.**
# 섞으면 같은 이름이 둘일 때 어느 쪽이 도는지 코드를 읽어야 안다.
[tools]
enable = ["reminder_set", "reminder_done", "reminder_list", "note_write", "note_recall"]
# module = "tools.py"        # 대신 자기 파이썬을 쓸 때. 둘 다 없으면 말만 한다
# gated  = ["..."]           # 잔고가 마르면 목록에서 빠지는 도구

# 설명문은 모델이 자기 자신에게 읽는 글이라 **말투가 곧 인격**이다.
# 무엇을 하는가는 골격이 정하고, 어떻게 설명되는가는 여기서 정한다.
# [tools.describe]
# note_write = "적어 둘 것. 짧게."

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

# **보는 자리** — 읽기만 하는 방들. 봇이 말하는 방([cast])과 갈라 둔다.
# 섞으면 남의 말이 '나에게 온 메시지'로 루프에 들어간다.
# [watch]
# slack       = ["C0123", "D0456"]  # 채널 id. 이름이 아니라 id 다
# first_days  = 3                   # 커서가 없는 첫 날 거슬러 올라가는 날수
# thread_days = 3                   # 이만큼 안에 움직인 스레드는 다시 파 본다
#
# ★ 남기는 값 둘. **이 두 줄이 곧 "무엇을 얼마나 남기나" 의 답이다.**
# keep_hours       = 72   # 원문(오간 말 그대로)
# keep_thread_days = 30   # 스레드 자국(id·시각만, 글은 안 남는다)
#
# ★ 셋째 층이 있는데 여기 없다 — `loops.json` 은 **안 지운다**(loops.py:
#   "닫힌 고리가 곧 한 일이다"). 고리 본문은 남의 말에서 뽑은 문장이라,
#   원문을 사흘에 버려도 **옮겨 적힌 것은 영영 남는다.** 알고 두는 것이다.

# 부르지 않았는데 말할 때의 **상한**이다. 말할지 말지의 판단은 여기가 아니라
# [policy] decision_tools 가 든다 — 판단과 상한을 갈라 둔다.
# 스스로 안 깨어나는 에이전트는 이 절이 없어도 된다.
# [nudge]
# morning         = "로그온"        # 또는 "08:30". 상시가 아닌 기계에서는 시각이 뜻이 없다
# evening         = "18:00"
# quiet           = ["22:00", "07:00"]   # 이 사이엔 아무것도 안 간다
# max_per_day     = 3
# min_gap_minutes = 180
# carry_over      = true            # 못 낸 것을 버리지 않고 다음에 낸다
'''
