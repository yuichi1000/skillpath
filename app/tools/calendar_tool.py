"""Google Calendar API ラッパ (設計書 §4.2 Scheduler)。

冪等性 (設計書 §5): イベントには extendedProperties.private.skillpath_session_id
を付与し、upsert_event は同 ID の既存イベントを検索して更新 or 作成する。

認証情報の解決順: ローカルの calendar-token.json → Secret Manager
(skillpath-calendar-token)。どちらも無ければ CalendarUnavailable を投げ、
呼び出し側 (scheduler_node) はプレースホルダにフォールバックする。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import get_settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SESSION_ID_PROP = "skillpath_session_id"
TOKEN_PATH = Path("calendar-token.json")
TOKEN_SECRET_ID = "skillpath-calendar-token"


class CalendarUnavailable(Exception):
    pass


_service = None


def _load_token_info() -> dict:
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    settings = get_settings()
    if settings.project_id:
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{settings.project_id}/secrets/{TOKEN_SECRET_ID}/versions/latest"
            payload = client.access_secret_version(name=name).payload.data
            return json.loads(payload.decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 理由を添えてフォールバックさせる
            raise CalendarUnavailable(f"Secret Manager からトークンを取得できません: {e}") from e
    raise CalendarUnavailable("カレンダートークンが未設定です (calendar_auth を実行してください)")


def get_service():
    global _service
    if _service is None:
        creds = Credentials.from_authorized_user_info(_load_token_info(), SCOPES)
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().schedule_tz)


def _to_rfc3339(dt: datetime) -> str:
    return dt.replace(tzinfo=_tz()).isoformat()


# 資格ごとのカレンダーに割り当てる色 (Google Calendar の colorId は 1-24)。
# 資格 ID から決定的に選ぶので、再作成しても同じ資格は同じ色になる。
CALENDAR_COLOR_COUNT = 24


def public_url(calendar_id: str) -> str:
    """カレンダーを一般公開したあとに共有できる閲覧 URL。"""
    tz = get_settings().schedule_tz
    return (
        "https://calendar.google.com/calendar/embed"
        f"?src={quote(calendar_id)}&ctz={quote(tz)}"
    )


def calendar_exists(calendar_id: str) -> bool:
    """カレンダーがまだ存在するか (ユーザーが手で消した場合の作り直し判定)。"""
    try:
        get_service().calendars().get(calendarId=calendar_id).execute()
        return True
    except HttpError as e:
        if e.resp.status in (403, 404):
            return False
        raise


def create_calendar(summary: str, description: str = "", color_key: str = "") -> str:
    """二次カレンダーを作成し、calendar_id を返す。

    公開設定 (ACL) はここでは行わない。一般公開はカレンダーの持ち主が
    Google カレンダーの設定画面から明示的に行う。
    """
    service = get_service()
    created = (
        service.calendars()
        .insert(
            body={
                "summary": summary,
                "description": description,
                "timeZone": get_settings().schedule_tz,
            }
        )
        .execute()
    )
    calendar_id = created["id"]
    if color_key:
        color_id = str(sum(map(ord, color_key)) % CALENDAR_COLOR_COUNT + 1)
        try:
            service.calendarList().patch(
                calendarId=calendar_id, body={"colorId": color_id}
            ).execute()
        except HttpError:  # 色は付けられなくても致命ではない
            logger.warning("カレンダーの色設定に失敗: %s", calendar_id)
    return calendar_id


def _own_session_id(ev: dict) -> str:
    """SkillPath が作ったイベントなら session_id を、そうでなければ空文字を返す。"""
    return ((ev.get("extendedProperties") or {}).get("private") or {}).get(SESSION_ID_PROP, "")


def _is_declined(ev: dict) -> bool:
    """自分が出席を辞退した予定か (辞退済みの会議で学習枠を潰さない)。"""
    return any(
        a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees") or []
    )


MAX_EVENT_PAGES = 40  # 250件/ページ × 40 = 1万件。学習ブロックの現実的な上限を超える


def _iter_events(calendar_id: str, time_min: datetime, time_max: datetime):
    """指定期間のイベントを列挙する。

    ページングは API が返す nextPageToken に従うが、同じトークンが返ってきたり
    ページ数が想定を超えた場合は打ち切る (外部 API に無限に付き合わない)。
    """
    service = get_service()
    page_token = None
    seen_tokens: set[str] = set()
    for _page in range(MAX_EVENT_PAGES):
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=_to_rfc3339(time_min),
                timeMax=_to_rfc3339(time_max),
                singleEvents=True,
                maxResults=250,
                pageToken=page_token,
            )
            .execute()
        )
        yield from resp.get("items", [])
        page_token = resp.get("nextPageToken")
        if not page_token or page_token in seen_tokens:
            break
        seen_tokens.add(page_token)
    else:
        logger.warning(
            "カレンダー %s のイベント列挙を %d ページで打ち切りました", calendar_id, MAX_EVENT_PAGES
        )


def get_busy(
    time_min: datetime,
    time_max: datetime,
    calendar_ids: tuple[str, ...] = ("primary",),
    own_prefix: str = "",
) -> list[tuple[datetime, datetime]]:
    """busy 区間をローカル時刻の naive datetime で返す。

    FreeBusy API ではなく events.list を使う: FreeBusy は SkillPath 自身が作った
    学習ブロックも busy として返し、しかも extendedProperties が見えないため
    「自分のブロックだけを除外する」ことができない (自己衝突)。

    own_prefix: この接頭辞で始まる session_id のイベントだけを busy から外す。
    今まさに組み直している自分の計画は再配置できるので空きとして扱い、
    それ以外 (他ユーザーの学習ブロック等) は通常の予定と同じく尊重する。
    空文字なら SkillPath のイベントも含めてすべて busy とみなす。

    終日イベント (date のみ)、「予定なし」扱い (transparent)、辞退済みの
    予定は学習ウィンドウを塞がない。
    """
    busy: list[tuple[datetime, datetime]] = []
    for calendar_id in calendar_ids:
        for ev in _iter_events(calendar_id, time_min, time_max):
            session_id = _own_session_id(ev)
            if own_prefix and session_id.startswith(own_prefix):
                continue  # 再配置対象の自分のブロック
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue
            if _is_declined(ev):
                continue
            start_raw = (ev.get("start") or {}).get("dateTime")
            end_raw = (ev.get("end") or {}).get("dateTime")
            if not start_raw or not end_raw:
                continue  # 終日イベントは学習ウィンドウを塞がない扱い
            start = datetime.fromisoformat(start_raw).astimezone(_tz()).replace(tzinfo=None)
            end = datetime.fromisoformat(end_raw).astimezone(_tz()).replace(tzinfo=None)
            busy.append((start, end))
    return busy


def delete_orphan_events(
    own_prefix: str,
    keep_session_ids: set[str],
    time_min: datetime,
    time_max: datetime,
    calendar_ids: tuple[str, ...] = ("primary",),
) -> int:
    """再配置で不要になった自分の学習ブロックを削除し、削除件数を返す。

    own_prefix で始まる session_id のうち keep_session_ids に無いものが対象。
    誤って全消しするのを防ぐため、呼び出し側は keep_session_ids が空でないことを保証すること。
    """
    if not keep_session_ids:
        raise ValueError("keep_session_ids が空です (全削除の防止)")
    service = get_service()
    removed = 0
    for calendar_id in calendar_ids:
        for ev in list(_iter_events(calendar_id, time_min, time_max)):
            session_id = _own_session_id(ev)
            if not session_id.startswith(own_prefix) or session_id in keep_session_ids:
                continue
            service.events().delete(calendarId=calendar_id, eventId=ev["id"]).execute()
            removed += 1
    return removed


def upsert_event(
    session_id: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    calendar_id: str = "primary",
) -> str:
    """skillpath_session_id で既存イベントを検索し、あれば更新・なければ作成 (設計書 §5)。"""
    service = get_service()
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": _to_rfc3339(start)},
        "end": {"dateTime": _to_rfc3339(end)},
        "extendedProperties": {"private": {SESSION_ID_PROP: session_id}},
    }
    existing = (
        service.events()
        .list(
            calendarId=calendar_id,
            privateExtendedProperty=f"{SESSION_ID_PROP}={session_id}",
            maxResults=1,
            singleEvents=True,
        )
        .execute()
        .get("items", [])
    )
    if existing:
        event = (
            service.events()
            .update(calendarId=calendar_id, eventId=existing[0]["id"], body=body)
            .execute()
        )
    else:
        event = service.events().insert(calendarId=calendar_id, body=body).execute()
    return event["id"]
