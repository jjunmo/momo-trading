#!/usr/bin/env bash
#
# momo-trading 실행 스크립트
#
# 사용법:
#   ./start.sh          — 포그라운드 실행
#   ./start.sh -d       — 백그라운드(데몬) 실행
#   ./start.sh stop     — 백그라운드 프로세스 종료
#   ./start.sh status   — 실행 상태 확인
#   ./start.sh logs     — 실시간 로그 보기
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
PID_FILE="$APP_DIR/.momo.pid"
LOG_FILE="$APP_DIR/logs/momo-trading.log"
HOST="${MOMO_HOST:-0.0.0.0}"
PORT="${MOMO_PORT:-9000}"

# venv 활성화
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "❌ venv 없음: $VENV_DIR"
    echo "   python -m venv venv && pip install -r requirements.txt"
    exit 1
fi

cd "$APP_DIR"

# .env 체크
if [ ! -f ".env" ]; then
    echo "⚠️  .env 파일이 없습니다. .env.example을 참고하세요."
fi

# 로그 디렉토리 확인
mkdir -p "$(dirname "$LOG_FILE")"

case "${1:-}" in
    stop)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "🛑 momo-trading 종료 (PID: $PID)"
                kill "$PID"
                rm -f "$PID_FILE"
            else
                echo "프로세스가 이미 종료됨 (stale PID: $PID)"
                rm -f "$PID_FILE"
            fi
        else
            echo "실행 중인 프로세스 없음"
        fi
        ;;

    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "✅ momo-trading 실행 중 (PID: $PID)"
                echo "   http://localhost:$PORT/admin"
            else
                echo "❌ 프로세스 종료됨 (stale PID: $PID)"
                rm -f "$PID_FILE"
            fi
        else
            echo "❌ 실행 중인 프로세스 없음"
        fi
        ;;

    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "로그 파일 없음: $LOG_FILE"
        fi
        ;;

    -d|--daemon)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "이미 실행 중 (PID: $(cat "$PID_FILE"))"
            exit 1
        fi

        echo "🚀 momo-trading 백그라운드 시작"
        echo "   Host: $HOST:$PORT"
        echo "   Admin: http://localhost:$PORT/admin"
        echo "   Log: $LOG_FILE"

        nohup python -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info \
            >> "$LOG_FILE" 2>&1 &

        echo $! > "$PID_FILE"
        echo "   PID: $(cat "$PID_FILE")"
        echo ""
        echo "종료: ./start.sh stop"
        ;;

    ""|--foreground)
        echo "🚀 momo-trading 시작 (포그라운드)"
        echo "   Host: $HOST:$PORT"
        echo "   Admin: http://localhost:$PORT/admin"
        echo "   종료: Ctrl+C"
        echo ""

        python -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info \
            --reload
        ;;

    *)
        echo "사용법: $0 [-d|stop|status|logs]"
        exit 1
        ;;
esac
