"""채널 어댑터 — 밖의 대화가 들어오는 자리.

자리가 **둘**이고, 섞으면 안 된다(`docs/wiring.md` 3절).

    보는 자리    팀원 DM · 단톡방 → `transcript.Book`
                 읽기만 한다. 여기 오는 말은 **나에게 온 것이 아니다**

    대화하는 자리 봇 DM 방 → `messaging.peers.Transport`
                 `send`/`poll` 둘을 채운다. 루프가 도는 곳이 여기다

★ 섞으면 남의 말이 '나에게 온 메시지' 로 루프에 들어간다. 그러면 에이전트가
  거기에 답하고, 팀원 DM 에 봇이 끼어든 꼴이 된다. **한 번이면 끝이다.**

★ 모델 어댑터(`adapters/`)와 같은 생각이다 — 붙는 쪽이 구멍을 채우면 코어는
  안 고친다. Slack 도 Teams 도 같은 자리에 꽂힌다.

여기 있는 것은 늦게 import 한다. `pyproject.toml` 이 모델 SDK 를 안 짊어지는
것과 같은 이유로, 안 쓰는 채널의 의존성을 지고 있지 않는다.
"""

from __future__ import annotations

from .. import env

__all__ = ["catchup"]


def catchup(spec, *, book=None, cursors=None, at=None, prune: bool = True) -> dict:
    """`[watch]` 에 적힌 자리를 한 번씩 긁는다. 자리마다 새로 들어온 개수.

    **말하지 않는다. 쌓기만 한다.** 판단은 깨어날 때다(`wake.CATCHUP`).

    ★ 토큰은 지금 환경변수로 읽는다(`{프리픽스}_SLACK_USER_TOKEN`). `wiring.md`
      §9 가 **아직 안 정한 것**으로 적어 둔 자리다 — 회사 PC 에서 평문 `.env`
      는 안 되고 Windows DPAPI 로 기울어 있다. 정해지면 **이 함수 안 한 줄**만
      바뀌게 두려고 읽는 자리를 여기 하나로 모았다.
    """
    from ..cursors import Cursors
    from ..transcript import Book

    book = Book(spec.state_root) if book is None else book
    cursors = Cursors(spec.state_root) if cursors is None else cursors
    w = spec.watch or {}
    got: dict = {}

    rooms = tuple(w.get("slack") or ())
    if rooms:
        from .slack import Slack, SlackWatch

        token = env.get("SLACK_USER_TOKEN") or ""
        if not token:
            raise RuntimeError(
                f"{env.key('SLACK_USER_TOKEN')} 이 없다. "
                "사용자 토큰으로 읽고 봇 토큰으로 말한다(wiring.md 3절)"
            )
        watch = SlackWatch(
            Slack(token),
            rooms=rooms,
            first_days=float(w.get("first_days", 3)),
            thread_days=float(w.get("thread_days", 3)),
        )
        got.update(watch.catchup(book, cursors, at=at))
        if watch.problems:
            got["_막힌 방"] = watch.problems

    # ★ 긁은 뒤에 버린다. §9 의 "원문은 따라잡기 창만" 이 도는 자리다.
    #   값이 `agent.toml` 에 있어야 **이 사람이 얼마를 남기기로 했는지가 기록**이 된다.
    # 리허설에서는 안 버릴 수 있다. **재는 동안 버리면 잴 것이 없다** —
    # 묶음 길이를 재려고 며칠치를 다시 돌리는 자리가 그렇다.
    지운수 = book.prune(hours=float(w.get("keep_hours", 72)), at=at,
                       thread_days=float(w.get("keep_thread_days", 30))) if prune else 0
    if 지운수:
        got["_버린 원문"] = 지운수
    return got
