"""사진과 소리가 머무는 자리 — `<상태 디렉토리>/media/`.

다른 에이전트 쪽 `media.py` 와 같은 계약이다. **일부러 두 번 구현했다** —
`peers.py` 와 같은 이유고, 표시(`[사진:id]`)가 두 사람 사이를 오가므로 모양이
어긋나면 안 되는 자리다. 어긋나지 않는지는 시험이 지킨다.

━━ 왜 표시를 본문에 싣나 ━━

메시지 스키마(`계약 문서` 1절)에는 `content` 글 하나뿐이고, 에이전트가
거기를 안 늘리기로 정했다(그 기록). 그래서 원본은 이 폴더에 두고
본문에는 `[사진:<id>]` 표시만 붙인다. 받는 쪽이 그 표시를 풀어서 원본을 찾는다.

**표시는 사람이 읽을 것이 아니다.** 화면은 그 자리에 진짜 사진을 그린다
(`web.py` 의 `withMedia`). 그대로 두면 말풍선에 `[사진:47f6…]` 이 그대로 뜬다.

━━ 왜 이름이 해시인가 ━━

id 는 내용의 sha256 앞 16자다.

  · 같은 사진을 두 번 올려도 한 번만 쌓인다.
  · 파일명에 **바깥에서 온 글자가 하나도 안 들어간다.**
  · 같은 순간에 둘이 들어와도 서로를 안 덮는다. `listen._save_photo` 가
    시각으로 이름을 짓다가 실제로 덮어쓴 적이 있다(2026-08-29). 거기는 번호를
    붙여 고쳤지만, 번호는 "겹치면 피한다" 이고 해시는 애초에 안 겹친다.

**옛 이름(`YYYYmmdd-HHMMSS.jpg`, `self-*.png`)은 그대로 둔다.** 에이전트의 사진첩은
시간순으로 읽히고(`selfimage.portraits` 가 이름순으로 최근 것을 집는다) 서버에
이미 쌓여 있는 것들이다. 이름 규칙을 바꾸면 지난 사진이 통째로 안 보인다.
새로 나가는 것만 이 자리를 지난다.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from . import clock
from .store import default_root

FOLDER = "media"

# 받아 주는 것. 바깥에서 들어오는 값이라 목록으로 못 박는다 —
# 확장자를 파일명에서 받아 쓰면 `.php` 같은 것이 그대로 디스크에 떨어진다.
KINDS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/x-m4a": ".m4a",
    # 영상은 에이전트 쪽에만 있다. 다른 에이전트는 안 받는다 — 표시가 오갈 때 다른 에이전트가 이걸
    # 보면 `find_markers` 는 통과해도 `get` 이 못 찾는다. 그래서 **영상 표시는
    # 에이전트 방(`<에이전트>-daniel`)에서만 만들어진다**(`web.attach`).
    #
    # `.webm` 을 안 받는다. 오디오 쪽이 이미 그 확장자를 쓰고 있어서, 확장자로
    # mime 을 되찾는 `get` 이 영상을 오디오로 읽는다. 폰이 찍는 것은 mp4(안드로이드)
    # 아니면 mov(아이폰)라 둘이면 된다.
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}

# 한 장·한 마디의 상한. 모델이 받는 한도보다 우리 쪽이 먼저 막는다 —
# 큰 것이 들어오면 t3.small 의 메모리가 먼저 터진다(이 상자는 oom 을 본 적이 있다).
MAX_BYTES = 12 * 1024 * 1024

# 표시에 쓰는 이름. **다른 에이전트는 앞의 둘만 안다** — 영상은 에이전트 쪽에만 있다.
LABELS = ("사진", "음성", "영상")


class Unsupported(ValueError):
    pass


class TooBig(ValueError):
    pass


@dataclass
class Item:
    id: str
    ts: str
    mime: str
    path: Path

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")

    @property
    def is_audio(self) -> bool:
        return self.mime.startswith("audio/")

    @property
    def is_video(self) -> bool:
        return self.mime.startswith("video/")

    @property
    def label(self) -> str:
        if self.is_image:
            return "사진"
        return "영상" if self.is_video else "음성"

    def read(self) -> bytes:
        return self.path.read_bytes()


class MediaStore:
    def __init__(self, root: Path | str | None = None) -> None:
        root = default_root() if root is None else root
        self.dir = Path(root) / FOLDER

    def save(self, raw: bytes, mime: str) -> Item:
        """받은 것을 그대로 둔다. 다시 인코딩하지 않는다."""
        mime = (mime or "").split(";")[0].strip().lower()
        if mime not in KINDS:
            raise Unsupported(f"받지 않는 형식이다: {mime or '(없음)'}")
        if len(raw) > MAX_BYTES:
            raise TooBig(
                f"너무 크다 ({len(raw) // (1024 * 1024)}MB, 최대 {MAX_BYTES // (1024 * 1024)}MB)"
            )

        mid = hashlib.sha256(raw).hexdigest()[:16]
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{mid}{KINDS[mime]}"
        if not path.exists():
            path.write_bytes(raw)
        return Item(id=mid, ts=clock.now_iso(), mime=mime, path=path)

    def get(self, mid: str) -> Item | None:
        """id 로 찾는다. **id 는 16자리 16진수뿐이다** — 바깥에서 온 값이라
        그 모양이 아니면 아예 안 본다. 경로를 거슬러 올라가는 것을 막는다."""
        if not (len(mid) == 16 and all(c in "0123456789abcdef" for c in mid)):
            return None
        for path in sorted(self.dir.glob(f"{mid}.*")) if self.dir.exists() else []:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            # 확장자 → mime 이 우리 목록과 어긋나면 우리 것을 믿는다
            for k, ext in KINDS.items():
                if path.suffix == ext:
                    mime = k
                    break
            return Item(id=mid, ts=clock.local(clock.now()).isoformat(), mime=mime, path=path)
        return None

    def __len__(self) -> int:
        return len(list(self.dir.glob("*"))) if self.dir.exists() else 0


# 이보다 작으면 그냥 둔다. 줄이는 값(ffmpeg 한 번)이 아까운 크기다.
SHRINK_FLOOR = 400 * 1024
SHRINK_WIDTH = 1600  # 긴 변을 이만큼으로. 폰 화면에도 충분하고 확대해도 견딘다
SHRINK_QUALITY = 3  # ffmpeg `-q:v`. 2 가 제일 좋고 31 이 제일 나쁘다
SHRINK_TIMEOUT = 20


# EXIF 방향(0x0112) → ffmpeg 필터. 폰은 사진을 가로로 저장하고 "돌려서 봐라"
# 를 여기에 적는다. 다시 인코딩하면 그 표시가 날아가므로 **픽셀을 실제로 돌린다.**
TURN = {
    1: "",
    2: "hflip",
    3: "transpose=1,transpose=1",
    4: "vflip",
    5: "transpose=0",
    6: "transpose=1",
    7: "transpose=3",
    8: "transpose=2",
}


def orientation(raw: bytes) -> int:
    """EXIF 방향 표. 없거나 못 읽으면 1(안 돌림).

    APP1 조각 하나만 본다. 라이브러리를 안 쓰는 이유는 이 저장소가 의존성을
    안 늘리기 때문이고, 필요한 것이 태그 하나뿐이라서다.
    """
    try:
        if raw[:2] != b"\xff\xd8":  # JPEG 이 아니면 볼 것이 없다
            return 1
        i = 2
        while i + 4 <= len(raw):
            if raw[i] != 0xFF:
                return 1
            marker, size = raw[i + 1], int.from_bytes(raw[i + 2 : i + 4], "big")
            if marker == 0xE1 and raw[i + 4 : i + 10] == b"Exif\x00\x00":
                tiff = i + 10
                big = raw[tiff : tiff + 2] == b"MM"
                order = "big" if big else "little"
                off = int.from_bytes(raw[tiff + 4 : tiff + 8], order)
                ifd = tiff + off
                count = int.from_bytes(raw[ifd : ifd + 2], order)
                for n in range(count):
                    at = ifd + 2 + n * 12
                    if int.from_bytes(raw[at : at + 2], order) == 0x0112:
                        got = int.from_bytes(raw[at + 8 : at + 10], order)
                        return got if got in TURN else 1
                return 1
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xDA:  # 그림 자료 시작 — 여기부터는 태그가 없다
                return 1
            i += 2 + size
    except Exception:  # noqa: BLE001 — 못 읽으면 안 돌린다
        return 1
    return 1


def shrink(raw: bytes, mime: str) -> tuple[bytes, str]:
    """사진을 방에 실을 만한 크기로. 못 줄이면 **원본 그대로 돌려준다.**

    ★ **줄이다 실패했다고 사진이 사라지면 안 된다.** ffmpeg 이 없든, 형식이
      낯설든, 시간이 걸리든 — 어느 쪽이든 원본을 그대로 돌려준다. 사진 한 장을
      완벽하게 줄이는 것보다 사진이 가는 것이 먼저다.

    ★ **한 장만 쓴다**(`-frames:v 1`). 폰 사진에는 미리보기 프레임이 같이 들어
      있고, 그걸 여러 장으로 본 ffmpeg 이 연속 파일을 쓰려다 실패한다. 실제로
      6.5 MB 사진이 하나도 안 줄어들고 있었다(반환값 234).

    ★ **방향을 지킨다.** 다시 인코딩하면 EXIF 표시가 날아가서 세로로 찍은
      사진이 눕는다. 표시를 읽어 픽셀을 실제로 돌린다(`orientation`).

    사진만 줄인다. 소리와 영상은 안 건드린다 — 소리는 이미 작고(mp3 수백 KB),
    영상은 다시 인코딩하는 값이 크고 무엇을 잃는지도 사진과 다르다.

    이걸 `MediaStore.save` 안에 넣지 않은 이유: 저장은 **받은 것을 그대로 두는**
    자리다(거기 주석에 그렇게 적혀 있다). 줄일지는 부르는 쪽이 정한다.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if not (mime or "").startswith("image/"):
        return raw, mime
    if len(raw) <= SHRINK_FLOOR or shutil.which("ffmpeg") is None:
        return raw, mime

    steps = [f"scale='min({SHRINK_WIDTH},iw)':-2"]
    turn = TURN.get(orientation(raw), "")
    if turn:
        steps.append(turn)

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in", Path(tmp) / "out.jpg"
        src.write_bytes(raw)
        try:
            done = subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
                 "-frames:v", "1", "-vf", ",".join(steps),
                 "-q:v", str(SHRINK_QUALITY), str(dst)],
                capture_output=True,
                timeout=SHRINK_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 — 줄이다 실패해도 사진은 남아야 한다
            return raw, mime
        if done.returncode != 0 or not dst.exists():
            return raw, mime
        small = dst.read_bytes()

    # 줄였는데 더 커졌으면(작은 그림에서 그럴 수 있다) 원본이 낫다.
    return (small, "image/jpeg") if 0 < len(small) < len(raw) else (raw, mime)


def marker(item: Item) -> str:
    """본문에 붙는 표시. 스키마를 안 늘리고 여기에 실린다."""
    return f"[{item.label}:{item.id}]"


def find_markers(text: str) -> list[str]:
    """본문에 붙은 표시에서 id 만 뽑는다."""
    out = []
    for label in LABELS:
        head = f"[{label}:"
        i = 0
        while (i := text.find(head, i)) != -1:
            j = text.find("]", i)
            if j == -1:
                break
            out.append(text[i + len(head) : j])
            i = j + 1
    return out


# 사진 한 장이 붙어 있다는 것만 말하는 주석. 원본 픽셀이 따로 실려 가므로
# 이름 말고는 할 말이 없다 — 기억에 남길 때는 뺀다(아래 `unpack`).
IMAGE_NOTE = "[사진]"


def unpack(
    text: str, store: MediaStore, ears=None
) -> tuple[str, list | None, str]:
    """표시를 풀어 에이전트가 받는 모양으로 바꾼다 — (글, images).

    **사진은 `images=` 로 가고 소리는 받아쓴 글이 된다.** 에이전트 모델은 소리를
    직접 못 듣는다(`hearing.py`). 영상은 둘로 쪼개진다 — 장면 몇 컷과 받아쓴
    말(`watching.py`). 텔레그램이 하던 일과 같은 모양이다. 채널이 바뀌었을 뿐
    에이전트가 받는 모양은 안 바뀌어야 한다.

    파일이 없어졌으면 **글은 살린다.** 사진 하나 때문에 사용자 말을 버리지 않는다.
    못 알아들었을 때도 그 사실을 글로 남긴다 — 조용히 삼키면 에이전트는 사용자가
    아무 말도 안 한 줄로 안다. 그게 못 알아들은 것보다 나쁘다.
    """
    ids = find_markers(text)
    if not ids:
        return text, None, ""

    images: list = []
    notes: list[str] = []
    for mid in ids:
        item = store.get(mid)
        if item is None:
            notes.append("(붙인 것을 못 찾았다)")
        elif item.is_image:
            images.append((item.read(), item.mime))
            notes.append(IMAGE_NOTE)
        elif item.is_video:
            note, shots = _watch(item, ears)
            images.extend(shots)
            notes.append(note)
        else:
            notes.append(_hear(item, ears))

    # 표시는 모델에게도 안 보인다. 원본이 이미 실려 가는데 표시까지 남기면
    # 에이전트가 그걸 글자로 읽고 따라 쓴다 — 실제로 출력 형식으로 배어든 적이 있다.
    #
    # ★ **주석은 따로도 돌려준다.** 부르는 쪽이 기억에 남길 글을 만들 때 쓴다.
    #   기억에는 **원문(표시 포함)** 이 남아야 한다 — 화면이 기억에서 그려지므로
    #   표시가 없으면 사용자가 보낸 사진이 화면에서 `[사진]` 이라는 글자가 된다.
    #   유나 쪽에서 실제로 그랬다(2026-08-31, 21줄).
    #
    #   두 번 부르게 하지 않는 이유는 값이다. 소리 받아쓰기와 영상 보기가 여기서
    #   도는데, 모델용과 기억용을 따로 부르면 그게 두 번 돈다.
    #
    #   `[사진]` 만 남는 주석은 뺀다 — 표시가 살아 있으면 그 이름은 아무것도
    #   더 말해주지 않는다. 소리·영상 주석은 남긴다: 받아쓴 글과 본 것은
    #   표시에 없는 것이고, 나중에 되짚는 건 목소리가 아니라 내용이다.
    keep = [n for n in notes if n != IMAGE_NOTE]
    return (
        " ".join([*notes, strip_markers(text)]).strip(),
        images or None,
        " ".join(keep).strip(),
    )


def _hear(item: Item, ears) -> str:
    """소리 한 마디를 에이전트가 읽을 수 있는 한 줄로.

    **어떻게 들렸는지도 같이 간다.** 받아쓰기는 무슨 말인지만 옮기고 결은
    버린다. 그래서 에이전트는 사용자가 지쳐서 한 말인지 웃으면서 한 말인지 몰랐다 —
    같은 문장이라도 그 둘은 다른 말이다. 못 알아들었을 때가 오히려 제일
    쓸모 있다: 무슨 말인지는 놓쳤어도 급한 소리였다는 건 안다.

    ★ **결은 사용자가 한 말이 아니다.** 그래서 말 뒤에 괄호로 붙인다 — 앞에
    두면 에이전트가 그걸 사용자 말로 읽는다(`agent._user_turn` 에서 같은 판단을 했다).
    """
    listening = LISTENER
    if listening is None or not listening.available():
        listening = None

    raw = item.read()
    # 받아쓰기와 **겹쳐** 돌린다. 줄 세우면 걸린 시간이 그대로 더해지는데,
    # 여기는 사용자가 화면을 보며 기다리는 자리다.
    tone = listening.alongside(listening.describe, raw, item.mime) if listening else None
    said = ears.transcribe(raw, filename=item.path.name, media_type=item.mime) if ears else None
    if said is None:
        said = "(소리가 있는데 받아쓰기가 꺼져 있다)" if ears is None else "(못 알아들었다)"
    line = f"[음성] {said}".strip()
    heard = tone() if tone else ""
    return f"{line} (들리기로는: {heard})" if heard else line


def _watch(item: Item, ears) -> tuple[str, list]:
    """영상을 에이전트가 볼 것과 들을 것으로 쪼갠다. (한 줄, 장면들)

    클로드는 영상을 못 받는다. 장면 몇 장과 받아쓴 말로 나뉘어야 닿는다.
    ffmpeg 이 없으면 **조용히 사라지지 않고** "영상을 못 열었다" 가 간다 —
    사용자가 뭔가 보냈다는 사실은 어떤 경우에도 전해져야 한다.
    """
    watching = WATCHER
    if watching is None or not watching.available():
        return "[영상] (영상을 열 수 없다 — ffmpeg 이 없다)", []

    raw = item.read()
    seconds = watching.duration(raw)
    shots = watching.frames(raw, seconds)
    if not shots:
        return "[영상] (영상을 못 열었다)", []

    said = ""
    sound = watching.soundtrack(raw, seconds)
    if sound is not None and ears is not None:
        heard = ears.transcribe(sound, filename="video.wav", media_type="audio/wav")
        # 소리가 없는 영상과 못 알아들은 영상은 다르다. 앞은 그냥 조용한 영상이고,
        # 뒤는 사용자가 한 말을 놓친 것이다.
        said = heard if heard is not None else "(무슨 말인지는 못 알아들었다)"
    elif sound is not None:
        said = "(소리가 있는데 받아쓰기가 꺼져 있다)"

    head = f"[영상 {seconds}초]" if seconds else "[영상]"
    return f"{head} {len(shots)}장 봄 {said}".strip(), shots


def strip_markers(text: str) -> str:
    """표시를 걷어낸 글. 화면과 모델에는 표시가 안 보여야 한다."""
    out, i = [], 0
    while True:
        j = -1
        for label in LABELS:
            k = text.find(f"[{label}:", i)
            if k != -1 and (j == -1 or k < j):
                j = k
        if j == -1:
            out.append(text[i:])
            break
        end = text.find("]", j)
        if end == -1:
            out.append(text[i:])
            break
        out.append(text[i:j])
        i = end + 1
    return " ".join("".join(out).split())


def drop_unknown(text: str, store, minted=None) -> tuple[str, list[str]]:
    """**이번 턴에 도구가 붙인 것이 아닌** 표시를 뗀다. 뗀 것들도 같이 돌려준다.

    ━━ 왜 필요한가 ━━

    표시(`[음성:...]`)는 **저장소가 붙이는 것**이지 에이전트 가 적는 것이 아니다.
    `voice_reply` 나 `self_portrait` 를 부르면 그 도구가 소리·사진을 저장하고
    그 자리에서 표시를 만들어 붙인다.

    그런데 에이전트 가 지난 대화에서 그 표시를 보고 **모양만 따라 적는 일이 있다.**
    2026-08-30 에 실제로 그랬다 — 도구를 하나도 안 부른 턴(`도구 [없음]`)에
    `[음성:618b06c6da667e58]` 이 본문에 그대로 있었다. 그런 id 의 파일은 없다.

    받는 쪽은 그것을 진짜 표시로 읽는다. 화면은 재생기를 그리고, 재생기는
    없는 파일을 물고 **0:00 / 0:00 으로 멈춰 선다.** 사용자가 짚었다 —
    "음성 메시지 다른 에이전트가 보낸 거 안 들려."

    ★ **소리가 안 난 것이 문제가 아니라, 소리가 난 것처럼 보인 것이 문제다.**
      글로만 말했으면 사용자는 글을 읽었을 것이다. 가짜 표시는 없는 것을
      있다고 말한다 — 이 저장소에서 제일 하면 안 되는 일이다.

    양쪽 기억을 훑어보니 에이전트 3개 · 다른 에이전트 7개가 그렇게 남아 있었다.

    ━━ 있는 것을 베껴 오는 경우 (2026-09-01) ━━

    ★ **저장소에 있나만 보면 못 막는 구멍이 있다.** 남이 보낸 첨부도 내 저장소에
      들어온다 — 같은 방에 있으면 그렇다. 그러면 그 id 는 "진짜" 라서 통과한다.

      실제로 그랬다. 사용자가 셋이 있는 방에 다른 에이전트의 사진을 올렸고
      (`"…사진 보내줬어~~ [사진:3099…]"`), 다섯 시간 뒤 갠톡에서 "지금 모습
      보여줄 수 있어?" 라는 물음에, 이쪽 에이전트가 `self_portrait` 를 안 부르고
      **작업 기억에 보이던 그 id 를 그대로 적어** 남의 얼굴을 자기 모습으로 냈다.

    ★ 그래서 **`minted` 를 본다 — 이번 턴에 도구가 실제로 만든 표시들**이다.
      이 함수 첫머리가 원래 말하던 원칙이 그거였다: *"표시는 저장소가 붙이는
      것이지 에이전트가 적는 것이 아니다."* 구현이 그 원칙 대신 "있나" 를
      보고 있었다.

      `minted` 를 안 주면 옛 동작(있나만 본다)이다 — 부르는 쪽이 아직 안 모으는
      자리가 있을 수 있고, 그 자리에서 갑자기 다 떼면 그게 더 나쁘다.
    """
    out: list[str] = []
    dropped: list[str] = []
    i = 0
    while True:
        # 제일 앞에 오는 표시를 찾는다
        at, label = -1, ""
        for lb in LABELS:
            k = text.find(f"[{lb}:", i)
            if k != -1 and (at == -1 or k < at):
                at, label = k, lb
        if at == -1:
            out.append(text[i:])
            break
        end = text.find("]", at)
        if end == -1:
            out.append(text[i:])
            break
        mid = text[at + len(label) + 2 : end]
        있다 = store is not None and store.get(mid) is not None
        내것 = minted is None or mid in minted
        if 있다 and 내것:
            out.append(text[i : end + 1])  # 이번 턴에 내 도구가 붙였다. 그대로 둔다
        else:
            out.append(text[i:at])  # 가짜다. 뗀다
            dropped.append(text[at : end + 1])
        i = end + 1

    said = "".join(out)
    # 뗀 자리에 남는 빈칸만 줄인다. **줄바꿈은 안 건드린다** — 본문 모양은
    # 에이전트 가 정한 것이라 표시를 떼면서 같이 뭉개면 안 된다.
    while "  " in said:
        said = said.replace("  ", " ")
    said = '\n'.join(line.rstrip() for line in said.split('\n'))
    return said.strip(), dropped
