"""명령줄 — 폴더 하나를 만들고, 보고, 돌린다.

    python -m genie_agents new  <폴더> [--adapter gemini]   틀을 만든다
    python -m genie_agents check <폴더>                     띄우기 전에 본다
    python -m genie_agents talk  <폴더> "..."               한 마디 걸어 본다

`check` 는 **키가 없어도 돈다.** 정의가 성한지와 키가 있는지는 다른 문제고,
둘을 같이 물으면 키 없는 자리에서 정의를 못 고친다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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

    from dataclasses import fields

    from .policy import Policy

    base = Policy()
    diff = [f.name for f in fields(spec.policy)
            if getattr(spec.policy, f.name) != getattr(base, f.name)]
    print(f"  정책        기본값에서 갈리는 것 {len(diff)}개"
          + (f" — {', '.join(diff)}" if diff else " (남과 똑같이 돈다)"))

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

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
