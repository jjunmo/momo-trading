"""LLM 응답에서 JSON을 안전하게 추출하는 유틸리티

LLM이 생성하는 JSON은 종종 문법 오류를 포함:
- trailing comma  {"a": 1,}
- 코드블록 마크다운  ```json ... ```
- 단일 따옴표 사용
- 제어 문자 포함
"""
import json
import re

from loguru import logger


def parse_llm_json(text: str) -> dict:
    """LLM 텍스트 응답에서 JSON 객체를 추출·파싱한다.

    1차: 코드블록 추출 → json.loads
    2차: 첫 { ~ 마지막 } 추출 → json.loads
    3차: trailing comma 등 일반적 오류 자동 수정 후 재시도

    Returns:
        파싱된 dict. 실패 시 빈 dict.
    """
    if not text or not text.strip():
        return {}

    # 1단계: ```json ... ``` 코드블록 추출
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        candidate = code_block.group(1).strip()
        result = _try_parse(candidate)
        if result is not None:
            return result

    # 1.5단계: 코드블록 내 개행 수정 후 재시도
    if code_block:
        candidate = code_block.group(1).strip()
        fixed = _escape_newlines_in_strings(candidate)
        result = _try_parse(fixed)
        if result is not None:
            return result

    # 2단계: 첫 { ~ 마지막 } 추출
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start:end + 1]
        result = _try_parse(candidate)
        if result is not None:
            return result

        # 3단계: 자동 수정 후 재시도
        fixed = _fix_common_errors(candidate)
        result = _try_parse(fixed)
        if result is not None:
            logger.debug("LLM JSON 자동 수정 후 파싱 성공")
            return result

        # 4단계: 마지막 수단 — 잘린 JSON 복구 시도
        result = _try_recover_truncated(candidate)
        if result is not None:
            logger.warning("LLM JSON 잘림 복구 후 파싱 (데이터 일부 손실 가능)")
            return result

    logger.warning("LLM JSON 파싱 최종 실패 — text[:200]: {}", text[:200])
    return {}


def _try_parse(text: str) -> dict | None:
    """json.loads 시도. 성공 시 dict, 실패 시 None."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _fix_common_errors(text: str) -> str:
    """LLM JSON의 흔한 문법 오류 수정"""
    # 먼저 JSON 문자열 내의 리터럴 개행을 이스케이프
    s = _escape_newlines_in_strings(text)

    # trailing comma 제거: ,] → ] , ,} → }
    s = re.sub(r",\s*([}\]])", r"\1", s)

    # 단일 따옴표 → 이중 따옴표 (JSON 문자열 내부가 아닌 경우)
    # 주의: 이건 완벽하지 않지만 대부분의 LLM 출력에서 동작
    s = re.sub(r"(?<![\\])'\s*:", '":', s)
    s = re.sub(r":\s*'(?![\\])", ': "', s)
    s = re.sub(r"(?<![\\])'\s*([,}\]])", r'"\1', s)
    s = re.sub(r"([{\[,])\s*'", r'\1 "', s)

    # 제어 문자 제거 (탭, 개행은 유지)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)

    return s


def _escape_newlines_in_strings(text: str) -> str:
    """JSON 문자열 내의 리터럴 개행(\\n, \\r, \\t)을 이스케이프 시퀀스로 변환

    LLM이 JSON 문자열 값에 리터럴 개행을 넣는 경우가 잦음:
      "checklist_notes": "
          [1] ADX 49.97..."
    이를 유효한 JSON으로 변환:
      "checklist_notes": "\\n          [1] ADX 49.97..."
    """
    result = []
    in_string = False
    escape_next = False

    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue

        if ch == '\\' and in_string:
            result.append(ch)
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue

        if in_string:
            if ch == '\n':
                result.append('\\n')
                continue
            if ch == '\r':
                result.append('\\r')
                continue
            if ch == '\t':
                result.append('\\t')
                continue

        result.append(ch)

    return ''.join(result)


def _try_recover_truncated(text: str) -> dict | None:
    """잘린 JSON 복구 — 열린 괄호를 닫아서 파싱 시도"""
    # 가장 마지막 유효 위치에서 잘라내기
    # candidates 배열이 잘린 경우가 가장 흔함
    s = _fix_common_errors(text)

    # 열린 문자열 닫기 (마지막 " 이후 닫히지 않은 경우)
    quote_count = s.count('"') - s.count('\\"')
    if quote_count % 2 == 1:
        s += '"'

    # 열린 괄호 수 세기
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")

    # 닫아주기
    s += "]" * max(0, open_brackets)
    s += "}" * max(0, open_braces)

    return _try_parse(s)
