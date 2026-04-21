#!/usr/bin/env bash
#
# dev/multi-agent + Codex CLI 전용 실행 래퍼
#
# 사용법:
#   ./start-multi-agent.sh          — 포그라운드 실행
#   ./start-multi-agent.sh -d       — 백그라운드 실행
#   ./start-multi-agent.sh stop     — 백그라운드 프로세스 종료
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

export MOMO_ENV_FILE="${MOMO_ENV_FILE:-$APP_DIR/.env.multi-agent}"
export MOMO_PORT="${MOMO_PORT:-9200}"
export MOMO_PID_FILE="${MOMO_PID_FILE:-$APP_DIR/.momo-multi-agent.pid}"
export MOMO_LOG_FILE="${MOMO_LOG_FILE:-$APP_DIR/logs/momo-trading-multi-agent.log}"
export MOMO_AUTO_MIGRATE="${MOMO_AUTO_MIGRATE:-true}"

if [ ! -f "$MOMO_ENV_FILE" ]; then
    echo "전용 env 파일이 없습니다: $MOMO_ENV_FILE"
    echo ".env를 참고해 .env.multi-agent를 먼저 구성하세요."
    exit 1
fi

cd "$APP_DIR"

case "${1:-}" in
    ""|--foreground|-d|--daemon)
        mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
        if [ "$MOMO_AUTO_MIGRATE" = "true" ]; then
            echo "전용 DB 마이그레이션 적용: $MOMO_ENV_FILE"
            "$APP_DIR/venv/bin/python" -m alembic upgrade head
        fi
        ;;
esac

exec "$APP_DIR/start.sh" "$@"
