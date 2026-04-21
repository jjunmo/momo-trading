"""Codex CLI Provider — 로컬 Codex CLI로 LLM 호출.

Codex CLI는 임의 session id를 미리 지정할 수 없으므로, start_session()은
"다음 호출부터 persistent thread를 사용하라"는 플래그만 세운다.
첫 generate()가 thread.started 이벤트에서 실제 thread id를 받아 저장한다.
"""
import asyncio
import json
import os
import signal
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from loguru import logger

from core.config import settings
from trading.enums import LLMProvider, LLMTier


class CodexCLIProvider:
    """Codex CLI subprocess provider.

    세션 관리:
    - start_session(): 다음 호출부터 persistent Codex thread 사용
    - pause_session(): 병렬 분석 구간에서는 ephemeral 호출로 전환
    - resume_session(id): 기존 Codex thread 재개
    - end_session(): 현재 thread id 반환 후 세션 사용 중단
    """

    _active_session_id: str | None = None
    _session_enabled: bool = False
    _session_initialized: bool = False
    _session_lock: asyncio.Lock | None = None
    _session_started_at: float = 0.0
    SESSION_TTL_SECONDS: float = 1800.0

    cumulative_usage: dict = {
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "by_model": defaultdict(lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }),
    }

    def __init__(self, tier: LLMTier = LLMTier.TIER1):
        self._tier = tier
        self._codex_path: str | None = None
        self._model = settings.get_llm_model(LLMProvider.CODEX_CLI, tier)
        self._reasoning_effort = settings.get_llm_reasoning_effort(LLMProvider.CODEX_CLI, tier)
        self._resolved_model: str = ""

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._session_lock is None:
            cls._session_lock = asyncio.Lock()
        return cls._session_lock

    @classmethod
    def start_session(cls) -> str | None:
        """새 persistent thread 사용을 예약."""
        cls._active_session_id = None
        cls._session_enabled = True
        cls._session_initialized = False
        cls._session_started_at = time.time()
        logger.debug("Codex CLI 세션 시작 예약")
        return None

    @classmethod
    def end_session(cls) -> str | None:
        sid = cls._active_session_id
        if sid:
            logger.debug("Codex CLI 세션 종료: {}", sid[:8])
        cls._active_session_id = None
        cls._session_enabled = False
        cls._session_initialized = False
        cls._session_started_at = 0.0
        return sid

    @classmethod
    def pause_session(cls) -> str | None:
        sid = cls._active_session_id
        if sid:
            logger.debug("Codex CLI 세션 일시 중지: {} (병렬 구간)", sid[:8])
        cls._active_session_id = None
        cls._session_enabled = False
        return sid

    @classmethod
    def resume_session(cls, session_id: str) -> None:
        cls._active_session_id = session_id
        cls._session_enabled = True
        cls._session_initialized = True
        if cls._session_started_at == 0:
            cls._session_started_at = time.time()
        logger.debug("Codex CLI 세션 재개: {}", session_id[:8])

    @classmethod
    def reset_session(cls) -> None:
        cls._active_session_id = None
        cls._session_enabled = False
        cls._session_initialized = False
        cls._session_started_at = 0.0
        logger.debug("Codex CLI 세션 상태 초기화")

    @classmethod
    def _is_session_expired(cls) -> bool:
        if not cls._session_enabled or cls._session_started_at == 0:
            return False
        return (time.time() - cls._session_started_at) > cls.SESSION_TTL_SECONDS

    @classmethod
    def _rotate_session(cls, reason: str = "") -> None:
        old_id = cls._active_session_id
        old_short = old_id[:8] if old_id else "없음"
        cls.start_session()
        logger.info("Codex CLI 세션 교체: {} → pending (사유: {})", old_short, reason or "unknown")

    @classmethod
    def get_session_id(cls) -> str | None:
        return cls._active_session_id

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CODEX_CLI

    @property
    def tier(self) -> LLMTier:
        return self._tier

    @property
    def model_id(self) -> str:
        suffix = f":{self._reasoning_effort}" if self._reasoning_effort else ""
        return self._resolved_model or f"codex:{self._model}{suffix}"

    def _find_codex(self) -> str | None:
        if self._codex_path:
            return self._codex_path
        path = settings.get_llm_cli_path(LLMProvider.CODEX_CLI)
        if path:
            self._codex_path = path
        return path

    def _build_prompt(self, prompt: str, system_prompt: str = "") -> str:
        if not system_prompt:
            return prompt
        return f"[역할]\n{system_prompt}\n\n[요청]\n{prompt}"

    def _build_command(self, output_path: str) -> list[str]:
        codex = self._find_codex()
        if not codex:
            raise RuntimeError("codex CLI를 찾을 수 없습니다 (PATH 또는 CODEX_CLI_PATH 확인)")

        base_flags = [
            "--json",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--model", self._model,
            "-o", output_path,
        ]
        if self._reasoning_effort:
            base_flags.extend(["-c", f"model_reasoning_effort={self._reasoning_effort}"])
        if settings.CODEX_DISABLE_MCP:
            base_flags.extend(["-c", "mcp_servers={}"])

        if self._session_enabled and self._active_session_id and self._session_initialized:
            return [codex, "exec", "resume", *base_flags, self._active_session_id, "-"]

        cmd = [codex, "exec", *base_flags]
        if not self._session_enabled:
            cmd.append("--ephemeral")
        cmd.append("-")
        return cmd

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        if self._session_enabled and self._is_session_expired():
            self._rotate_session(reason="TTL 만료 (30분)")

        actual_prompt = self._build_prompt(prompt, system_prompt)

        if self._session_enabled:
            async with self._get_lock():
                return await self._execute_with_output_file(actual_prompt)
        return await self._execute_with_output_file(actual_prompt)

    async def _execute_with_output_file(self, prompt: str) -> str:
        output_file = tempfile.NamedTemporaryFile(prefix="momo-codex-", suffix=".txt", delete=False)
        output_path = output_file.name
        output_file.close()
        try:
            cmd = self._build_command(output_path)
            return await self._execute(cmd, prompt, output_path)
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass

    @staticmethod
    def _clean_env() -> dict:
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        return env

    async def _execute(self, cmd: list[str], prompt: str, output_path: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._clean_env(),
                start_new_session=True,
            )
        except OSError as e:
            raise RuntimeError(f"Codex CLI 프로세스 생성 실패: {e}")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            await self._terminate_process_group(proc, force=True)
            raise RuntimeError("Codex CLI 응답 시간 초과 (300초)")
        except asyncio.CancelledError:
            await self._terminate_process_group(proc, force=True)
            raise

        raw_stdout = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500] or raw_stdout[:500]
            logger.error("Codex CLI 호출 실패 (exit {}): {}", proc.returncode, err)
            raise RuntimeError(f"Codex CLI 실패 (exit {proc.returncode}): {err}")

        events = self._parse_jsonl(raw_stdout)
        result_text = self._read_output_file(output_path) or events["text"]
        result_text = result_text.strip()
        if not result_text:
            raise RuntimeError("Codex CLI 빈 응답")

        thread_id = events["thread_id"]
        if self._session_enabled and thread_id:
            self.__class__._active_session_id = thread_id
            self.__class__._session_initialized = True

        self._track_usage(events["usage"])
        return result_text

    @staticmethod
    async def _terminate_process_group(proc: asyncio.subprocess.Process, *, force: bool) -> None:
        """Codex wrapper와 native child를 함께 종료."""
        if proc.returncode is not None:
            return

        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            pass

        if force:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Codex CLI 프로세스 강제 종료 대기 실패: pid={}", proc.pid)

    @staticmethod
    def _read_output_file(output_path: str) -> str:
        try:
            return Path(output_path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _extract_text_from_item(item: dict) -> str:
        content = item.get("content", [])
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text") or block.get("output_text")
            if text:
                parts.append(str(text))
        return "\n".join(parts)

    def _parse_jsonl(self, raw: str) -> dict:
        text_parts: list[str] = []
        thread_id: str | None = None
        usage: dict = {}

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type") or event.get("event")
            if event_type == "thread.started":
                thread_id = (
                    event.get("thread_id")
                    or event.get("threadId")
                    or (event.get("thread") or {}).get("id")
                )
            elif event_type == "item.completed":
                item = event.get("item") or event
                if item.get("role") == "assistant" or item.get("type") == "message":
                    item_text = self._extract_text_from_item(item)
                    if item_text:
                        text_parts.append(item_text)
            elif event_type == "turn.completed":
                usage = event.get("usage") or event.get("token_usage") or {}

        return {
            "text": "\n".join(text_parts).strip(),
            "thread_id": thread_id,
            "usage": usage,
        }

    def _track_usage(self, usage: dict) -> None:
        model_name = self._model
        input_tokens = int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("inputTokens")
            or 0
        )
        output_tokens = int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("outputTokens")
            or 0
        )

        self.cumulative_usage["total_calls"] += 1
        self.cumulative_usage["total_input_tokens"] += input_tokens
        self.cumulative_usage["total_output_tokens"] += output_tokens

        m = self.cumulative_usage["by_model"][model_name]
        m["calls"] += 1
        m["input_tokens"] += input_tokens
        m["output_tokens"] += output_tokens

    @classmethod
    def get_usage_snapshot(cls) -> dict:
        u = cls.cumulative_usage
        return {
            "total_calls": u["total_calls"],
            "total_input_tokens": u["total_input_tokens"],
            "total_output_tokens": u["total_output_tokens"],
            "by_model": {
                model: {**stats}
                for model, stats in u["by_model"].items()
            },
            "session_id": cls._active_session_id[:8] if cls._active_session_id else None,
        }

    async def is_available(self) -> bool:
        path = self._find_codex()
        if not path:
            logger.debug("Codex CLI를 찾을 수 없음 (PATH, CODEX_CLI_PATH 확인)")
        return path is not None
