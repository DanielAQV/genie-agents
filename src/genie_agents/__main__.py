"""명령줄 — 폴더 하나를 만들고, 보고, 돌린다.

    python -m genie_agents new  <폴더> [--adapter gemini]   틀을 만든다
    python -m genie_agents check <폴더>                     띄우기 전에 본다
    python -m genie_agents talk  <폴더> "..."               한 마디 걸어 본다
    python -m genie_agents wake  <폴더> [--kind 따라잡기]   깨어난다 (밖에서 부른다)
    python -m genie_agents rooms <폴더>                     방 id 를 찾는다 (한 번 쓰고 만다)
    python -m genie_agents rehearse <폴더> --from … --to …  며칠 전인 척 다시 돌린다
    python -m genie_agents extract  <폴더> [--dry-run]      쌓인 말에서 고리를 캔다

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
        got = rooms(slack, types=args.types or ",".join(KINDS),
                    humans_only=not args.all, me=me)
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


def cmd_rehearse(args) -> int:
    """며칠 전인 척하고 하루씩 다시 돌린다.

    ★ **연휴 때문에 만든 것이 아니다.** 4단계 추출은 프롬프트를 고치고 다시
      돌려 보는 일의 반복인데, 그때마다 하루를 기다릴 수는 없다. 같은 며칠을
      몇 번이고 다시 돌릴 수 있어야 그 일이 성립한다.

    ★ **진짜 자리를 안 건드린다.** 상태는 `--root`(기본 `<폴더>/.rehearsal`)로
      간다. 리허설이 커서를 밀어 버리면 진짜 원장이 그 창을 영영 못 본다.

    ★ 리허설 자리에도 **팀원의 말이 그대로 쌓인다.** 다 쓰면 지워라 —
      보존 결정은 `.followup/` 에만 걸려 있고 여기는 그 밖이다.
    """
    from datetime import datetime, timedelta, timezone

    from .spec import BadSpec, load

    _settings(args.folder)
    try:
        spec = load(args.folder)
    except BadSpec as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    from . import clock, env
    from .channels import catchup
    from .cursors import Cursors
    from .transcript import Book

    env.use(spec.prefix)
    clock.set_default(spec.prefix, spec.timezone, spec.utc_offset)

    def 날(s: str) -> datetime:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    try:
        시작, 끝 = 날(args.since), 날(args.until)
    except ValueError as e:
        print(f"✗ 날짜를 못 읽었다: {e} (2026-08-20 또는 2026-08-20T18:00)", file=sys.stderr)
        return 1
    if 시작 > 끝:
        print("✗ --from 이 --to 보다 뒤다", file=sys.stderr)
        return 1

    root = Path(args.root) if args.root else spec.state_root.with_name(".rehearsal")
    if args.fresh:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    book, cursors = Book(root), Cursors(root)

    print(f"  {root}  ·  {시작:%Y-%m-%d %H:%M} → {끝:%Y-%m-%d %H:%M}"
          f"  ({args.step}시간씩" + (", 안 버림)" if args.no_prune else ")"))

    진짜시계 = clock.now
    걸음, 그때 = 0, 시작
    try:
        while 그때 <= 끝:
            # 시계까지 민다. `at` 을 전부 꿰어 두긴 했지만, 한 군데라도
            # 진짜 지금을 읽으면 리허설이 조용히 틀린다.
            clock.set_clock(lambda 순간=그때: 순간)
            got = catchup(spec, book=book, cursors=cursors, at=그때,
                          prune=not args.no_prune)
            방 = {k: v for k, v in got.items() if not k.startswith("_")}
            if any(방.values()) or args.verbose:
                print(f"  {그때:%m-%d %H:%M}  "
                      + " · ".join(f"{k} {v}" for k, v in 방.items()))
            for k, v in got.items():
                if k.startswith("_"):
                    print(f"           {k[1:]} — {v}")
            걸음 += 1
            그때 += timedelta(hours=args.step)
    finally:
        clock.set_clock(진짜시계)

    print()
    print(f"  {걸음}번 깨어난 셈 · 원장 {len(book)}줄")
    for room in book.rooms():
        ls = book.lines(room)
        mine = sum(1 for x in ls if x.mine)
        th = book.threads(room)
        답글 = sum(1 for x in ls if x.thread)
        print(f"    {room:<13} {len(ls):>4}줄 · 내 말 {mine:>3} · 스레드 {len(th):>2}"
              f" · 스레드 안 {답글:>3}")

    print()
    print("  묶음 — 4단계가 모델에 실을 단위다")
    잰것 = []
    for room in book.rooms():
        for t in book.threads(room):
            잰것.append(("스레드", room, book.bundle(room, thread=t)))
        잰것.append(("창", room, book.bundle(room)))
    잰것 = [(k, r, b) for k, r, b in 잰것 if len(b)]
    if not 잰것:
        print("    (없다)")
        return 0
    글자 = sorted(sum(len(x.text) for x in b.lines) for _, _, b in 잰것)
    줄 = sorted(len(b) for _, _, b in 잰것)

    def 백분위(xs, p):
        return xs[min(len(xs) - 1, int(len(xs) * p))]

    print(f"    {len(잰것)}묶음 · 줄 중앙 {백분위(줄, .5)} · p95 {백분위(줄, .95)} · 최대 {줄[-1]}")
    print(f"             글자 중앙 {백분위(글자, .5):,} · p95 {백분위(글자, .95):,} · 최대 {글자[-1]:,}")
    # 2026-08-31 실측(Qwen3-4B-Instruct-2507, 이 워크스페이스 10일치):
    # 토큰/글자 = 0.40. 처음엔 0.6~0.9로 어림했는데 그보다 훨씬 낮았다 —
    # 베트남어가 라틴 문자라 잘 쪼개진다. 다른 팀에서는 다시 재야 한다.
    print(f"    ≈ 토큰 중앙 {int(백분위(글자, .5) * 0.4):,} · p95 {int(백분위(글자, .95) * 0.4):,}"
          f"  (글자×0.40, 이 워크스페이스 실측)")
    print(f"      다른 언어·다른 팀이면 비율이 다르다. 실제 토크나이저로 다시 재라 —")
    print(f"      이 수가 로컬 모델을 어디까지 태울 수 있는지를 정한다.")
    return 0


def cmd_extract(args) -> int:
    """겹 1~3 을 한 번 돌린다(`docs/wiring.md` 5절).

    ★ **`--dry-run` 이 기본으로 쓰이는 자리다.** 무엇이 모델에 실릴지를 먼저
      눈으로 보라 — 팀원의 글이 밖으로 나가는 첫 자리가 여기다.
    """
    from .spec import BadSpec, load

    _settings(args.folder)
    try:
        spec = load(args.folder)
    except BadSpec as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    if not spec.extract:
        print("✗ [prompt] extract 가 없다 — 무엇을 고리로 볼지는 이 비서가 정한다",
              file=sys.stderr)
        return 1

    from . import clock, env, extract as ex
    from .cursors import Cursors
    from .loops import LoopBook
    from .transcript import Book

    env.use(spec.prefix)
    clock.set_default(spec.prefix, spec.timezone, spec.utc_offset)

    root = Path(args.root) if args.root else spec.state_root
    book, loops = Book(root), LoopBook(root)
    me_id = args.me or env.get("SLACK_ME") or ""

    묶음 = ex.plan(book, me_id=me_id, rooms=tuple(spec.watch.get("slack") or ()))
    if not 묶음:
        print("  태울 것이 없다 — 겹 1 이 후보를 하나도 안 골랐다")
        return 0

    names = {}
    if args.names and Path(args.names).exists():
        import json as _json

        names = _json.loads(Path(args.names).read_text(encoding="utf-8"))

    # ★ **시간 순으로 돈다.** 앞의 대화가 연 고리를 뒤의 대화가 닫는다 —
    #   순서가 뒤집히면 닫는 말을 먼저 보고, 그러면 무엇을 닫는지 모른다.
    묶음.sort(key=lambda b: b.span[0])
    if args.limit:
        # 지침을 고치고 다시 돌려 보는 일의 반복이라, 스무 묶음을 다 돌리면
        # 한 번에 30분이다. 앞 몇 개로 먼저 본다.
        묶음 = 묶음[: args.limit]
    print(f"  {root} · 묶음 {len(묶음)}개 · 열린 고리 {len(loops.live())}개")

    if args.dry_run:
        실린것 = [(b, ex.worth_asking(b, me_id),
                 ex.serialize(b, loops=loops.live(), names=names, me_id=me_id))
                for b in 묶음]
        for b, why, body in 실린것:
            머리 = f"{b.room}" + (f" · 스레드 {b.thread}" if b.thread else " · 최근")
            print(f"{chr(10)}── {머리}  ({len(b)}줄 · {len(body):,}자 · {why})")
            if args.full:
                print(body)
        전체 = sum(len(x[2]) for x in 실린것)
        print(f"{chr(10)}  합 {전체:,}자 ≈ {int(전체 * 0.4):,}토큰"
              f" (묶음당 평균 {int(전체 / len(실린것) * 0.4):,})")
        print("  ★ --dry-run 이라 모델을 안 불렀다. 아무것도 밖으로 안 나갔다.")
        return 0

    from .runner import _client_for, _default_model

    client = _client_for(spec)
    model = spec.model or _default_model(spec)
    별칭 = ex.me_names(names, me_id)
    셈 = {"움직임": 0, "열림": 0, "못 씀": 0, "겹침": 0}
    못푼것 = []
    for b in 묶음:
        # ★ **부르기 직전에 싣는다.** 미리 전부 직렬화해 두면 열린 고리 목록이
        #   *시작할 때의 것*으로 굳어서, 앞 묶음이 연 고리를 뒤 묶음이 못 본다.
        #   그러면 `moves` 가 가리킬 것이 영영 없고 — 실제로 첫 판에서
        #   움직임이 0이었다 — 목록은 자라기만 한다.
        body = ex.serialize(b, loops=loops.live(), names=names, me_id=me_id)
        text = ex.ask(client, model, spec.extract, body)
        got = ex.parse(text)
        for k, v in ex.apply(got, loops, book, bundle=b,
                             names=names, mine=별칭).items():
            셈[k] += v
        못푼것 += got.unresolved
        머리 = f"{b.room}" + (f"·{b.thread}" if b.thread else "")
        print(f"  {머리:<28} 움직임 {len(got.moves)} · 열림 {len(got.opens)}"
              f" · 못 찾음 {len(got.unresolved)}"
              + (f" · 버림 {len(got.dropped)}" if got.dropped else ""))
        for d in got.dropped:
            print(f"      · {d}")

    print(f"{chr(10)}  원장 {len(loops)}줄 · 살아 있는 것 {len(loops.live())}개 · {셈}")
    if 못푼것:
        # ★ 못 찾은 것을 조용히 버리면 사람이 고칠 기회 자체가 사라진다.
        print(f"{chr(10)}  못 찾은 말 {len(못푼것)}개 — 저녁 목록에 이대로 올라간다")
        for u in 못푼것[:10]:
            print(f"    · \"{u.said}\" — {u.why}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="genie_agents", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="에이전트 틀을 만든다")
    n.add_argument("folder")
    n.add_argument("--adapter", default="anthropic", choices=["anthropic", "gemini", "local"])
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

    # ★ 여기서 `channels.slack` 을 import 하면 **모든 명령이** 그걸 진다 —
    #   도움말 문자열 하나 때문에. 어댑터는 늦게 부른다(pyproject 첫머리와 같은 규칙).
    r = sub.add_parser("rooms", help="[watch] 에 넣을 방 id 를 찾는다")
    r.add_argument("folder")
    r.add_argument("--types", default="",
                   help="쉼표로. im, mpim, private_channel, public_channel")
    r.add_argument("--all", action="store_true",
                   help="앱 DM·퇴사자 DM 까지 전부 (기본은 사람 DM 만)")
    r.set_defaults(fn=cmd_rooms)

    h = sub.add_parser("rehearse", help="며칠 전인 척 다시 돌린다 (진짜 자리는 안 건드린다)")
    h.add_argument("folder")
    h.add_argument("--from", dest="since", required=True, help="2026-08-20")
    h.add_argument("--to", dest="until", required=True, help="2026-08-28")
    h.add_argument("--step", type=float, default=24, help="몇 시간씩 (기본 24)")
    h.add_argument("--root", default="", help="상태 자리 (기본 <폴더>/.rehearsal)")
    h.add_argument("--fresh", action="store_true", help="그 자리를 먼저 비운다")
    h.add_argument("--no-prune", action="store_true",
                   help="안 버린다. 재는 동안 버리면 잴 것이 없다")
    h.add_argument("--verbose", action="store_true", help="0줄인 걸음도 찍는다")
    h.set_defaults(fn=cmd_rehearse)

    x = sub.add_parser("extract", help="쌓인 말에서 고리를 캔다 (겹 1~3)")
    x.add_argument("folder")
    x.add_argument("--root", default="", help="어느 원장에 (기본 <폴더>/.<id>)")
    x.add_argument("--me", default="", help="본인 slack id. 멘션을 알아보려면 필요하다")
    x.add_argument("--names", default="", help="{id: 이름} JSON 파일")
    x.add_argument("--limit", type=int, default=0,
                   help="앞 N 묶음만. 지침을 고쳐 가며 볼 때")
    x.add_argument("--dry-run", action="store_true",
                   help="모델을 안 부르고 무엇이 실릴지만 본다")
    x.add_argument("--full", action="store_true", help="--dry-run 에서 본문까지 찍는다")
    x.set_defaults(fn=cmd_extract)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
