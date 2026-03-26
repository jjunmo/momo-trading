from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def ensure_kst(dt: datetime) -> datetime:
    """naive datetime에 KST timezone 부여 (SQLite는 tz를 저장하지 않음)"""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt