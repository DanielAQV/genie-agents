"""대화 기록 — 오간 말을 그대로 든다. **신호가 아니다.**

`world` 와 갈라 둔 이유가 셋이다. 한때 여기에 부으려 했다(`docs/wiring.md` 8단계
3번의 "world 에 쌓기").

  시각    `world.ingest` 는 `ts` 를 **적재 시각**으로 찍는다. 말한 시각이 아니면
          시간창도 `quiet_days` 도 전부 틀린다
  중복    `world` 는 `(source, title)` 로 막는다. "ㅇㅋ" 를 두 번 치면 뒷것이
          조용히 사라진다. 여기는 **근거 키**로 막는다
  소음    `world_recent` 는 모델이 부르는 브리핑 도구다(`kit/world.py`).
          팀원 잡담이 거기 섞이면 *"소음이 가장 큰 위험"* 이 골격 안쪽에서 터진다

★ **여기에 kit 도구를 안 단다.** 원문은 모델이 꺼내 보는 것이 아니라 추출이
  묶어서 한 번 싣는 것이다. 도구로 달면 모델이 아무 방이나 되짚어 읽게 되고,
  읽는 범위를 세 자리로 좁힌 결정이 그 자리에서 무의미해진다.

━━ 근거 키가 신원이다 ━━

    slack:<channel>:<ts>          말 하나
    mail:<account>:<thread_id>    메일 스레드 하나

★ 이 키가 그대로 `loops.Loop.source` 로 간다. `LoopBook.open()` 이 같은 근거로
  두 번 안 여는 것과 `Book.put()` 이 같은 키를 두 번 안 넣는 것은 **같은 규칙이
  두 층에 있는 것**이다. 폴링이 겹치고, 수정된 메시지가 새것처럼 오고, 창이
  벌어져 다시 훑는 일 — 셋 다 실제로 있다.

★ 키에서 **링크가 복원돼야 한다**(`url`). 저녁 목록의 한 줄을 사람이 못 눌러
  보면 "이거 하기로 했잖아" 와 같아지고, 그러면 원장 전체를 안 믿는다.

━━ 원문은 오래 안 둔다 ━━

★ `prune()` 이 이 파일 하나에만 걸린다. 그래서 갈라 뒀다 — `world.json` 과
  한 파일이면 보존 정책을 종류별로 갈라야 하고, 그런 정책은 언젠가 한쪽에만
  적용된다.

  **지워도 고리는 안 없어진다.** `Loop` 는 `text` 를 자기 복사본으로 들고
  `source` 로 자리를 가리킨다. 원문이 사라져도 원장은 성하고, 링크도 복원된다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import clock
from .store import JsonStore, default_root, from_dict

MENTION = re.compile(r"<@([UW][A-Z0-9]+)")

ME = "나"
"""말한 사람이 본인일 때 `Line.who` 에 들어가는 값. `loops.ME` 와 같은 말이다."""

# 묶음 상한 — `docs/wiring.md` 5절. **창을 넓힐수록 정확해지지 않는다. 소음이 는다.**
THREAD_MAX = 20   # 스레드 하나에서 싣는 말
RECENT_MAX = 10   # 스레드가 없을 때 직전 몇 개
WINDOW_MIN = 30   # 그 대신 볼 시간창(분)


@dataclass
class Line:
    """오간 말 하나."""

    key: str
    """근거 키. **신원이자 중복 방지다.** 그대로 `Loop.source` 로 간다."""

    room: str
    who: str
    """말한 사람. 본인이면 `ME`("나") 로 들어온다 — 어댑터가 그렇게 적는다.

    ★ 겹 1 규칙 넷이 전부 *"내가 친 말인가"* 를 묻는다. 원시 id 로 두고 매번
      본인 id 와 비교하게 하면, 비교를 빠뜨린 자리 하나가 남의 말을 내 말로 센다.
    """

    text: str
    ts: str
    """★ **말한 시각.** 적재 시각이 아니다. ISO 로 둔다 — Slack 의 `1756...` 도
    메일의 RFC 날짜도 어댑터가 여기로 옮겨 온다."""

    thread: str = ""
    """스레드 id(방 안에서만 뜻이 있다). 스레드에 속하지 않으면 빈 문자열."""

    url: str = ""
    edited: str = ""
    """고쳐진 말이면 그 표시. 키가 같아 덮어쓰는 자리를 가른다."""

    @property
    def mine(self) -> bool:
        return self.who == ME

    @property
    def mentions(self) -> list[str]:
        """이 말이 부른 사람들. **저장하지 않고 본문에서 꺼낸다** — 두 군데 있으면
        언젠가 갈리고, 갈리면 멘션을 놓치는 쪽으로 갈린다."""
        return MENTION.findall(self.text)

    def calls(self, who: str) -> bool:
        return who in self.mentions


@dataclass
class Bundle:
    """추출 한 번에 싣는 것 — **방 하나 × 시간창 하나**.

    ★ 후보를 하나씩 부르지 않는다. 오전의 "내가 볼게" 와 오후의 "다 봤어" 가
      같은 묶음 안에 있어야 열자마자 닫는다(`docs/wiring.md` 5절).
    """

    room: str
    lines: list[Line] = field(default_factory=list)
    thread: str = ""
    """스레드를 판 묶음이면 그 id. 아니면 빈 문자열."""

    def __len__(self) -> int:
        return len(self.lines)

    @property
    def span(self) -> tuple[str, str]:
        return (self.lines[0].ts, self.lines[-1].ts) if self.lines else ("", "")


class Book:
    """오간 말을 쌓고, 묶음으로 꺼내고, 오래된 것을 버린다."""

    def __init__(self, root: Path | str | None = None) -> None:
        # 기본 인자로 두면 def 를 읽는 순간 굳어서 프리픽스가 걸리기 전 값에
        # 앉는다(`store.default_root`). 부를 때 정한다.
        self._store = JsonStore(
            Path(root if root is not None else default_root()) / "transcript.json"
        )
        raw = self._store.load({"lines": [], "threads": {}})
        self._items: list[Line] = [from_dict(Line, d) for d in raw.get("lines", [])]
        self._by_key: dict[str, Line] = {x.key: x for x in self._items}
        self._threads: dict[str, dict[str, str]] = {
            room: dict(ts) for room, ts in (raw.get("threads") or {}).items()
        }

    def __len__(self) -> int:
        return len(self._items)

    # --- 쌓기 ---

    def put(self, line: Line) -> bool:
        """넣는다. **같은 키가 이미 있으면 안 넣는다** — `True` 면 새것이다.

        ★ 다만 *고쳐진 말*은 덮어쓴다. 같은 자리의 말이 바뀐 것이지 새 말이
          아니라서, 두 벌로 두면 추출이 옛 문장과 새 문장을 둘 다 읽는다.
        """
        return self.put_many([line]) == 1

    def put_many(self, lines) -> int:
        """여럿을 한 번에. 새로 들어온 개수를 돌려준다.

        ★ **쓰기는 마지막에 한 번**이다. 창이 벌어진 아침엔 한 장에 200줄이
          오고, 줄마다 파일을 다시 쓰면 그게 곧 지연이 된다.
        """
        새것 = 0
        바뀜 = False
        for line in lines:
            old = self._by_key.get(line.key)
            # 자국은 줄이 새것이 아니어도 늘 수 있다 — 옛 줄을 다시 긁어 온
            # 자리에서도 그 스레드가 아직 살아 있다는 것은 새 정보다.
            바뀜 = self._note(line) or 바뀜
            if old is None:
                self._items.append(line)
                self._by_key[line.key] = line
                새것 += 1
                바뀜 = True
            elif line.edited and line.edited != old.edited:
                self._items[self._items.index(old)] = line
                self._by_key[line.key] = line
                바뀜 = True
        if 바뀜:
            self._items.sort(key=lambda x: clock.parse(x.ts))
            self._flush()
        return 새것

    def _note(self, line: Line) -> bool:
        """스레드를 **원문과 따로** 적어 둔다. 여기가 이 원장의 기억이다.

        ★ `prune` 이 원문을 버려도 이건 남는다. 안 그러면 **오래 가는 스레드일수록
          안 보이게 된다** — `history` 는 창 밖 원글을 안 주고, 다시 팔 스레드
          목록마저 사라지면 그 스레드는 영영 안 들어온다.

        ★ 남기는 것은 **id 와 시각뿐, 글은 아니다.** wiring.md §9 가 적어 둔
          *"그 뒤엔 고리와 근거 키만"* 이 정확히 이 모양이다.
        """
        if not line.thread:
            return False
        방 = self._threads.setdefault(line.room, {})
        if line.thread not in 방 or clock.parse(line.ts) > clock.parse(방[line.thread]):
            방[line.thread] = line.ts
            return True
        return False

    # --- 꺼내기 ---

    def get(self, key: str) -> Line | None:
        return self._by_key.get(key)

    def rooms(self) -> list[str]:
        return sorted({x.room for x in self._items})

    def lines(self, room: str = "", *, since: str = "", until: str = "") -> list[Line]:
        """시각 순으로. 빈 인자는 안 거른다.

        ★ 시각 비교를 글자로 하면 안 된다 — 시간대 표기가 섞이면 "+09:00" 과
          "+00:00" 이 글자 순서로 비교돼 조용히 뒤집힌다(`wake.last` 와 같은 자리).
        """
        got = self._items if not room else [x for x in self._items if x.room == room]
        if since:
            바닥 = clock.parse(since)
            got = [x for x in got if clock.parse(x.ts) >= 바닥]
        if until:
            천장 = clock.parse(until)
            got = [x for x in got if clock.parse(x.ts) <= 천장]
        return sorted(got, key=lambda x: clock.parse(x.ts))

    def thread(self, room: str, thread: str, limit: int = THREAD_MAX) -> list[Line]:
        """스레드 하나. **원글이 앞에 오고, 넘치면 가운데를 버린다.**

        ★ 상한에 걸릴 때 앞을 자르면 안 된다 — 원글이 곧 가리키는 대상이고,
          그게 없으면 "다 확인했어" 가 무엇인지 영영 못 찾는다.
        """
        got = sorted((x for x in self._items if x.room == room and x.thread == thread),
                     key=lambda x: clock.parse(x.ts))
        if len(got) <= limit or limit < 2:
            return got[:limit] if limit else got
        return [got[0], *got[-(limit - 1):]]

    def bundle(
        self,
        room: str,
        *,
        thread: str = "",
        at: str = "",
        recent: int = RECENT_MAX,
        window_minutes: float = WINDOW_MIN,
        limit: int = THREAD_MAX,
    ) -> Bundle:
        """추출 한 번에 실을 것을 고른다.

        스레드를 주면 그 스레드(원글 + 답글). 안 주면 **직전 `recent` 개 또는
        시간창 안** — 둘 중 넓은 쪽이다.

        ★ 넓은 쪽인 이유: 조용한 방에서는 30분 창이 통째로 비고, 시끄러운 방에서는
          10개가 30분을 못 덮는다. 하나만 쓰면 그 방에서만 조용히 짧아진다.
        """
        if thread:
            return Bundle(room=room, thread=thread, lines=self.thread(room, thread, limit))

        got = self.lines(room, until=at)
        if not got:
            return Bundle(room=room)
        끝 = clock.parse(at) if at else clock.parse(got[-1].ts)
        창 = [x for x in got
              if (끝 - clock.parse(x.ts)).total_seconds() <= window_minutes * 60]
        골라 = 창 if len(창) >= recent else got[-recent:]
        return Bundle(room=room, lines=골라[-limit:])

    def threads(self, room: str = "", *, newer_than_days: float = 0,
                at: str | None = None) -> list[str]:
        """살아 있는 스레드 id 들. **어느 스레드를 다시 파 볼지**를 여기서 안다.

        ★ `conversations.history` 는 **스레드 답글을 안 준다.** 원글이 창 밖에
          있는 스레드에 붙은 답글은 창을 아무리 넓혀도 안 온다 — 그것을 찾는
          유일한 길이 "내가 아는 스레드를 다시 판다" 이고, 그 목록이 여기다.

        ★ **원문이 아니라 자국을 본다**(`_note`). 원문은 72시간이면 버려지는데
          스레드는 그보다 오래 산다 — 실측으로 한 방의 대화 **44%가 스레드
          안**에 있었다. 원문에서 목록을 만들면 오래 가는 스레드일수록 안
          보이게 되고, 그건 창을 넓혀도 안 고쳐진다.

        새것이 앞이다. 상한을 거는 쪽이 앞에서부터 자르면 된다.
        """
        마지막: dict[str, str] = {}
        for r, 방 in self._threads.items():
            if room and r != room:
                continue
            마지막.update(방)
        if newer_than_days > 0:
            마지막 = {t: ts for t, ts in 마지막.items()
                    if clock.elapsed_minutes(ts, at) / 1440 <= newer_than_days}
        return [t for t, _ in sorted(마지막.items(),
                                     key=lambda kv: clock.parse(kv[1]), reverse=True)]

    def thread_at(self, room: str, thread: str) -> str:
        """그 스레드에서 마지막으로 본 말의 시각. 다시 팔 때 `oldest` 로 쓴다 —
        **매시 백 줄짜리 스레드를 통째로 다시 받아 오지 않는다.**"""
        return (self._threads.get(room) or {}).get(thread, "")

    # --- 버리기 ---

    def prune(self, hours: float = 72, at: str | None = None,
              thread_days: float = 30) -> int:
        """이보다 오래된 원문을 버린다. 지운 개수를 돌려준다.

        ★ **고리는 안 없어진다.** `Loop` 가 `text` 를 복사본으로 들고 `source`
          로 자리를 가리키므로, 원문이 사라져도 원장은 성하고 링크도 복원된다.
          여기가 §9 의 *"원문은 따라잡기 창만 두고 그 뒤엔 고리와 근거 키만"* 이
          실제로 걸리는 한 줄이다.
        """
        남길 = [x for x in self._items if clock.elapsed_minutes(x.ts, at) <= hours * 60]
        지운수 = len(self._items) - len(남길)

        # ★ 스레드 자국은 **원문보다 오래 든다.** 글이 아니라 id 와 시각뿐이라
        #   싸고, 이게 없으면 오래 가는 스레드가 통째로 안 보인다. 그래도
        #   영영 안 버리지는 않는다 — 한 달 조용한 스레드는 다시 안 판다.
        옛자국 = {
            room: {t: ts for t, ts in 방.items()
                   if clock.elapsed_minutes(ts, at) / 1440 <= thread_days}
            for room, 방 in self._threads.items()
        }
        옛자국 = {room: 방 for room, 방 in 옛자국.items() if 방}
        자국바뀜 = 옛자국 != self._threads

        if 지운수 or 자국바뀜:
            self._items = 남길
            self._by_key = {x.key: x for x in 남길}
            self._threads = 옛자국
            self._flush()
        return 지운수

    def _flush(self) -> None:
        self._store.save({
            "lines": [asdict(x) for x in self._items],
            "threads": self._threads,
        })
