"""명령줄 — 폴더 하나를 만들고, 보고, 돌린다.

    python -m genie_agents new  <폴더> [--adapter gemini]   틀을 만든다
    python -m genie_agents check <폴더>                     띄우기 전에 본다
    python -m genie_agents talk  <폴더> "..."               한 마디 걸어 본다
    python -m genie_agents wake  <폴더> [--kind 따라잡기]   깨어난다 (밖에서 부른다)
    python -m genie_agents rooms <폴더>                     방 id 를 찾는다 (한 번 쓰고 만다)

★ 설정은 **그 폴더의 `.env`** 에서 읽는다(`<폴더>/.env`). 현재 디렉토리가 아니다 —
  한 호스트에 에이전트 여럿이 살고, cwd 로 읽으면 **어디서 불렀느냐에 따라 남의
  토큰을 읽는다.** 상태 디렉토리를 안 섞는 것과 같은 규칙이다(`store.default_root`).
  셸에서 준 값이 파일보다 우선이다(`config.load_env` 가 `setdefault`).

`check` 는 **키가 없어도 돈다.** 정의가 성한지와 키가 있는지는 다른 문제고,
둘을 같이 물으면 키 없는 자리에서 정의를 못 고친다.

★ `wake` 는 **한 번 돌고 끝난다.** 상시 프로세스가 아니다 — 깨우는 것은
  밖(작업 스케줄러·cron·systemd timer)이고, 그것은 놓친다. 놓친 것을 따라잡는
  자리가 `wake.pending` 이라 단발로도 성립한다(`docs/wiring.md` 2절).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ★ stderr 도 같이 돌린다. 잘못됐다는 말이 깨져서 나오면 그게 제일 나쁘다 —
#   윈도우 콘솔 기본 코드페이지에서 한글 오류문이 통째로 뭉개진다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _settings(folder: str) -> None:
    """그 폴더의 `.env` 를 환경에 올린다. 없으면 조용히 넘어간다.

    ★ **`load` 보다 먼저 부른다.** `check` 가 "키가 없다" 를 말하는 자리가
      `load` 뒤라서, 나중에 부르면 있는 키를 없다고 말한다.
    """
    from .config import load_env

    load_env(Path(folder) / ".env")


def cmd_new(args) -> int:
    from .spec import FILE, TEMPLATE

    root = Path(args.folder)
    if root.exists() and any(root.iterdir()):
        print(f"이미 뭔가 있다: {root}", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)
    name = root.name.replace(" ", "-")

    (root / FILE).write_text(
        TEMPLATE.format(id=name, adapter=args.adapter, prefix=name.upper()),
        encoding="utf-8",
    )
    (root / "prompt.md").write_text(
        "# 지침\n\n"
        "구조로 강제할 수 없는 것만 적는다. 구조로 막을 수 있는 것을 여기 적으면\n"
        "두 군데를 관리하게 되고, 둘이 어긋나면 지침 쪽이 진다.\n\n"
        "**말투는 여기 적지 마라.** 그건 정체성이다.\n",
        encoding="utf-8",
    )
    (root / "identity.md").write_text(
        "# 이 존재는 누구인가\n\n"
        "여기 적힌 것이 정본이다. 지침과 부딪히면 이쪽이 이긴다.\n",
        encoding="utf-8",
    )
    # `tools.py` 는 안 만든다. 틀은 골격 도구를 켜는 쪽으로 나가고,
    # 안 쓰는 빈 파일이 있으면 그게 도는 자리인 줄 알고 거기를 고친다.
    print(f"만들었다: {root}")
    for f in sorted(p.name for p in root.iterdir()):
        print(f"  {f}")
    print(f"\n다음: python -m genie_agents check {root}")
    return 0


def cmd_check(args) -> int:
    from .runner import check
    from .spec import BadSpec, load

    _settings(args.folder)

    try:
        spec = load(args.folder)
    except BadSpec as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    print(f"  {spec.id}  ({spec.adapter} · 프리픽스 {spec.prefix} · {spec.timezone})")
    print(f"  상태 자리   {spec.state_root}")
    print(f"  지침        {len(spec.instructions):,}자"
          + (f" · 정체성 {len(spec.identity):,}자" if spec.identity else ""))
    if spec.enabled:
        print(f"  도구        {len(spec.enabled)}개 — {', '.join(spec.enabled)}")
        if spec.describe:
            print(f"              설명을 갈아 끼운 것: {', '.join(sorted(spec.describe))}")
    elif spec.tools_module:
        print(f"  도구        {spec.tools_module} (직접 들고 온 것)")
    else:
        print("  도구        없다 — 말만 한다")
    if spec.cast:
        print(f"  배역        방 {len(spec.cast['rooms'])}개")
    if spec.watch:
        w = spec.watch
        print(f"  보는 자리   slack {len(w.get('slack') or ())}곳"
              f" · 원문 {w.get('keep_hours', 72)}시간"
              f" · 첫날 {w.get('first_days', 3)}일치")

    from dataclasses import fields

    from .policy import Policy

    base = Policy()
    diff = [f.name for f in fields(spec.policy)
            if getattr(spec.policy, f.name) != getattr(base, f.name)]
    print(f"  정책        기본값에서 갈리는 것 {len(diff)}개"
          + (f" — {', '.join(diff)}" if diff else " (남과 똑같이 돈다)"))

    from .wake import DEFAULT as 기본상한

    n = spec.nudge
    ndiff = [f.name for f in fields(n)
             if getattr(n, f.name) != getattr(기본상한, f.name)]
    print(f"  상한        아침 {n.morning} · 저녁 {n.evening} · "
          f"조용 {n.quiet[0]}~{n.quiet[1]} · 하루 {n.max_per_day}번"
          + (f"  (갈리는 것 {len(ndiff)}개)" if ndiff else "  (기본값 그대로)"))

    problems = check(args.folder)
    if not problems:
        print("\n✓ 성하다")
        return 0
    print()
    for p in problems:
        print(("  " if p.startswith("(참고)") else "✗ ") + p)
    return 0 if all(p.startswith("(참고)") for p in problems) else 1


def cmd_talk(args) -> int:
    from .runner import Agent
    from .spec import BadSpec, load

    _settings(args.folder)

    try:
        agent = Agent(load(args.folder))
    except BadSpec as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    turn = agent.run(args.text, scope=args.scope)
    print(turn.text or "(말이 없다)")
    print(
        f"  ({turn.model} · 요청 {turn.requests}회 · "
        f"입력 {turn.input_tokens:,}(캐시 {turn.cached_tokens:,}) "
        f"출력 {turn.output_tokens:,} · {turn.seconds:.1f}초"
        + (f" · 도구 {', '.join(c['name'] for c in turn.tool_calls)}" if turn.tool_calls else "")
        + ")",
        file=sys.stderr,
    )
    return 0


def cmd_wake(args) -> int:
    """깨어남 한 번. **밖에서 부른다.**

    무엇을 말할지는 여기서 안 정한다 — 그건 인격이다. 여기가 하는 일은
    *무엇이 밀렸나*를 묻고, 상한을 지나가게 하고, 낸 것을 적는 것뿐이다.
    """
    from .singleton import AlreadyRunning, acquire
    from .spec import BadSpec, load
    from .wake import CATCHUP, Wake

    _settings(args.folder)

    try:
        spec = load(args.folder)
    except BadSpec as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    from . import clock, env

    env.use(spec.prefix)
    clock.set_default(spec.prefix, spec.timezone, spec.utc_offset)

    try:
        # 로그온 트리거와 매시 트리거가 겹치는 일이 실제로 있다.
        acquire(f"{spec.id}-wake", spec.state_root)
    except AlreadyRunning as e:
        print(f"  {e}", file=sys.stderr)
        return 0  # 겹친 것은 잘못이 아니다. 한쪽이 돌고 있으면 그걸로 됐다

    wake = Wake(spec.nudge, spec.state_root)

    # ★ **어느 깨어남이든 먼저 긁는다.** 트리거가 셋이어도 작업은 하나다
    #   (`wiring.md` 2절). 아침에만 긁고 따라잡기에서 안 긁으면 매시 트리거가
    #   하는 일이 없어지고, 아침이 그날 첫 실행이 되어 창이 통째로 벌어진다.
    긁힘 = ""
    if spec.watch:
        from .channels import catchup

        try:
            got = catchup(spec)
            방 = {k: v for k, v in got.items() if not k.startswith("_")}
            # ★ **0인 방도 찍는다.** 안 찍으면 스코프 때문에 영영 0인 방과
            #   그냥 조용한 방이 구별이 안 된다 — 앞엣것은 몇 주 뒤에나 들킨다.
            print("  긁었다 — " + (" · ".join(f"{k} {v}" for k, v in 방.items())
                                 or "보는 자리가 없다"))
            for k, v in got.items():
                if k.startswith("_"):
                    print(f"  {k[1:]} — {v}")
            긁힘 = " · ".join(f"{k} {v}" for k, v in 방.items() if v)
        except Exception as e:
            # 못 읽었다고 밀린 것까지 날리지 않는다. **다만 조용히 넘어가지도
            # 않는다** — 안 읽히는 방이 있으면 원장이 틀린 채로 자란다.
            print(f"✗ 못 긁었다: {e}", file=sys.stderr)
            if args.kind == CATCHUP:
                return 1

    if args.kind == CATCHUP:
        # 쌓기만 한다. 판단은 깨어날 때. **여기서 모델을 안 부른다.**
        wake.said(CATCHUP, 긁힘 or "따라잡기", at=None)
        print(f"  {CATCHUP} — 쌓기만 했다")
        return 0

    kinds, why = wake.batch()
    if not kinds:
        print(f"  낼 것이 없다{f' — {why}' if why else ''}")
        return 0

    # ★ 여기가 인격이 들어올 자리다. 아직 소스도 말하는 자리도 안 붙었으므로
    #   무엇이 밀렸는지만 찍고 적는다 — 4단계까지는 사람에게 한 마디도 안 한다.
    print(f"  낼 것: {' + '.join(kinds)}")
    if args.dry_run:
        print("  (--dry-run 이라 안 적었다)")
        return 0
    wake.said(kinds, args.note)
    return 0


def cmd_rooms(args) -> int:
    """`[watch] slack` 에 넣을 방 id 를 찾는다.

    ★ **도는 물건이 아니다.** 사람이 Slack UI 에서 DM 채널 id 를 캐내는 손일
      하나를 없애려고 두는 것이다. 루프는 이걸 안 부른다.
    """
    from .spec import BadSpec, load

    _settings(args.folder)
    try:
        spec = load(args.folder)
    except BadSpec as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    from . import env

    env.use(spec.prefix)
    token = env.get("SLACK_USER_TOKEN")
    if not token:
        print(f"✗ {env.key('SLACK_USER_TOKEN')} 이 없다 — {spec.root / '.env'}",
              file=sys.stderr)
        return 1

    from .channels.slack import KINDS, Slack, SlackError, rooms

    slack = Slack(token)
    try:
        me = str(slack.call("auth.test").get("user_id") or "")
        got = rooms(slack, types=args.types, humans_only=not args.all, me=me)
    except SlackError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    if not got:
        print("  방이 없다. 스코프를 보라 — channels:read groups:read im:read mpim:read")
        return 0

    보는중 = set(spec.watch.get("slack") or ())
    for r in got:
        표 = "◆" if r["id"] in 보는중 else " "
        print(f"  {표} {r['id']:<12} {KINDS[r['kind']]:<6} {r['name']}"
              + (f"  ({r['members']}명)" if r["members"] > 2 else ""))
    print()
    print(f"  ◆ 는 이미 [watch] slack 에 있는 것. "
          f"{len(got)}곳 중 {len(보는중)}곳을 본다.")
    if not args.all:
        print("    (앱 DM 과 퇴사자 DM 은 뺐다. 전부 보려면 --all)")
    print("  ★ 읽는 범위를 좁힌 것이 결정이다 — 늘릴수록 진짜 판단이 목록에 묻힌다.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="genie_agents", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="에이전트 틀을 만든다")
    n.add_argument("folder")
    n.add_argument("--adapter", default="anthropic", choices=["anthropic", "gemini"])
    n.set_defaults(fn=cmd_new)

    c = sub.add_parser("check", help="띄우기 전에 본다 (키 없어도 돈다)")
    c.add_argument("folder")
    c.set_defaults(fn=cmd_check)

    t = sub.add_parser("talk", help="한 마디 걸어 본다")
    t.add_argument("folder")
    t.add_argument("text")
    t.add_argument("--scope", default="")
    t.set_defaults(fn=cmd_talk)

    from .wake import KINDS

    w = sub.add_parser("wake", help="깨어남 한 번 (밖에서 부른다)")
    w.add_argument("folder")
    w.add_argument("--kind", default="", choices=["", *KINDS],
                   help="비우면 밀린 것을 낸다. 따라잡기는 쌓기만 한다")
    w.add_argument("--note", default="", help="자국에 남길 한 줄")
    w.add_argument("--dry-run", action="store_true",
                   help="무엇이 밀렸는지만 보고 안 적는다")
    w.set_defaults(fn=cmd_wake)

    from .channels.slack import KINDS

    r = sub.add_parser("rooms", help="[watch] 에 넣을 방 id 를 찾는다")
    r.add_argument("folder")
    r.add_argument("--types", default=",".join(KINDS),
                   help=f"쉼표로. 아는 것: {', '.join(KINDS)}")
    r.add_argument("--all", action="store_true",
                   help="앱 DM·퇴사자 DM 까지 전부 (기본은 사람 DM 만)")
    r.set_defaults(fn=cmd_rooms)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
