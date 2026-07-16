#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

if command -v uv >/dev/null 2>&1; then
    UV=uv
elif [ -x .venv/bin/uv ]; then
    UV=.venv/bin/uv
else
    echo "실행 실패: uv를 설치해주세요. https://docs.astral.sh/uv/" >&2
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "실행 실패: FFmpeg를 설치하고 PATH에 추가해주세요." >&2
    exit 1
fi

if [ -z "${RTZR_CLIENT_ID:-}" ] || [ -z "${RTZR_CLIENT_SECRET:-}" ]; then
    echo "실행 실패: .env 또는 환경변수에 RTZR credential을 설정해주세요." >&2
    exit 1
fi

"$UV" sync --frozen --extra dev
exec "$UV" run nextstop journey-demo "$@"
