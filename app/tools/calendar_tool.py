"""Google Calendar API ラッパ (設計書 §4.2 Scheduler)。

冪等性 (設計書 §5): イベントには extendedProperties.private.skillpath_session_id
を付与し、upsert_event は同 ID の既存イベントを検索して更新 or 作成する。

認証情報の解決順: ローカルの calendar-token.json → Secret Manager
(skillpath-calendar-token)。どちらも無ければ CalendarUnavailable を投げ、
呼び出し側 (scheduler_node) はプレースホルダにフォールバックする。
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import get_settings

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


def get_busy(
    time_min: datetime, time_max: datetime, calendar_id: str = "primary"
) -> list[tuple[datetime, datetime]]:
    """busy 区間をローカル時刻の naive datetime で返す。

    FreeBusy API ではなく events.list を使う: FreeBusy は SkillPath 自身が作った
    学習ブロックも busy として返すため、再計画時に自分のイベントで空き枠が
    埋まってしまう (自己衝突)。skillpath_session_id 付きイベントは除外する。
    終日イベント (date のみ) と「予定なし」扱い (transparent) も busy にしない。
    """
    service = get_service()
    busy: list[tuple[datetime, datetime]] = []
    page_token = None
    while True:
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
        for ev in resp.get("items", []):
            props = (ev.get("extendedProperties") or {}).get("private") or {}
            if SESSION_ID_PROP in props:
                continue  # 自分のイベントは再配置可能なので busy にしない
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue
            start_raw = (ev.get("start") or {}).get("dateTime")
            end_raw = (ev.get("end") or {}).get("dateTime")
            if not start_raw or not end_raw:
                continue  # 終日イベントは学習ウィンドウを塞がない扱い
            start = datetime.fromisoformat(start_raw).astimezone(_tz()).replace(tzinfo=None)
            end = datetime.fromisoformat(end_raw).astimezone(_tz()).replace(tzinfo=None)
            busy.append((start, end))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return busy


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
