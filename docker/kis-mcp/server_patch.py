"""Patch upstream KIS_MCP_Server for token cache safety.

The upstream image is cloned during Docker build, so repository changes must be
applied as a deterministic source patch at build time.
"""
from pathlib import Path


SERVER = Path("/app/server.py")


def main() -> None:
    text = SERVER.read_text()

    # 재실행 시 조용히 실패하면 업스트림이 바뀐 건지 두 번 적용된 건지 구분이 안 된다.
    if "TOKEN_EXPIRED_CODE" in text:
        raise SystemExit("server_patch.py: already applied to /app/server.py")

    if "import asyncio" not in text:
        text = text.replace("import json\n", "import asyncio\nimport hashlib\nimport json\n", 1)
    elif "import hashlib" not in text:
        text = text.replace("import asyncio\n", "import asyncio\nimport hashlib\n", 1)

    try:
        start = text.index("# Token storage")
        end = text.index("async def get_hashkey", start)
    except ValueError as exc:
        raise SystemExit(
            "server_patch.py: upstream markers '# Token storage' / 'async def get_hashkey' "
            "not found — upstream KIS_MCP_Server changed and this patch needs updating."
        ) from exc
    token_block = '''# Token storage
TOKEN_EXPIRED_CODE = "EGW00123"
TOKEN_REFRESH_SKEW = timedelta(minutes=5)


def _token_file_path() -> Path:
    configured = os.environ.get("KIS_TOKEN_FILE")
    if configured:
        return Path(configured)

    account_type = os.environ.get("KIS_ACCOUNT_TYPE", "VIRTUAL").upper()
    suffix = "virtual" if account_type == "VIRTUAL" else "real"
    token_dir = Path(os.environ.get("KIS_TOKEN_DIR", "/app/data"))
    return token_dir / f"kis_token.{suffix}.json"


TOKEN_FILE = _token_file_path()
_token_lock = asyncio.Lock()
_cached_token = None
_cached_expires_at = None


def _token_context() -> dict:
    app_key = os.environ.get("KIS_APP_KEY", "")
    return {
        "account_type": os.environ.get("KIS_ACCOUNT_TYPE", "VIRTUAL").upper(),
        "app_key_hash": hashlib.sha256(app_key.encode()).hexdigest() if app_key else "",
    }


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _token_expires_at(token_data: dict, issued_at: datetime | None = None) -> datetime:
    issued_at = issued_at or datetime.now()
    expires_at = (
        _parse_datetime(token_data.get("access_token_token_expired"))
        or _parse_datetime(token_data.get("expires_at"))
    )
    if expires_at:
        return expires_at

    expires_in = token_data.get("expires_in")
    try:
        if expires_in:
            return issued_at + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        pass

    return issued_at + timedelta(hours=23)


def _is_token_valid(expires_at: datetime) -> bool:
    return datetime.now() + TOKEN_REFRESH_SKEW < expires_at


def _token_matches_context(token_data: dict) -> bool:
    context = _token_context()
    return (
        token_data.get("account_type") == context["account_type"]
        and token_data.get("app_key_hash") == context["app_key_hash"]
    )


def _clear_cached_token(remove_file: bool = False):
    global _cached_token, _cached_expires_at

    _cached_token = None
    _cached_expires_at = None
    if remove_file:
        try:
            TOKEN_FILE.unlink()
        except FileNotFoundError:
            pass


def load_token():
    """Load token from file if it exists, matches this app key, and is not expired."""
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, "r") as f:
                token_data = json.load(f)
            expires_at = _token_expires_at(token_data)
            if _token_matches_context(token_data) and _is_token_valid(expires_at):
                return token_data["token"], expires_at
        except Exception as e:
            print(f"Error loading token: {e}", file=sys.stderr)
    return None, None


def save_token(token: str, expires_at: datetime, issued_at: datetime):
    """Save token atomically. Host (trading/kis_api.py) shares this file via bind
    mount, so we tmp-write then replace to avoid torn reads during concurrent issuance.
    """
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = TOKEN_FILE.with_suffix(TOKEN_FILE.suffix + ".tmp")
        with open(tmp_path, "w") as f:
            json.dump({
                "token": token,
                "expires_at": expires_at.isoformat(),
                "issued_at": issued_at.isoformat(),
                **_token_context(),
            }, f)
        os.replace(tmp_path, TOKEN_FILE)
    except Exception as e:
        print(f"Error saving token: {e}", file=sys.stderr)


async def get_access_token(client: httpx.AsyncClient, force_refresh: bool = False) -> str:
    """
    Get access token with in-process serialization to avoid EGW00133 bursts.
    Returns cached token if valid, otherwise requests new token.
    """
    global _cached_token, _cached_expires_at

    if not force_refresh and _cached_token and _cached_expires_at and _is_token_valid(_cached_expires_at):
        return _cached_token

    for attempt in range(2):
        async with _token_lock:
            if force_refresh and attempt == 0:
                _clear_cached_token(remove_file=True)

            if not force_refresh and _cached_token and _cached_expires_at and _is_token_valid(_cached_expires_at):
                return _cached_token

            if not force_refresh or attempt > 0:
                token, expires_at = load_token()
                if token and expires_at and _is_token_valid(expires_at):
                    _cached_token = token
                    _cached_expires_at = expires_at
                    return token

            token_response = await client.post(
                f"{DOMAIN}{TOKEN_PATH}",
                headers={"content-type": CONTENT_TYPE},
                json={
                    "grant_type": "client_credentials",
                    "appkey": os.environ["KIS_APP_KEY"],
                    "appsecret": os.environ["KIS_APP_SECRET"],
                },
            )

            if token_response.status_code == 200:
                token_data = token_response.json()
                token = token_data["access_token"]
                issued_at = datetime.now()
                expires_at = _token_expires_at(token_data, issued_at)
                _cached_token = token
                _cached_expires_at = expires_at
                save_token(token, expires_at, issued_at)
                return token

            response_text = token_response.text
            if "EGW00133" not in response_text or attempt > 0:
                raise Exception(f"Failed to get token: {response_text}")

            logger.warning("KIS token issuance limited (EGW00133), retrying after 60 seconds")

        await asyncio.sleep(60)

    raise Exception("Failed to get token: issuance retry exhausted")


def _response_has_expired_token(response: httpx.Response) -> bool:
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("msg_cd") == TOKEN_EXPIRED_CODE:
            return True
    except Exception:
        pass
    return TOKEN_EXPIRED_CODE in response.text


_original_async_request = httpx.AsyncClient.request


async def _request_with_token_retry(self, method, url, **kwargs):
    response = await _original_async_request(self, method, url, **kwargs)
    if TOKEN_PATH in str(url) or not _response_has_expired_token(response):
        return response

    headers = dict(kwargs.get("headers") or {})
    auth_header = next((key for key in headers if key.lower() == "authorization"), None)
    if not auth_header:
        return response

    logger.warning("KIS token expired response (EGW00123), refreshing token and retrying once")
    token = await get_access_token(self, force_refresh=True)
    headers[auth_header] = f"{AUTH_TYPE} {token}"
    kwargs["headers"] = headers
    return await _original_async_request(self, method, url, **kwargs)


httpx.AsyncClient.request = _request_with_token_retry


'''
    text = text[:start] + token_block + text[end:]
    SERVER.write_text(text)


if __name__ == "__main__":
    main()
