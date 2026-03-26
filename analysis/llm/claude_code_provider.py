"""Claude Code CLI Provider — 로컬 Claude Code 구독으로 LLM 호출

claude -p 모드를 사용하여 API 키 없이 Claude Code 구독 크레딧으로 동작.
세션을 유지하여 사이클 내 맥락(시장 스캔 → 종목 분석 → 리포트)을 공유.
"""
import asyncio
import json
import shutil
from collections import defaultdict
from uuid import uuid4

from loguru import logger

from core.config import settings
from trading.enums import LLMProvider, LLMTier


class ClaudeCodeProvider:
    """Claude Code CLI를 subprocess로 호출하는 LLM Provider

    세션 관리:
    - start_session(): 새 세션 시작 (사이클 시작 시)
    - 이후 generate() 호출은 --resume로 같은 세션 이어감
    - end_session(): 세션 종료, ID 반환 (나중에 resume 가능)
    - resume_session(id): 이전 세션 이어서 사용

    세션이 없으면 일회성 호출 (--no-session-persistence)
    """

    # 클래스 레벨 세션 관리 (모든 인스턴스 공유)
    _active_session_id: str | None = None
    _session_initialized: bool = False  # 첫 호출 완료 여부
    _session_lock: asyncio.Lock | None = None

    # 클래스 레벨 누적 사용량
    cumulative_usage: dict = {
        "total_calls": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read": 0,
        "total_cache_creation": 0,
        "by_model": defaultdict(lambda: {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_creation": 0, "cost_usd": 0.0,
        }),
    }

    def __init__(self, tier: LLMTier = LLMTier.TIER1):
        self._tier = tier
        self._claude_path: str | None = None
        # Tier별 모델: TIER1=haiku(빠름), TIER2=sonnet(정확)
        if tier == LLMTier.TIER1:
            self._model = settings.CLAUDE_CODE_MODEL_TIER1 or settings.CLAUDE_CODE_MODEL or "haiku"
        else:
            self._model = settings.CLAUDE_CODE_MODEL_TIER2 or settings.CLAUDE_CODE_MODEL or "sonnet"
        self._resolved_model: str = ""

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """세션 락 (resume 호출 직렬화)"""
        if cls._session_lock is None:
            cls._session_lock = asyncio.Lock()
        return cls._session_lock

    # ── 세션 관리 ──

    @classmethod
    def start_session(cls) -> str:
        """새 세션 시작 — 사이클/거래일 시작 시 호출"""
        cls._active_session_id = str(uuid4())
        cls._session_initialized = False
        logger.debug("Claude Code 세션 시작: {}", cls._active_session_id[:8])
        return cls._active_session_id

    @classmethod
    def end_session(cls) -> str | None:
        """세션 종료 — 세션 ID 반환 (나중에 resume 가능)"""
        sid = cls._active_session_id
        if sid:
            logger.debug("Claude Code 세션 종료: {}", sid[:8])
        cls._active_session_id = None
        cls._session_initialized = False
        return sid

    @classmethod
    def pause_session(cls) -> str | None:
        """세션 일시 중지 — 병렬 분석 구간에서 사용

        세션 ID를 보존하되 활성 상태 해제 → generate()가 일회성 호출로 동작.
        병렬 분석 완료 후 resume_session()으로 복원.
        """
        sid = cls._active_session_id
        if sid:
            logger.debug("Claude Code 세션 일시 중지: {} (병렬 구간)", sid[:8])
        cls._active_session_id = None
        # _session_initialized는 보존 (resume 시 그대로 사용)
        return sid

    @classmethod
    def resume_session(cls, session_id: str) -> None:
        """이전 세션 재개 — 장 재개, 다음 사이클 등"""
        cls._active_session_id = session_id
        cls._session_initialized = True  # 이미 디스크에 존재
        logger.debug("Claude Code 세션 재개: {}", session_id[:8])

    @classmethod
    def get_session_id(cls) -> str | None:
        return cls._active_session_id

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CLAUDE_CODE

    @property
    def tier(self) -> LLMTier:
        return self._tier

    @property
    def model_id(self) -> str:
        return self._resolved_model or f"claude-code:{self._model}"

    def _find_claude(self) -> str | None:
        """claude CLI 경로 탐색"""
        if self._claude_path:
            return self._claude_path
        configured = getattr(settings, "CLAUDE_CODE_PATH", "")
        if configured:
            self._claude_path = configured
            return configured
        path = shutil.which("claude")
        if path:
            self._claude_path = path
            return path
        import os
        for candidate in [
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
            os.path.expanduser("~/.local/bin/claude"),
            os.path.expanduser("~/.npm-global/bin/claude"),
        ]:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                self._claude_path = candidate
                return candidate
        return None

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """claude -p 로 텍스트 생성

        세션이 활성화된 경우:
        - 첫 호출: --session-id로 세션 생성 + system_prompt 설정
        - 이후 호출: --resume로 세션 이어감 (맥락 유지)
          - system_prompt는 사용자 프롬프트 앞에 [역할] 로 포함

        세션이 없는 경우:
        - --no-session-persistence로 일회성 호출
        """
        claude = self._find_claude()
        if not claude:
            raise RuntimeError("claude CLI를 찾을 수 없습니다 (PATH 확인)")

        # Tier별 effort: TIER1(스캔/분석)=medium, TIER2(최종검토)=high
        effort = "medium" if self._tier == LLMTier.TIER1 else "high"

        cmd = [
            claude, "-p",
            "--output-format", "json",
            "--model", self._model,
            "--max-turns", "1",
            "--effort", effort,
            "--dangerously-skip-permissions",
        ]

        actual_prompt = prompt

        if self._active_session_id:
            if self._session_initialized:
                # 기존 세션 이어감
                cmd.extend(["--resume", self._active_session_id])
                # resume 시 system_prompt 변경 불가 → 프롬프트 앞에 역할 명시
                if system_prompt:
                    actual_prompt = f"[역할]\n{system_prompt}\n\n[요청]\n{prompt}"
            else:
                # 첫 호출: 세션 생성
                cmd.extend(["--session-id", self._active_session_id])
                if system_prompt:
                    cmd.extend(["--system-prompt", system_prompt])
        else:
            # 세션 없음: 일회성
            cmd.extend(["--no-session-persistence"])
            if system_prompt:
                cmd.extend(["--system-prompt", system_prompt])

        # 세션 사용 시 직렬화 (같은 세션에 동시 resume 방지)
        if self._active_session_id:
            async with self._get_lock():
                result = await self._execute(cmd, actual_prompt)
                # 첫 호출 성공 후 세션 초기화 완료 표시
                if not self.__class__._session_initialized:
                    self.__class__._session_initialized = True
                return result
        else:
            return await self._execute(cmd, actual_prompt)

    @staticmethod
    def _clean_env() -> dict:
        """subprocess용 환경변수 — CLAUDECODE 제거 (중첩 세션 방지)"""
        import os
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        return env

    async def _execute(self, cmd: list, prompt: str) -> str:
        """subprocess 실행 + JSON 파싱"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._clean_env(),
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=300.0,
        )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            # stderr 비어있으면 stdout에서 에러 메시지 추출
            if not err.strip():
                err = stdout.decode("utf-8", errors="replace")[:500]
            logger.error("Claude Code 호출 실패 (exit {}): {}", proc.returncode, err)
            raise RuntimeError(f"Claude Code 실패 (exit {proc.returncode}): {err}")

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            raise RuntimeError("Claude Code 빈 응답")

        try:
            resp = json.loads(raw)
            result_text = resp.get("result", "")
            self._track_usage(resp)
        except json.JSONDecodeError:
            result_text = raw

        if not result_text:
            raise RuntimeError("Claude Code 빈 응답")

        return result_text

    def _track_usage(self, resp: dict) -> None:
        """JSON 응답에서 토큰 사용량 누적"""
        cost = resp.get("total_cost_usd", 0)
        model_usage = resp.get("modelUsage", {})

        self.cumulative_usage["total_calls"] += 1
        self.cumulative_usage["total_cost_usd"] += cost

        for model_name, usage in model_usage.items():
            inp = usage.get("inputTokens", 0)
            out = usage.get("outputTokens", 0)
            cache_r = usage.get("cacheReadInputTokens", 0)
            cache_c = usage.get("cacheCreationInputTokens", 0)
            model_cost = usage.get("costUSD", 0)

            self.cumulative_usage["total_input_tokens"] += inp
            self.cumulative_usage["total_output_tokens"] += out
            self.cumulative_usage["total_cache_read"] += cache_r
            self.cumulative_usage["total_cache_creation"] += cache_c

            m = self.cumulative_usage["by_model"][model_name]
            m["calls"] += 1
            m["input_tokens"] += inp
            m["output_tokens"] += out
            m["cache_read"] += cache_r
            m["cache_creation"] += cache_c
            m["cost_usd"] += model_cost

            if not self._resolved_model or model_cost > 0.01:
                self._resolved_model = model_name

    @classmethod
    def get_usage_snapshot(cls) -> dict:
        """현재 누적 사용량 스냅샷 반환 (API용)"""
        u = cls.cumulative_usage
        return {
            "total_calls": u["total_calls"],
            "total_cost_usd": round(u["total_cost_usd"], 4),
            "total_input_tokens": u["total_input_tokens"],
            "total_output_tokens": u["total_output_tokens"],
            "total_cache_read": u["total_cache_read"],
            "total_cache_creation": u["total_cache_creation"],
            "by_model": {
                model: {**stats}
                for model, stats in u["by_model"].items()
            },
            "session_id": cls._active_session_id[:8] if cls._active_session_id else None,
        }

    async def is_available(self) -> bool:
        """claude CLI 설치 여부 확인"""
        path = self._find_claude()
        if not path:
            logger.debug("Claude Code CLI를 찾을 수 없음 (PATH, /opt/homebrew/bin 등 확인)")
        return path is not None
