"""한 번에 하나만 — 프로세스 중복 실행 방지.

받는 자리가 두 개 돌면 둘 다 인박스를 집어가고 같은 말을 두 번 한다. 깨어남
루프가 두 개 돌면 깨어남도 값도 두 배가 된다. 그리고 둘 다 같은 상태 파일을
서로 덮어쓴다.

━━ PID 파일을 안 쓴다 ━━

**OS 파일 잠금을 쓴다.** 프로세스가 죽으면 OS 가 알아서 놓아주므로, 죽은
프로세스의 PID 가 남아 영영 못 뜨는 일이 없다.

한때 두 에이전트가 각자 다른 방식을 썼다 — 한쪽은 이 파일 잠금, 다른 쪽은 PID
파일. **PID 파일 쪽은 위 문장이 경고한 그것이었고**, 죽은 자물쇠를 치우려고
`os.kill(pid, 0)` 과 `tasklist` 를 부르는 코드까지 딸려 있었다. 그 사이에 PID 가
재사용되면 살아 있는 남의 프로세스를 자기라고 볼 수도 있다. 합치면서 잠금 쪽으로
맞췄다.

부르는 모양이 둘인 것은 그대로 둔다 — 둘 다 쓰이고 있고, 하는 일은 같다.

    acquire("loop", root)          잡고 프로세스 수명 동안 들고 있는다
    with Lock("loop", root): ...   블록을 벗어나면 놓는다
"""

from __future__ import annotations

import os
from pathlib import Path

from .store import default_root

# 잡은 잠금은 여기 남는다. 파일 객체가 닫히면 잠금도 풀리므로, 반환값을 버려도
# 되게 하려면 누군가 들고 있어야 한다.
_held: list = []


class AlreadyRunning(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"'{name}' 가 이미 돌고 있다. 두 개가 동시에 돌면 같은 일을 두 번 한다.\n"
            f"돌던 것을 먼저 끄고 다시 시작해라."
        )
        self.name = name


def _lock_file(handle) -> None:
    """이 프로세스가 아니면 못 잡게. 못 잡으면 OSError."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire(name: str, root: Path | str | None = None):
    """잠금을 잡는다. 이미 잡혀 있으면 `AlreadyRunning`.

    잡은 잠금은 모듈이 들고 있으므로 반환값을 버려도 된다.
    """
    root = Path(default_root() if root is None else root)
    root.mkdir(parents=True, exist_ok=True)
    handle = open(root / f"{name}.lock", "a+")  # noqa: SIM115 - 프로세스 수명 동안 연다

    try:
        _lock_file(handle)
    except OSError:
        handle.close()
        raise AlreadyRunning(name) from None

    _held.append(handle)
    return handle


def release_all() -> None:
    """시험용. 프로세스가 죽으면 OS 가 알아서 풀어주므로 평소엔 쓸 일이 없다."""
    while _held:
        _held.pop().close()


class Lock:
    """`with` 로 쓰는 같은 잠금."""

    def __init__(self, name: str, root: Path | str | None = None) -> None:
        self.name = name
        self.root = Path(default_root() if root is None else root)
        self.path = self.root / f"{name}.lock"
        self._handle = None

    def acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")  # noqa: SIM115
        try:
            _lock_file(handle)
        except OSError:
            handle.close()
            raise AlreadyRunning(self.name) from None
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "Lock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False
