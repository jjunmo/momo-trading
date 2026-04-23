"""LLM provider subprocess 공통 유틸리티.

Claude/Codex CLI 둘 다 subprocess 실행 시 동일한 방식으로 정리하도록 공유한다.
각 provider는 프로세스 그룹(start_new_session=True)을 만들고,
타임아웃/취소 시 terminate_process_group을 호출한다.
"""
import asyncio
import os
import signal

from loguru import logger


async def terminate_process_group(
    proc: asyncio.subprocess.Process, *, force: bool = True
) -> None:
    """subprocess를 프로세스 그룹 단위로 안전하게 종료한다.

    1) 이미 종료됐으면 아무 것도 안 함
    2) SIGTERM으로 그룹 전체 종료 시도 → 2초 대기
    3) force=True면 SIGKILL로 강제 종료 → 2초 대기
    """
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
            logger.warning("LLM CLI 프로세스 강제 종료 대기 실패: pid={}", proc.pid)
