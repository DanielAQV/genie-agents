#!/usr/bin/env bash
# 골격 → EC2. `/opt/genie-agents` 에 얹는다.
#
#   deploy/push.sh            민다
#   deploy/push.sh --install  밀고 쓰는 쪽 venv 에 설치까지
#
# ━━ 왜 /opt/princess 밑이 아닌가 ━━
#
# 골격은 princess 것이 아니다. princess 가 첫 사용자일 뿐이고, 다음 사용자는
# 다른 저장소다. 한쪽 배포 밑에 두면 그 한쪽을 지울 때 같이 지워진다.
#
# ★ **쓰는 쪽 코드보다 먼저 민다.** 순서가 뒤집히면 새 코드가 옛 골격 위에서
#   뜨고, 그 사이 재시작이 걸리면 import 부터 실패한다.
set -euo pipefail

HOST=${DEPLOY_HOST:-ubuntu@54.89.239.197}
KEY=${DEPLOY_KEY:-$HOME/.ssh/geniein-new-v2.pem}
DEST=/opt/genie-agents
# 이 골격에 기대는 venv 들. 새 사용자가 생기면 여기 는다.
USERS=${GENIE_USERS:-/opt/princess/yuna/venv /opt/princess/yena/venv}

cd "$(dirname "$0")/.."
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "git 저장소 안에서 돌려라" >&2; exit 1; }

ssh_() { ssh -i "$KEY" -o BatchMode=yes -o IdentitiesOnly=yes "$HOST" "$@"; }

echo "== 골격 → $HOST:$DEST  (추적 파일 $(git ls-files | wc -l) 개)"
git ls-files -z | tar czf - --null -T - \
  | ssh_ "sudo mkdir -p $DEST && sudo chown ubuntu:ubuntu $DEST && sudo -u ubuntu tar xzf - -C $DEST"

if [ "${1:-}" = --install ]; then
  for v in $USERS; do
    echo "== $v 에 설치"
    ssh_ "$v/bin/pip install -q -e $DEST && $v/bin/python -c 'import genie_agents; print(\"  OK\", genie_agents.__file__)'"
  done
fi
