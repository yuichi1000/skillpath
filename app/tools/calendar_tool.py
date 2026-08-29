"""Google Calendar API ラッパ (設計書 §4.2 Scheduler)。

冪等性: イベント作成時に LearningSession.id を
extendedProperties.private.skillpath_session_id として付与し、
再実行時は同 ID の既存イベントを検索して update / insert を切り替える (設計書 §5)。
"""

from datetime import datetime

SESSION_ID_PROP = "skillpath_session_id"


def get_freebusy(
    uid: str, time_min: datetime, time_max: datetime
) -> list[tuple[datetime, datetime]]:
    """FreeBusy API で空き時間帯を返す。OAuth トークンは Secret Manager/Firestore から取得。"""
    raise NotImplementedError("TODO: Calendar FreeBusy API")


def upsert_event(
    uid: str,
    session_id: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
) -> str:
    """skillpath_session_id で既存イベントを検索し、あれば更新・なければ作成。event_id を返す。"""
    raise NotImplementedError("TODO: Calendar events.insert / events.update")
