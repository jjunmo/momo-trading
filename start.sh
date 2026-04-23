#!/usr/bin/env bash
#
# momo-trading 통합 실행 스크립트
#
# 모드는 다음 우선순위로 결정된다:
#   (1) 명시 인자  claude | codex
#   (2) env 파일의 LLM_PROVIDER 값 (기본 .env, MOMO_ENV_FILE로 지정 가능)
#   (3) claude (기본값)
#
# 사용법:
#   ./start.sh                       — .env의 LLM_PROVIDER 자동 감지, 포그라운드 실행
#   ./start.sh claude                — Claude Code CLI 모드
#   ./start.sh codex                 — Codex CLI 모드
#   ./start.sh [claude|codex] -d     — 백그라운드(데몬) 실행
#   ./start.sh [claude|codex] stop   — 백그라운드 프로세스 종료
#   ./start.sh [claude|codex] status — 실행 상태 확인
#   ./start.sh [claude|codex] logs   — 실시간 로그 보기
#
# env 파일: 항상 .env 한 개. (MOMO_ENV_FILE로 다른 파일 지정 가능)
# 모드별로는 port/pid/log만 분리되어 동시에 두 모드를 나란히 실행할 수 있다.
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"

# 모드 결정 순서: (1) 명시 인자 > (2) env 파일의 LLM_PROVIDER > (3) claude 기본값
MODE=""
if [ $# -gt 0 ]; then
    case "$1" in
        claude|codex)
            MODE="$1"
            shift
            ;;
    esac
fi

if [ -z "$MODE" ]; then
    # env 파일이 지정됐으면 그것, 아니면 .env를 후보로 읽어 LLM_PROVIDER 확인
    PROBE_ENV="${MOMO_ENV_FILE:-$APP_DIR/.env}"
    if [ -f "$PROBE_ENV" ]; then
        # 마지막 LLM_PROVIDER= 라인을 사용 (주석 무시)
        LLM_PROVIDER_VAL=$(
            grep -E '^[[:space:]]*LLM_PROVIDER[[:space:]]*=' "$PROBE_ENV" \
                | tail -1 \
                | cut -d= -f2- \
                | sed 's/#.*$//' \
                | tr -d ' "'"'" \
                | tr '[:upper:]' '[:lower:]' \
                || true
        )
        case "$LLM_PROVIDER_VAL" in
            codex_cli|codex)
                MODE="codex"
                ;;
            claude_code|claude)
                MODE="claude"
                ;;
        esac
    fi
fi

MODE="${MODE:-claude}"

# env 파일은 양 모드 공통으로 .env 사용. MOMO_ENV_FILE로 오버라이드 가능.
export MOMO_ENV_FILE="${MOMO_ENV_FILE:-$APP_DIR/.env}"
export MOMO_AUTO_MIGRATE="${MOMO_AUTO_MIGRATE:-false}"

# 모드별로 port/pid/log만 분리 — 두 모드를 동시에 띄울 수 있도록.
case "$MODE" in
    codex)
        export MOMO_PORT="${MOMO_PORT:-9200}"
        export MOMO_PID_FILE="${MOMO_PID_FILE:-$APP_DIR/.momo-codex.pid}"
        export MOMO_LOG_FILE="${MOMO_LOG_FILE:-$APP_DIR/logs/momo-trading-codex.log}"
        MODE_LABEL="Codex CLI"
        ;;
    claude)
        export MOMO_PORT="${MOMO_PORT:-9000}"
        export MOMO_PID_FILE="${MOMO_PID_FILE:-$APP_DIR/.momo.pid}"
        export MOMO_LOG_FILE="${MOMO_LOG_FILE:-$APP_DIR/logs/momo-trading.log}"
        MODE_LABEL="Claude Code"
        ;;
esac

# 앱이 LLM_PROVIDER 로직에서 모드를 실제로 결정하도록 환경변수로 강제 전달
export LLM_PROVIDER="${LLM_PROVIDER:-$([ "$MODE" = "codex" ] && echo "CODEX_CLI" || echo "CLAUDE_CODE")}"

PID_FILE="$MOMO_PID_FILE"
LOG_FILE="$MOMO_LOG_FILE"
ENV_FILE="$MOMO_ENV_FILE"
HOST="${MOMO_HOST:-0.0.0.0}"
PORT="$MOMO_PORT"

# venv 활성화
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "❌ venv 없음: $VENV_DIR"
    echo "   python -m venv venv && pip install -r requirements.txt"
    exit 1
fi

cd "$APP_DIR"

# env 체크
if [ ! -f "$ENV_FILE" ]; then
    echo "⚠️  env 파일이 없습니다: $ENV_FILE"
    echo "   .env.example을 복사해 .env를 구성하세요."
fi

# 로그/데이터 디렉토리 준비
mkdir -p "$(dirname "$LOG_FILE")" "$APP_DIR/data"

# codex 모드: 전용 DB 마이그레이션 선택
run_auto_migrate() {
    if [ "$MOMO_AUTO_MIGRATE" = "true" ]; then
        echo "📦 마이그레이션 적용: $ENV_FILE"
        "$VENV_DIR/bin/python" -m alembic upgrade head
    fi
}

case "${1:-}" in
    stop)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "🛑 momo-trading [$MODE_LABEL] 종료 (PID: $PID)"
                kill "$PID"
                rm -f "$PID_FILE"
            else
                echo "프로세스가 이미 종료됨 (stale PID: $PID)"
                rm -f "$PID_FILE"
            fi
        else
            echo "실행 중인 프로세스 없음 ($MODE_LABEL)"
        fi
        ;;

    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                echo "✅ momo-trading [$MODE_LABEL] 실행 중 (PID: $PID)"
                echo "   http://localhost:$PORT/admin"
            else
                echo "❌ 프로세스 종료됨 (stale PID: $PID)"
                rm -f "$PID_FILE"
            fi
        else
            echo "❌ 실행 중인 프로세스 없음 ($MODE_LABEL)"
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
            echo "이미 실행 중 ($MODE_LABEL, PID: $(cat "$PID_FILE"))"
            exit 1
        fi

        run_auto_migrate

        echo "🚀 momo-trading [$MODE_LABEL] 백그라운드 시작"
        echo "   Host: $HOST:$PORT"
        echo "   Admin: http://localhost:$PORT/admin"
        echo "   Env: $ENV_FILE"
        echo "   Log: $LOG_FILE"

        nohup python -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info \
            >> "$LOG_FILE" 2>&1 &

        echo $! > "$PID_FILE"
        echo "   PID: $(cat "$PID_FILE")"
        echo ""
        echo "종료: ./start.sh $MODE stop"
        ;;

    ""|--foreground)
        run_auto_migrate

        echo "🚀 momo-trading [$MODE_LABEL] 시작 (포그라운드)"
        echo "   Host: $HOST:$PORT"
        echo "   Admin: http://localhost:$PORT/admin"
        echo "   Env: $ENV_FILE"
        echo "   Log: $LOG_FILE"
        echo "   종료: Ctrl+C"
        echo ""

        python -m uvicorn main:app \
            --host "$HOST" --port "$PORT" \
            --log-level info \
            --reload 2>&1 | tee -a "$LOG_FILE"
        ;;

    *)
        echo "사용법: $0 [claude|codex] [-d|stop|status|logs]"
        exit 1
        ;;
esac
