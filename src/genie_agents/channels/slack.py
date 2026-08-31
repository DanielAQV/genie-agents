"""Slack — **보는 자리**. 읽기만 한다.

말하는 자리(봇 DM)는 여기 없다. `SlackTransport` 가 5단계에서 따로 온다 —
자리를 둘로 가른 이유는 `channels/__init__.py` 첫머리에 있다.

━━ 사용자 토큰은 이벤트를 못 받는다 ━━

Events API·Socket Mode 는 앱(봇) 기준이다. 그래서 **`conversations.history`
폴링**이 정석이고, 방마다 `last_ts` 를 든다.

★ 상시 프로세스가 없으니 60초짜리 폴링도 없다. 꺼져 있던 시간만큼 창이 벌어질
  뿐이고 `oldest=커서` 가 그걸 그대로 메운다.

★ **창이 벌어지면 페이지가 여러 장 온다.** 상시 폴링에서는 한 장이면 끝이라
  `has_more`·`next_cursor` 를 안 돌려도 티가 안 났다. 월요일 아침엔 티가 난다 —
  **첫 장만 읽으면 금요일 오후가 통째로 사라진다.**

━━ 스레드 답글은 history 에 없다 ━━

★ `conversations.history` 는 **스레드 답글을 안 준다.** 원글이 창 밖에 있는
  스레드에 오늘 붙은 답글은, 창을 아무리 넓혀도 안 온다. 창을 넓히는 것으로는
  절대 안 잡히는 종류의 구멍이다.

  그래서 `conversations.replies` 로 한 겹 더 판다. **어느 스레드를 팔지**는
  원장이 안다 — 이번에 긁힌 것 + `Book.threads()` 가 아는 살아 있는 것.

━━ 안 옮기는 것 ━━

`<@U123>` `<#C1|일반>` `<https://…|글자>` 를 **안 푼다.** 사람 이름으로 바꾸는
것은 추출이 묶음을 실을 때 하는 일이고(4단계), 여기서 풀면 원문이 아니게 된다.
`Line.mentions` 도 이 날것 표기를 읽는다.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..transcript import ME, Book, Line

API = "https://slack.com/api/"

PAGE = 200
"""한 장에 몇 개. Slack 권장 상한이 200이다."""

MAX_PAGES = 30
"""한 방 한 번에 볼 최대 장수. **무한 루프를 막는 값**이지 창을 자르는 값이
아니다 — 200×30 이면 한 방 6000개다. 여기 걸리면 그것 자체가 알림이다."""

RETRIES = 3
"""429·5xx 를 몇 번까지 다시 물어보나."""

SKIP = frozenset({
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive",
    "group_join", "group_leave", "group_topic", "group_purpose", "group_name",
    "thread_broadcast_join", "tombstone", "message_deleted",
})
"""버리는 subtype. **들어왔다 나갔다는 신호가 아니다.**"""


class SlackError(RuntimeError):
    """`ok: false` 로 돌아온 것. 무엇이 틀렸는지 그대로 들고 온다."""

    def __init__(self, method: str, error: str, detail: str = "") -> None:
        self.method, self.error = method, error
        super().__init__(f"{method}: {error}" + (f" ({detail})" if detail else ""))


def _urlopen(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        # 429 는 예외로 오지만 **에러가 아니다** — 기다렸다 다시 물어보라는 답이다.
        return e.code, dict(e.headers or {}), e.read()


class Slack:
    """얇은 HTTP 한 겹. 토큰 하나에 `call` 하나.

    ★ `http` 와 `sleep` 을 갈아 끼울 수 있게 둔 것은 시험 때문만이 아니다.
      **자격 증명 없이 도는 자리를 남겨 두는 것**이 이 골격의 규칙이다
      (`check` 가 키 없이 도는 것과 같다).
    """

    def __init__(self, token: str, *, http=None, sleep=None, timeout: float = 20) -> None:
        self.token = token
        self._http = http or _urlopen
        self._sleep = sleep or time.sleep
        self.timeout = timeout
        self.calls = 0

    def call(self, method: str, **params) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "genie-agents/0.1",
        }
        clean = {k: v for k, v in params.items() if v not in ("", None)}
        url = API + method + ("?" + urllib.parse.urlencode(clean) if clean else "")

        for attempt in range(RETRIES + 1):
            self.calls += 1
            status, head, body = self._http(url, headers, self.timeout)
            if status == 429 or status >= 500:
                if attempt == RETRIES:
                    raise SlackError(method, "ratelimited" if status == 429 else "server",
                                     f"HTTP {status}")
                # Retry-After 를 **믿는다.** 우리가 정한 backoff 로 덮으면
                # 그 창을 또 두드려서 상한이 더 길어진다.
                self._sleep(float(head.get("Retry-After") or (attempt + 1)))
                continue
            try:
                data = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise SlackError(method, "bad_json", str(e)) from None
            if not data.get("ok"):
                raise SlackError(method, str(data.get("error") or "unknown"),
                                 str(data.get("needed") or ""))
            return data
        raise SlackError(method, "unreachable")  # pragma: no cover

    def paged(self, method: str, key: str = "messages", **params):
        """`has_more` · `next_cursor` 를 끝까지 따라간다.

        ★ 이 함수가 있는 이유 하나가 위 첫머리의 *"금요일 오후"* 다.
        """
        cursor = ""
        for _ in range(MAX_PAGES):
            data = self.call(method, cursor=cursor, limit=PAGE, **params)
            yield from data.get(key, [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor") or ""
            if not data.get("has_more") or not cursor:
                return


def stamp(ts: str) -> str:
    """Slack 의 `"1756000000.000100"` → ISO(UTC).

    ★ 원시 ts 는 `key` 와 `thread` 에만 남는다. 시각 비교는 전부 ISO 로 한다 —
      메일도 Teams 도 같은 자리에 들어오는데 저마다 다른 시각 표기를 들고 온다.
    """
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def epoch(iso: str) -> str:
    """`stamp` 의 반대. ISO → Slack 의 `"1756000000.000100"`.

    커서와 `oldest` 는 Slack 의 표기로 돌려줘야 한다. 원장은 ISO 로 들고 있어서
    (메일·Teams 와 같은 자리라) 나갈 때 한 번 되돌린다.
    """
    from .. import clock

    return f"{clock.parse(iso).timestamp():.6f}"


def key_of(room: str, ts: str) -> str:
    return f"slack:{room}:{ts}"


def parse_key(key: str) -> tuple[str, str]:
    """근거 키에서 `(방, ts)` 를 되돌린다. 아니면 `("", "")`."""
    part = key.split(":")
    return (part[1], part[2]) if len(part) == 3 and part[0] == "slack" else ("", "")


def permalink(team_url: str, room: str, ts: str, thread: str = "") -> str:
    """근거 키에서 링크를 **복원**한다. API 를 한 번 더 안 부른다.

    ★ 저녁 목록의 한 줄을 사람이 못 눌러 보면 "이거 하기로 했잖아" 와 같아지고,
      그러면 원장 전체를 안 믿는다. 그래서 링크는 있으면 좋은 것이 아니다.

    ★ 팀 주소를 모르면 **빈 문자열을 준다.** 틀린 링크는 없는 링크보다 나쁘다 —
      한 번 눌러서 안 열리면 그 다음부터 아무도 안 누른다.
    """
    if not team_url:
        return ""
    base = team_url.rstrip("/")
    link = f"{base}/archives/{room}/p{ts.replace('.', '')}"
    if thread and thread != ts:
        link += f"?thread_ts={thread}&cid={room}"
    return link


@dataclass
class SlackWatch:
    """관찰 대상. **`SignalSource` 가 아니다** — 내놓는 것은 `Line` 이다.

        watch = SlackWatch(Slack(token), rooms=["C01", "D02"])
        watch.catchup(book, cursors)      # 긁고 → 쌓고 → 커서를 옮긴다
    """

    slack: Slack
    rooms: tuple[str, ...] = ()
    me: str = ""
    """본인 사용자 id. 비우면 `auth.test` 로 한 번 알아 온다."""

    team_url: str = ""
    """`https://팀.slack.com/`. 비우면 `auth.test` 가 준다."""

    skip_bots: bool = True
    """봇이 친 말을 버린다. ★ 값으로 올려 둔 것은 **이게 판단**이라서다 —
    GitHub 알림 봇을 근거로 쓸 수도 있지만, 지금은 근거를 GitHub API 에서
    받기로 했으므로(`followup.md`) 여기서는 소음이다."""

    first_days: float = 3
    """커서가 없는 첫 날 얼마나 거슬러 올라가나. **72시간 보존과 같은 값**이다 —
    더 긁어와도 `Book.prune` 이 곧 버린다."""

    thread_days: float = 3
    """이만큼 안에 움직인 스레드는 다시 파 본다."""

    max_threads: int = 20
    """한 방 한 번에 다시 팔 스레드 수. 새것부터."""

    _known: dict = field(default_factory=dict, repr=False)

    # --- 나는 누구인가 ---

    def whoami(self) -> tuple[str, str]:
        """`(내 id, 팀 주소)`. **한 번만 묻는다.**"""
        if not self.me or not self.team_url:
            data = self.slack.call("auth.test")
            self.me = self.me or str(data.get("user_id") or "")
            self.team_url = self.team_url or str(data.get("url") or "")
        return self.me, self.team_url

    # --- 옮기기 ---

    def line(self, room: str, msg: dict, thread_hint: str = "") -> Line | None:
        """Slack 메시지 하나 → `Line`. 버릴 것은 `None`."""
        ts = str(msg.get("ts") or "")
        if not ts:
            return None
        sub = msg.get("subtype") or ""
        if sub in SKIP:
            return None
        if self.skip_bots and (sub == "bot_message" or msg.get("bot_id")) and not msg.get("user"):
            return None
        text = str(msg.get("text") or "").strip()
        if not text:
            # 파일만 올린 말·빈 말. **버리지만 조용히 버린다** — 첨부는
            # 이 물건이 읽는 것이 아니다(읽는 범위를 좁힌 것이 결정이다).
            return None
        me, team = self.whoami()
        who = str(msg.get("user") or msg.get("bot_id") or "")
        return Line(
            key=key_of(room, ts),
            room=room,
            who=ME if who and who == me else who,
            text=text,
            ts=stamp(ts),
            thread=str(msg.get("thread_ts") or thread_hint or ""),
            url=permalink(team, room, ts, str(msg.get("thread_ts") or thread_hint or "")),
            edited=str((msg.get("edited") or {}).get("ts") or ""),
        )

    # --- 긁기 ---

    def history(self, room: str, oldest: str = "", latest: str = "") -> list[Line]:
        """그 방의 새 말. **페이지를 끝까지 판다.**

        `latest` 는 **그때인 척하는 자리**다. 안 주면 지금까지.
        """
        got = []
        for msg in self.slack.paged("conversations.history", channel=room,
                                    oldest=oldest, latest=latest):
            line = self.line(room, msg)
            if line is not None:
                got.append(line)
        return got

    def replies(self, room: str, thread: str, since: str = "",
                latest: str = "") -> list[Line]:
        """스레드 하나. `since`(ISO)를 주면 그 뒤에 붙은 것만.

        ★ `since` 를 주는 쪽이 **원글을 이미 들고 있는지 확인해야 한다.**
          원글 없이 답글만 쌓이면 묶음이 가리키는 대상을 잃는다 — 그러면
          "다 확인했어" 가 무엇인지 영영 못 찾는다(`transcript.Book.thread`).
        """
        got = []
        for msg in self.slack.paged("conversations.replies", channel=room, ts=thread,
                                    oldest=epoch(since) if since else "",
                                    latest=latest):
            line = self.line(room, msg, thread_hint=thread)
            if line is not None:
                got.append(line)
        return got

    def _since(self, room: str, cursors, at=None) -> str:
        at_cursor = cursors.get(f"slack:{room}")
        if at_cursor:
            return at_cursor
        now = datetime.now(timezone.utc) if at is None else at
        return f"{now.timestamp() - self.first_days * 86400:.6f}"

    def catchup(self, book: Book, cursors, at=None) -> dict[str, int]:
        """긁고 → 쌓고 → **그 다음에** 커서를 옮긴다. 방마다 새로 들어온 개수.

        ★ 순서가 규칙이다(`cursors.py`). 먼저 옮기면 그 사이에 죽었을 때 그
          창이 영영 안 온다. 다시 긁는 것은 값이 싸고, 안 긁는 것은 값이 없다.

        ★ **커서는 `history` 가 준 ts 로만 옮긴다.** 스레드 답글의 ts 는 더 클 수
          있는데 그걸로 커서를 밀면, 다음번 `oldest` 가 아직 안 읽은 윗줄을
          건너뛸 자리가 생긴다. 답글을 놓치지 않는 것은 커서가 아니라
          `Book.threads()` 가 맡는다.

        ★ 방 하나가 죽어도 나머지는 들어온다(`world.poll_all` 과 같은 생각).
          토큰 하나가 한 방에만 없는 일이 실제로 있다 — 안 부른 단톡방 하나 때문에
          DM 두 자리가 통째로 안 들어오면 그날 원장이 통째로 빈다.
        """
        # ★ **`at` 은 창의 바닥만이 아니라 천장이기도 하다.** 전에는 `_since`
        #   에만 걸려서 `at=8/15` 로 불러도 8/12~**오늘**을 긁어 왔다 — 그때인
        #   척하는 것이 아니라 그냥 더 많이 긁는 것이었다. 리허설이 성립하려면
        #   천장이 있어야 한다.
        천장 = epoch(at.isoformat() if hasattr(at, "isoformat") else at) if at else ""

        새것: dict[str, int] = {}
        for room in self.rooms:
            try:
                oldest = self._since(room, cursors, at)
                lines = self.history(room, oldest, latest=천장)
                끝 = max((parse_key(x.key)[1] for x in lines), default="")

                # ★ **순서가 있는 목록이어야 한다.** 집합으로 모아 `[:상한]` 을
                #   씌우면 파이썬 집합 순서대로 잘려서 **매번 아무 스레드나
                #   빠진다.** 실측으로 한 방에 살아 있는 스레드가 22개였고
                #   상한이 20이었다 — 조용히 둘씩 사라지는 중이었다.
                #
                #   이번에 긁힌 것이 먼저다(지금 움직인 스레드다). 그 다음이
                #   원장이 아는 것, 새것부터.
                볼스레드: list[str] = []
                본것: set[str] = set()
                for t in ([x.thread for x in lines if x.thread]
                          + book.threads(room, newer_than_days=self.thread_days, at=at)):
                    if t not in 본것:
                        본것.add(t)
                        볼스레드.append(t)

                for thread in 볼스레드[: self.max_threads]:
                    # ★ 마지막으로 본 데부터만 받는다. 안 그러면 매시 스레드를
                    #   통째로 다시 받아 온다 — 백 줄짜리 스레드가 몇 개만 있어도
                    #   그게 이 물건이 쓰는 값의 대부분이 된다.
                    #
                    # ★ 다만 **원글을 들고 있을 때만** 그렇게 한다. 원문이
                    #   버려진 뒤(72시간)에는 원글부터 다시 받아야 한다 —
                    #   답글만 쌓이면 묶음이 가리키는 대상을 잃는다.
                    있다 = book.get(key_of(room, thread)) is not None
                    lines += self.replies(
                        room, thread,
                        since=book.thread_at(room, thread) if 있다 else "",
                        latest=천장)

                새것[room] = book.put_many(lines)
                cursors.set(f"slack:{room}", 끝)   # ★ 쌓은 뒤에
            except SlackError as e:
                self._known[room] = str(e)
                새것[room] = 0
        return 새것

    @property
    def problems(self) -> dict:
        """마지막 `catchup` 에서 죽은 방들. **삼키고 끝내지 않는다** —
        조용히 안 읽히는 방이 있으면 원장이 틀린 채로 자란다."""
        return dict(self._known)


# ── 방 id 를 찾는 자리 ───────────────────────────────────────────────
#
# ★ 이건 도는 물건이 아니라 **한 번 쓰고 마는 자**다. `[watch] slack` 에 넣을
#   id 를 사람이 Slack UI 에서 캐내야 하는데, DM 은 거기서 잘 안 보인다.
#   손일 하나를 없애자고 두는 것이지 루프가 부르는 자리가 아니다.

KINDS = {
    "im": "DM",
    "mpim": "여럿DM",
    "private_channel": "비공개",
    "public_channel": "공개",
}


def people(slack: Slack) -> dict[str, dict]:
    """`users.list` 한 번 → `{id: {"name", "bot", "gone"}}`.

    표시 이름을 먼저 본다 — `<@U123>` 이 누구인지는 **사람이 부르는 이름**이고,
    `name`(핸들)은 그 사람이 자기를 부르는 이름이 아닐 때가 많다.

    ★ `bot` 과 `gone` 을 같이 든다. 이 워크스페이스에는 앱 DM(Jira·GitHub·
      Slackbot·번역기…)과 퇴사자 DM 이 **사람 DM 보다 많다.** 거를 근거가
      없으면 방을 고르는 일이 목록을 눈으로 훑는 일이 된다.
    """
    got = {}
    for u in slack.paged("users.list", key="members"):
        p = u.get("profile") or {}
        got[u.get("id")] = {
            "name": (p.get("display_name") or p.get("real_name") or u.get("real_name")
                     or u.get("name") or u.get("id")),
            "bot": bool(u.get("is_bot") or u.get("id") == "USLACKBOT"),
            "gone": bool(u.get("deleted")),
        }
    return got


def names(slack: Slack) -> dict[str, str]:
    """`{id: 이름}` 만. 4단계 묶음에 싣는 **사람 이름 표**가 이것이다."""
    return {k: v["name"] for k, v in people(slack).items()}


def rooms(slack: Slack, types: str = ",".join(KINDS), *,
          who: dict | None = None, humans_only: bool = True, me: str = "") -> list[dict]:
    """이 사용자가 든 방들. `[watch] slack` 에 붙여 넣을 수 있게 낸다.

    ★ **보관된 방은 뺀다.** 안 빼면 목록이 옛 방으로 차고, 거기서 고르다
      죽은 방을 하나 넣게 된다 — 그러면 매시 그 방을 헛되이 두드린다.

    ★ `humans_only` 는 **DM 에만 걸린다.** 앱 DM 과 퇴사자 DM 을 뺀다.
      이건 정보를 감추는 것이 아니라 **고를 수 있게 하는 것**이다 — 백 줄짜리
      목록에서 셋을 고르는 일과 스무 줄에서 셋을 고르는 일은 다른 일이다.
      전부 보려면 `humans_only=False`.
    """
    who = people(slack) if who is None else who
    out = []
    for c in slack.paged("conversations.list", key="channels",
                         types=types, exclude_archived="true"):
        kind = ("im" if c.get("is_im") else "mpim" if c.get("is_mpim")
                else "private_channel" if c.get("is_private") else "public_channel")
        if kind == "im":
            그사람 = who.get(c.get("user")) or {}
            if humans_only and (그사람.get("bot") or 그사람.get("gone") or not 그사람):
                continue
            이름 = 그사람.get("name") or c.get("user") or "?"
            if me and c.get("user") == me:
                # ★ 자기 자신과의 DM(저장된 항목). 표시 이름이 본인 이름이라
                #   목록에서는 팀원과 구별이 안 간다 — 실제로 헷갈린다.
                이름 = f"{이름}  ← 나 자신 (저장된 항목)"
        else:
            이름 = c.get("name") or c.get("id")
        out.append({
            "id": c.get("id"), "kind": kind, "name": 이름,
            "members": c.get("num_members") or (2 if kind == "im" else 0),
        })
    order = list(KINDS)
    return sorted(out, key=lambda r: (order.index(r["kind"]), r["name"]))
