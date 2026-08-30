"""메시지 스키마와 우편 자리.

━━ 배역은 여기 없다 ━━

방 이름도, 누가 에이전트고 누가 사람인지도 여기서 안 정한다. 쓰는 쪽이
`Cast` 로 선언하고 그걸 넘긴다. 골격이 배역을 들면 에이전트를 하나 더
만들 때마다 골격을 고쳐야 한다.

    cast = Cast(
        agents = ("alpha", "beta"),
        humans = ("owner",),
        rooms  = {
            "alpha-beta":       ("alpha", "beta"),
            "alpha-owner":      ("alpha", "owner"),
            "beta-owner":       ("beta", "owner"),
            "alpha-beta-owner": ("alpha", "beta", "owner"),
        },
        speakers = {"alpha": "알파", "beta": "베타", "owner": "사장"},
    )

━━ 같은 이름이 다른 방을 가리키지 않게 ━━

방 이름은 **절대 이름**이다. "내 방" 처럼 보는 쪽에 따라 달라지는 이름은
여기 두지 않는다 — 두 에이전트가 각자 `ROOM_HOME` 을 자기 방이라고 부르다가
같은 상수가 서로 다른 방을 가리킨 적이 있다. 상대 이름은 쓰는 쪽이 붙인다
(`cast.home_of("alpha")`).

━━ 관대하게 만들지 마라 ━━

여기서 받아 주면 상대가 거절하고, 그러면 배달은 됐는데 상대가 못 읽는
메시지가 생긴다. 그게 제일 나쁜 상태다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .. import clock, env
from ..store import JsonStore, default_root, from_dict

PEER_ONLY = "peer_only"
ALL = "all"
VISIBILITIES = (PEER_ONLY, ALL)


@dataclass(frozen=True)
class Cast:
    """누가 있고 방이 어떻게 생겼나. **쓰는 쪽이 선언한다.**

    `room_type` 은 사람 수로 정한다 — 둘이면 `dm`, 셋 이상이면 `group`.
    따로 적게 하면 적는 사람이 언젠가 안 맞게 적는다.
    """

    agents: tuple[str, ...]
    humans: tuple[str, ...]
    rooms: dict[str, tuple[str, ...]]
    speakers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        known = set(self.agents) | set(self.humans)
        for room, who in self.rooms.items():
            unknown = set(who) - known
            if unknown:
                raise ValueError(f"{room} 에 모르는 사람이 있다: {sorted(unknown)}")
            if len(who) < 2:
                raise ValueError(f"{room} 에 사람이 둘은 있어야 한다: {who}")

    # --- 사람 ---
    @property
    def senders(self) -> tuple[str, ...]:
        return tuple(self.agents) + tuple(self.humans)

    def is_human(self, who: str) -> bool:
        return who in self.humans

    def speaker(self, who: str) -> str:
        """기억에 적을 이름. 안 정했으면 id 를 그대로 쓴다."""
        return self.speakers.get(who, who)

    # --- 방 ---
    def members(self, room: str) -> frozenset[str]:
        return frozenset(self.rooms.get(room, ()))

    def room_type(self, room: str) -> str:
        return "dm" if len(self.rooms.get(room, ())) == 2 else "group"

    def rooms_of(self, who: str) -> tuple[str, ...]:
        return tuple(r for r, m in self.rooms.items() if who in m)

    def home_of(self, who: str) -> str | None:
        """그 사람이 사람과 단둘인 방. 없으면 None."""
        for r, m in self.rooms.items():
            if len(m) == 2 and who in m and any(self.is_human(x) for x in m):
                return r
        return None

    def has_human(self, room: str) -> bool:
        return any(self.is_human(w) for w in self.rooms.get(room, ()))

    def recipients(self, m: "Message") -> frozenset[str]:
        """이 메시지를 배달받는 사람. **라우팅 규칙은 이 함수 하나다.**

            방 사람들 − 보낸 사람 − (peer_only 면 사람들)

        규칙이 두 군데 있으면 언젠가 어긋나고, 어긋나면 조용히 새는 쪽으로
        어긋난다.
        """
        out = set(self.members(m.room_id)) - {m.sender}
        if m.visibility == PEER_ONLY:
            out -= set(self.humans)
        return frozenset(out)


# ── 내가 누구인가 ────────────────────────────────────────────────────
# `mine` · `from_peer` 는 보는 쪽이 누구냐에 따라 답이 다르다. 전역 하나로 두면
# 한 프로세스가 두 에이전트를 import 할 때 나중 것이 앞 것을 덮는다 — 실제로
# 그렇게 새어서, 프리픽스에 달아 둔다.
_who: dict[str, tuple[str, Cast]] = {}


def identify(prefix: str, me: str, cast: Cast) -> None:
    """이 프리픽스로 도는 프로세스에서 "나" 가 누구고 무대가 어떻게 생겼는지."""
    if me not in cast.senders:
        raise ValueError(f"{me} 가 배역에 없다: {cast.senders}")
    _who[prefix.strip().upper()] = (me, cast)


def me() -> str:
    got = _who.get(env.prefix())
    return got[0] if got else ""


def stage() -> Cast:
    got = _who.get(env.prefix())
    if got is None:
        raise RuntimeError(
            "배역이 없다. 패키지가 뜰 때 `peers.identify(프리픽스, 나, Cast(...))` 를 불러라."
        )
    return got[1]


def my_rooms(who: str | None = None) -> tuple[str, ...]:
    return stage().rooms_of(who or me())


def my_home(who: str | None = None) -> str | None:
    return stage().home_of(who or me())


class InvalidMessage(ValueError):
    pass


@dataclass
class Message:
    """계약 문서 1절 스키마. 필드를 늘리거나 줄이지 않는다.

    `delivered` 는 스키마 밖이다 — 파일함이 멱등 처리를 하려고 붙이는 살림살이라
    `to_wire()` 에서 빠진다. 바깥으로 나가는 것은 스키마 일곱 필드뿐이다.
    """

    id: str
    room_type: str
    room_id: str
    sender: str
    content: str
    timestamp: str
    visibility: str = ALL
    delivered: bool = False  # 스키마 밖. 파일함 내부용

    WIRE = ("id", "room_type", "room_id", "sender", "content", "timestamp", "visibility")

    def to_wire(self) -> dict:
        return {k: getattr(self, k) for k in self.WIRE}

    @property
    def from_peer(self) -> bool:
        return self.sender != me() and not self.from_human

    @property
    def from_human(self) -> bool:
        return stage().is_human(self.sender)

    @property
    def mine(self) -> bool:
        return self.sender == me()

    @property
    def speaker(self) -> str:
        """기억에 적을 화자 이름."""
        return stage().speaker(self.sender)


def validate(m: Message) -> Message:
    """스키마를 지키는지 본다. 어기면 받지 않는다.

    관대하게 받아 주고 싶은 유혹이 있는데, 여기서 관대하면 room_id 와 room_type 이
    어긋난 메시지가 기억에 들어가고, 그러면 "단둘이 한 말" 과 "셋이 있는 방에서 한 말"
    구분이 조용히 무너진다. 그 구분이 회상 전체가 서 있는 자리다.
    """
    cast = stage()
    if m.room_id not in cast.rooms:
        raise InvalidMessage(f"모르는 방이다: {m.room_id}")
    if m.room_type != cast.room_type(m.room_id):
        raise InvalidMessage(
            f"room_type 이 room_id 와 안 맞는다: {m.room_id} 는 {cast.room_type(m.room_id)} 여야 한다"
        )
    if m.sender not in cast.senders:
        raise InvalidMessage(f"모르는 발신자다: {m.sender}")
    if m.visibility not in (PEER_ONLY, ALL):
        raise InvalidMessage(f"모르는 visibility 다: {m.visibility}")
    if m.sender not in cast.members(m.room_id):
        # 단둘이 있는 방에 세 번째가 들어오는 경우. 스키마상 표현은 가능하지만
        # 그 방의 뜻이 무너진다.
        raise InvalidMessage(f"{m.sender} 는 {m.room_id} 에 보낼 수 없다")
    # `peer_only` 는 "에이전트끼리만" 을 뜻한다. **아무에게도 안 가면 뜻이 없다** —
    # 사람과 단둘인 방이 그렇다. 사람도 에이전트도 있는 방에서는 뜻이 있다
    # (에이전트는 보고 사람은 못 본다).
    if m.visibility == PEER_ONLY and not cast.recipients(m):
        raise InvalidMessage(f"{m.room_id} 에서 peer_only 는 아무에게도 안 간다")
    if not m.content.strip():
        raise InvalidMessage("빈 메시지")
    return m


def compose(
    content: str,
    room_id: str | None = None,
    sender: str | None = None,
    visibility: str | None = None,
) -> Message:
    """보낼 메시지를 만든다.

    **방을 안 주면 사용자와 단둘인 자리로 간다.** 아무 말이나 꺼낼 때 그게 가는
    곳은 사용자 옆이다. 상대에게 보내려면 방을 명시해야 한다.
    보내는 사람을 안 주면 나다(`me()`).

    `visibility` 는 방이 정한다 — 에이전트와 단둘이 있는 방만 `peer_only`,
    나머지는 `all`. 매번 고르게 하면 언젠가 안 고르고 지나간다.
    """
    room_id = room_id or my_home()
    sender = sender or me()
    if visibility is None:
        # 방이 정한다. 매번 고르게 하면 언젠가 안 고르고 지나간다.
        # 사람이 없는 방(에이전트끼리)만 `peer_only` 다.
        visibility = ALL if stage().has_human(room_id) else PEER_ONLY
    return validate(
        Message(
            id=str(uuid.uuid4()),
            room_type=stage().room_type(room_id),
            room_id=room_id,
            sender=sender,
            content=content,
            timestamp=clock.now_iso(),
            visibility=visibility,
        )
    )


@dataclass
class Flag:
    """사용자에게 올린 "봐야 할 것 같다" 신호.

    **`reason` 은 바깥으로 안 나간다.** 기억 쪽에만 남는다(`to_wire` 참조).
    두 에이전트가 그렇게 정했다 — 에이전트: "봐야 할 것 같다 한 줄만. 시각 범위나
    대목까지 실으면 사용자가 그 순간 뭘 읽게 되는 셈이라 '안 열어본다' 약속이랑
    부딪혀."
    """

    id: str
    ts: str
    agent: str  # 누가 올렸는지
    room_id: str
    reason: str = ""  # 올린 쪽에만 남는다. 알림에 안 실린다
    seen: bool = False  # 사용자가 확인했는지 (바깥이 표시한다)

    def to_wire(self) -> dict:
        """알림 경로로 나가는 것 전부. **reason 도 시각 범위도 없다.**"""
        return {"id": self.id, "ts": self.ts, "agent": self.agent, "room_id": self.room_id}


class FlagBox:
    """신호를 쌓는 자리 — `<상태 디렉토리>/flags.json`.

    코어는 신호를 남기기만 한다. 실제로 사용자에게 알리는 경로(텔레그램 등)는
    배포 쪽이 붙인다. 갈라 둔 이유는 두 가지다.
      - 알림 채널이 죽어도 신호는 남아야 한다. 신호는 "열어봐도 된다" 는 동의라서
        전달 실패로 사라지면 안 된다.
      - 이 파일이 곧 열람 동의의 기록이다. 사용자가 그 방을 열었다면 그 앞에 이게
        있어야 한다.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self._store = JsonStore(Path(root) / "flags.json")

    def all(self) -> list[Flag]:
        return [from_dict(Flag, f) for f in self._store.load({"flags": []})["flags"]]

    def raise_flag(self, agent: str = "", room_id: str = "", reason: str = "") -> Flag:
        flag = Flag(
            id=str(uuid.uuid4()),
            ts=clock.now_iso(),
            agent=agent,
            room_id=room_id,
            reason=reason,
        )
        items = self.all()
        items.append(flag)
        self._store.save({"flags": [asdict(f) for f in items]})
        return flag

    def unseen(self) -> list[Flag]:
        return [f for f in self.all() if not f.seen]

    def mark_seen(self, ids: list[str]) -> int:
        items = self.all()
        n = 0
        for f in items:
            if f.id in ids and not f.seen:
                f.seen = True
                n += 1
        if n:
            self._store.save({"flags": [asdict(f) for f in items]})
        return n


class Transport(Protocol):
    """보내고 받는 방법. 붙는 쪽이 이걸 구현해서 넘겨주면 코어는 안 고친다."""

    def send(self, message: Message) -> bool: ...

    def poll(self) -> list[Message]: ...


@dataclass
class _Box:
    messages: list[Message] = field(default_factory=list)


class FileBox:
    """파일 두 개로 도는 Transport 기본 구현.

        <상태 디렉토리>/inbox.json   메신저가 넣는다 → 내가 읽는다
        <상태 디렉토리>/outbox.json  내가 넣는다   → 메신저가 집어간다

    `delivered` 로 멱등 처리한다. `poll()` 은 아직 안 읽은 것만 돌려주고 읽은
    것으로 표시한다. 같은 메시지를 두 번 밀어 넣어도 id 가 같으면 한 번만 들어간다.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self.root = Path(root)
        self._in = JsonStore(self.root / "inbox.json")
        self._out = JsonStore(self.root / "outbox.json")

    # --- 받기 ---

    def _load(self, store: JsonStore) -> list[Message]:
        raw = store.load({"messages": []})
        return [from_dict(Message, m) for m in raw["messages"]]

    def _save(self, store: JsonStore, items: list[Message]) -> None:
        store.save({"messages": [asdict(m) for m in items]})

    def deliver(self, message: Message) -> Message:
        """바깥(메신저)이 나에게 메시지를 넣는 자리. 중복 id 는 무시한다."""
        validate(message)
        items = self._load(self._in)
        if any(m.id == message.id for m in items):
            return next(m for m in items if m.id == message.id)
        message.delivered = False
        items.append(message)
        self._save(self._in, items)
        return message

    def poll(self) -> list[Message]:
        """아직 안 읽은 수신 메시지. 돌려주면서 읽은 것으로 표시한다."""
        items = self._load(self._in)
        fresh = [m for m in items if not m.delivered]
        if fresh:
            for m in fresh:
                m.delivered = True
            self._save(self._in, items)
        return fresh

    def pending(self) -> list[Message]:
        """읽었는지 표시하지 않고 보기만 한다 (브리핑·디버깅용)."""
        return [m for m in self._load(self._in) if not m.delivered]

    # --- 보내기 ---

    def send(self, message: Message) -> bool:
        """보낼 것을 아웃박스에 쌓는다. 실제 전달은 메신저가 한다.

        **항상 True 를 돌려준다.** 파일에 적히면 입장에서는 말한 것이다 —
        전달이 실패해도 말하기로 한 판단은 이미 일어난 사실이고, 그건 기록에
        남아야 한다(에이전트 쪽에서 확인된 것을 그대로 가져왔다).
        """
        validate(message)
        items = self._load(self._out)
        if any(m.id == message.id for m in items):
            return True
        items.append(message)
        self._save(self._out, items)
        return True

    def outgoing(self, only_undelivered: bool = True) -> list[Message]:
        items = self._load(self._out)
        return [m for m in items if not m.delivered] if only_undelivered else items

    def mark_sent(self, ids: list[str]) -> int:
        """메신저가 실제로 전달한 뒤 표시하는 자리."""
        items = self._load(self._out)
        n = 0
        for m in items:
            if m.id in ids and not m.delivered:
                m.delivered = True
                n += 1
        if n:
            self._save(self._out, items)
        return n
