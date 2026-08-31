"""추출 — 오간 말에서 열린 고리를 캔다. **세 겹**(`docs/wiring.md` 5절).

    겹 1  규칙으로 후보를 판다      싸다. 모델을 안 부른다
    겹 2  모델 한 번. 묶어서 준다   방 하나 × 시간창 하나
    겹 3  원장과 대조              닫는 것이 여는 것보다 먼저다

━━ 이 호출은 도구 루프가 아니다 ━━

★ `loop.run` 을 안 탄다. **도구를 안 부르고 JSON 하나를 받는 자리**라 도구
  루프에 태우면 모델이 도구를 부르려 들고, 그 턴들이 전부 값이다. 어댑터의
  클라이언트를 곧장 쓴다(`adapters/base.py` 의 `Client`).

  도구 루프를 타는 자리는 따로 있다 — 저녁에 사람이 한 줄 답할 때.

━━ 여기가 안 정하는 것 ━━

**무엇을 고리로 볼지는 안 정한다.** 그건 그 사람의 일에 딸린 것이라
`extract.md` 가 든다(`spec.extract`). 여기가 드는 것은 *무엇을 실을지* ·
*무엇을 믿을지* · *어떻게 원장에 옮길지*뿐이다.

━━ 규칙이 모델 위에서 누른다 ━━

★ 모델에게 `sure` 를 내게 하되 **규칙이 위에서 누른다.** 모델은 자기 추론을
  확신한다 — 추론해서 찾아냈다는 사실 자체가 확신의 근거가 되지 않는다.
  `sure=False` 는 먼저 말 걸지 않는다(`loops.py`). 여기가 신뢰의 방파제다.

━━ 막힌 것을 예외로 안 던진다 ━━

★ 모델은 지워진 id 를 부르고 없는 상태를 지어낸다. 예외면 그 턴이 통째로
  날아가고, **같이 온 성한 판단까지 버려진다.** 못 쓴 것은 `dropped` 에
  모아 돌려준다 — 버리되 조용히 버리지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import clock
from .loops import DONE, DROPPED, LIVE, ME, OPEN, STATES, LoopBook
from .transcript import Book, Bundle, Line

# 겹 1 값 — 무엇을 후보로 볼 것인가. `docs/wiring.md` 5절.
QUIET_DAYS = 2.0
"""내가 마지막으로 말한 뒤 이만큼 조용하면 후보. 답이 끊긴 자리다."""

ASK = re.compile(r"[?？]|어때|되나요|될까|괜찮|가능|알려|봐줄|해줄|부탁")
"""물음으로 보는 표. **완벽하지 않아도 된다** — 겹 1 은 후보를 늘리는 자리고,
거르는 일은 겹 2 가 한다. 여기서 틀리면 묶음 하나가 더 갈 뿐이다."""

# `sure` 를 True 로 둘 수 있는 근거 — **문장 안에** 대상이 있는가.
POINTS_AT = re.compile(r"<@[UW][A-Z0-9]+|<#C[A-Z0-9]+|https?://|#\d+|![0-9]+|[A-Z]+-\d+")
"""멘션 · 채널 · 링크 · PR/이슈 번호 · 지라 키. 하나라도 있으면 그 문장이
스스로 대상을 가리킨다."""


@dataclass
class Move:
    """있는 고리를 움직인다."""

    id: str
    state: str = ""
    note: str = ""
    source: str = ""


@dataclass
class Open:
    """새 고리를 연다."""

    text: str
    owner: str = ME
    source: str = ""
    due: str = ""
    sure: bool = False
    why: str = ""


@dataclass
class Unresolved:
    """가리키는 대상을 못 찾은 말.

    ★ **버리지 않는다.** 저녁 목록의 그 한 줄 — *"'다 확인했어'(10:32) — 뭘
      확인한 건지 못 찾았어"* — 이 통에서 나온다. 못 찾은 것을 조용히 버리면
      사람이 고칠 기회 자체가 사라지고, 그 고리는 영영 안 닫힌다.
    """

    source: str = ""
    said: str = ""
    why: str = ""


@dataclass
class Extraction:
    """모델이 내놓는 세 통. **`moves` 가 `opens` 보다 앞이다 — 순서가 곧 우선순위다.**"""

    moves: list[Move] = field(default_factory=list)
    opens: list[Open] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    """읽다 못 쓴 것들. 예외로 안 던지고 여기 모은다."""

    def __len__(self) -> int:
        return len(self.moves) + len(self.opens) + len(self.unresolved)


# ─────────────────────────────────────────────────────────────────────
# 겹 1 — 규칙으로 후보를 판다. 모델을 안 부른다
# ─────────────────────────────────────────────────────────────────────

def worth_asking(bundle: Bundle, me_id: str = "", at: str | None = None) -> str:
    """이 묶음을 모델에 태울 이유. 없으면 빈 문자열.

    규칙 넷(`wiring.md` 5절) 중 하나라도 걸리면 태운다.

    ★ **후보를 늘리는 쪽으로 틀린다.** 여기서 잘못 태우면 토큰 한 묶음이고,
      잘못 안 태우면 그 고리는 영영 안 잡힌다. 값이 대칭이 아니다.
    """
    lines = bundle.lines
    if not lines:
        return ""

    if any(x.mine for x in lines):
        return "내가 친 말이 있다"
    if me_id and any(x.calls(me_id) for x in lines):
        return "나를 부른 말이 있다"

    # 남이 나에게 물은 뒤 내가 안 답했다
    last = lines[-1]
    if not last.mine and ASK.search(last.text):
        return "남이 물었고 내가 아직 안 답했다"

    # 내가 마지막 발신자인데 조용하다 — 답이 끊긴 자리
    mine = [x for x in lines if x.mine]
    if mine and mine[-1] is lines[-1]:
        if clock.elapsed_minutes(last.ts, at) / 1440 >= QUIET_DAYS:
            return f"내가 마지막으로 말한 뒤 {QUIET_DAYS}일 넘게 조용하다"
    return ""


def plan(book: Book, *, me_id: str = "", rooms=(), at: str | None = None,
         window_minutes: float = 0) -> list[Bundle]:
    """무엇을 태울지 고른다. **묶음 단위 = 방 하나 × 시간창 하나.**

    ★ 후보를 하나씩 부르지 않는다. 오전의 "내가 볼게" 와 오후의 "다 봤어" 가
      **같은 묶음 안에 있어야 열자마자 닫는다.** 하나씩 부르면 고리를 열고,
      다음 호출에서 그걸 닫는 말을 보고도 무엇을 닫는지 모른다.

    스레드가 있으면 스레드가 묶음이고, 없으면 그 방의 창 하나다.
    """
    got: list[Bundle] = []
    for room in (rooms or book.rooms()):
        for t in book.threads(room):
            b = book.bundle(room, thread=t)
            if worth_asking(b, me_id, at):
                got.append(b)
        kw = {"window_minutes": window_minutes} if window_minutes else {}
        b = book.bundle(room, **kw)
        # 스레드에 든 말만 있는 창이면 위에서 이미 태웠다
        if b.lines and not all(x.thread for x in b.lines) and worth_asking(b, me_id, at):
            got.append(b)
    return got


# ─────────────────────────────────────────────────────────────────────
# 겹 2 — 무엇을 실을 것인가
# ─────────────────────────────────────────────────────────────────────

def me_names(names: dict, me_id: str) -> list[str]:
    """남들이 본인을 부르는 이름들.

    ★ 실측에서 이게 없어서 **본인 일이 남 일로 잡혔다.** 팀원들이 본인을
      "mr Khôi" · "Khôi" 라고 부르는데 이름 표에는 id 밖에 없었고, 모델은
      그걸 남으로 봤다. 내 일을 남 일로 세면 원장이 **조용히** 틀린다 —
      목록에 줄은 그대로 있어서 사람이 눈치채기 어렵다.
    """
    full = (names or {}).get(me_id, "")
    got = [x for x in (me_id, full) if x]
    # "Daniel (Khôi)" → "Daniel" · "Khôi". 사람들은 이 조각으로 부른다.
    for 조각 in re.split(r"[()\[\]{}·,/]| - ", full):
        조각 = 조각.strip()
        if len(조각) >= 2:
            got.append(조각)
    return list(dict.fromkeys(got))


def owner_of(raw: str, names: dict | None = None, mine: list | None = None) -> str:
    """모델이 낸 `owner` 를 원장이 쓰는 이름으로 옮긴다.

    ★ 규칙이 모델 위에서 누르는 자리가 하나 더 있다. 모델은 `owner` 에
      **설명을 적는다** — 실측에서 `"설명 없이 '내가 볼게' 라고 말한 사람"`
      이 주인으로 들어왔다. 그리고 날 id 를 그대로 적는다.
    """
    raw = (raw or "").strip()
    if not raw:
        return ME
    낮은 = raw.lower()
    for alias in mine or ():
        if alias and alias.lower() in 낮은:
            return ME          # 남들이 부르는 이름으로 적혀 온 내 일
    if names and raw in names:
        return names[raw]      # 날 id 를 이름으로
    return raw


def serialize(bundle: Bundle, *, loops: list = (), names: dict | None = None,
              me_id: str = "") -> str:
    """모델에 실을 글 한 덩이.

    같이 싣는 것(`wiring.md` 5절): 그 방에서 **열려 있는 고리 목록(id 포함)** —
    ★ **닫을 것을 지목하게 하려고** 넣는다. 없으면 모델은 열기만 한다.
    그리고 사람 이름 표 — `<@U123>` 이 누구인지.
    """
    names = names or {}
    out = [f"# 방 {bundle.room}" + (f" · 스레드 {bundle.thread}" if bundle.thread else "")]

    live = [x for x in loops if x.state in LIVE and x.source.startswith(f"slack:{bundle.room}:")]
    if live:
        out.append("\n## 이 방에서 열려 있는 고리 — 닫히거나 움직였으면 id 로 지목해라")
        for x in live:
            out.append(f"- [{x.id}] ({x.state} · {x.owner}) {x.text}")

    쓰인이름 = {u: names[u] for x in bundle.lines for u in x.mentions if u in names}
    if 쓰인이름:
        out.append("\n## 사람")
        out.extend(f"- <@{u}> = {n}" for u, n in 쓰인이름.items())
        if me_id and me_id in names:
            out.append(f"- <@{me_id}> = 나")

    out.append("\n## 오간 말")
    for x in bundle.lines:
        out.append(f"[{x.key}] {clock.local(x.ts):%m-%d %H:%M} {x.who}: {x.text}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# 겹 2 — 답을 읽는다
# ─────────────────────────────────────────────────────────────────────

FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def parse(text: str) -> Extraction:
    """모델이 낸 글에서 JSON 세 통을 꺼낸다.

    ★ **울타리를 벗긴다.** 4B 급은 물론이고 큰 모델도 ```json 을 두른다.
      형식 하나 때문에 그 턴을 통째로 버리는 것이 제일 비싼 실패다.

    ★ 못 읽은 것은 **예외가 아니라 `dropped`** 다. 셋 중 하나가 이상해도
      나머지 둘은 성하다.
    """
    got = Extraction()
    raw = text.strip()
    m = FENCE.search(raw)
    if m:
        raw = m.group(1).strip()
    else:
        s, e = raw.find("{"), raw.rfind("}")
        raw = raw[s : e + 1] if s >= 0 and e > s else raw
    try:
        d = json.loads(raw)
    except ValueError as e:
        got.dropped.append(f"JSON 을 못 읽었다: {e}")
        return got
    if not isinstance(d, dict):
        got.dropped.append(f"JSON 이 사전이 아니다: {type(d).__name__}")
        return got

    for kind, into, cls in (("moves", got.moves, Move),
                            ("opens", got.opens, Open),
                            ("unresolved", got.unresolved, Unresolved)):
        items = d.get(kind) or []
        if not isinstance(items, list):
            got.dropped.append(f"{kind} 가 목록이 아니다")
            continue
        known = set(cls.__dataclass_fields__)
        for it in items:
            if not isinstance(it, dict):
                got.dropped.append(f"{kind} 에 사전이 아닌 것: {it!r}"[:120])
                continue
            try:
                into.append(cls(**{k: v for k, v in it.items() if k in known}))
            except TypeError as e:
                got.dropped.append(f"{kind} 한 줄을 못 읽었다: {e}")
    return got


# ─────────────────────────────────────────────────────────────────────
# 겹 3 — 원장과 대조
# ─────────────────────────────────────────────────────────────────────

def certain(open_: Open, book: Book) -> bool:
    """이 고리를 **먼저 말 걸어도 되는 것**으로 볼 수 있나.

    ★ 규칙이 모델 위에서 누른다. 모델이 `sure=True` 라고 해도, 근거가 된
      **그 문장 안에 대상이 없으면** 내린다 — 자리로 추론한 것이기 때문이다.

        "#42 는 내가 오늘 볼게"   문장 안에 대상이 있다      → 그대로
        "내가 볼게"               자리로 추론했다            → 내린다
        "<@U7> 이거 봐줄래?"      구조가 명시했다            → 그대로
    """
    if not open_.sure:
        return False
    line = book.get(open_.source)
    if line is None:
        # 근거를 못 찾겠으면 확신하지 않는다. 지어낸 근거일 수도 있다.
        return False
    return bool(POINTS_AT.search(line.text))


def apply(got: Extraction, loops: LoopBook, book: Book, *,
          bundle: Bundle | None = None, names: dict | None = None,
          mine: list | None = None) -> dict:
    """원장에 옮긴다. **닫는 것이 먼저다.**

    ★ `moves` 를 `opens` 보다 먼저 도는 이유: 못 닫으면 목록이 자라고,
      자란 목록은 안 읽힌다. 안 읽히는 목록은 없는 것과 같다.

    돌려주는 것은 **무엇이 실제로 바뀌었나**다 — 모델이 낸 것이 아니라.
    """
    셈 = {"움직임": 0, "열림": 0, "못 씀": 0, "겹침": 0}
    실린키 = {x.key for x in bundle.lines} if bundle is not None else None

    for mv in got.moves:
        if not loops.get(mv.id):
            # ★ 모델은 지워진 id 를 부른다. 예외로 안 던진다.
            got.dropped.append(f"없는 고리 id: {mv.id}")
            셈["못 씀"] += 1
            continue
        if mv.state and mv.state not in STATES:
            got.dropped.append(f"모르는 상태: {mv.state!r} ({mv.id})")
            셈["못 씀"] += 1
            continue

        # ★ **근거 없이는 안 닫는다.** 여는 쪽에만 걸어 뒀던 규칙인데,
        #   닫는 쪽이 더 급했다 — 실측에서 모델이 이렇게 닫았다:
        #   *"본인이 '다 확인했어'를 말하지 않았으나, 전반적으로 완료된 것으로
        #   판단됨."* 닫는 말이 없는데 닫은 것이다.
        #
        #   잘못 닫으면 그 고리는 목록에서 **조용히 사라진다.** 안 닫힌 고리는
        #   목록에 남아서 사람이 지울 수 있지만, 잘못 닫힌 고리는 사람이 볼
        #   기회 자체가 없다. 두 실패의 값이 대칭이 아니다.
        if mv.state in (DONE, DROPPED):
            if not mv.source:
                got.dropped.append(f"근거 없이 닫으려 했다: {mv.id}")
                셈["못 씀"] += 1
                continue
            if 실린키 is not None and mv.source not in 실린키:
                # 이 묶음에 없는 말을 근거로 댔다 — 모델은 그 말을 못 봤다.
                got.dropped.append(f"묶음에 없는 근거로 닫으려 했다: {mv.id} ({mv.source})")
                셈["못 씀"] += 1
                continue
        loops.move(mv.id, mv.note or "추출", state=mv.state or "")
        셈["움직임"] += 1

    for op in got.opens:
        if not op.text.strip():
            got.dropped.append("본문이 빈 고리")
            셈["못 씀"] += 1
            continue
        if not op.source:
            # ★ 근거 없이는 안 연다(`loops.py`). 사람이 확인할 길이 없으면
            #   한 번 틀렸을 때 원장 전체를 안 믿게 된다.
            got.dropped.append(f"근거 없는 고리: {op.text[:40]}")
            셈["못 씀"] += 1
            continue
        # ★ **같은 일을 두 번 안 연다.** `open()` 은 근거가 같을 때만 막는데,
        #   같은 일이 다른 줄에서 다시 나오는 일이 실제로 있다 — 실측에서 한
        #   고리가 세 번 열렸다. 못 닫는 것만 목록을 키우는 게 아니라
        #   **두 번 여는 것도 키운다.**
        #
        #   버리지 않고 **움직임으로 적는다.** 다시 나왔다는 것 자체가 그 고리가
        #   아직 살아 있다는 정보고, 안 적으면 조용한 날짜만 보고 찌르게 된다.
        있던것 = loops.similar(op.text.strip())
        if 있던것 is not None:
            loops.move(있던것.id, f"또 나왔다 — {op.why or op.text[:40]}")
            셈["겹침"] += 1
            continue

        loops.open(
            op.text.strip(), source=op.source,
            owner=owner_of(op.owner, names, mine),
            due=op.due, sure=certain(op, book), note=op.why or "추출",
        )
        셈["열림"] += 1
    return 셈


# ─────────────────────────────────────────────────────────────────────
# 한 번 돌리기
# ─────────────────────────────────────────────────────────────────────

MAX_OUT = 768
"""한 번에 낼 수 있는 최대 토큰.

★ 실측(2026-08-31): 상한이 2048 이었을 때 한 호출이 **1,031 토큰**을 냈고
  241초가 걸렸다 — 그리고 그 다음 호출이 시간 초과로 죽었다. 이 자리가 내는
  것은 JSON 하나이고, 길어지는 것은 잘 판단한다는 뜻이 아니라 **장황해진다**는
  뜻이다. 잘려도 `parse` 가 그 턴을 통째로 안 버린다.
"""


def ask(client, model: str, prompt: str, body: str, *, max_tokens: int = MAX_OUT) -> str:
    """모델 한 번. **도구를 안 넘긴다.**

    ★ 도구를 넘기면 모델이 도구를 부르려 들고, 그 턴들이 전부 값이다.
      여기가 원하는 것은 JSON 하나뿐이다.
    """
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        system=prompt,
        messages=[{"role": "user", "content": body}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content)
