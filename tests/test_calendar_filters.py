"""カレンダーの busy 判定フィルタ (API 接続不要な純粋部分)。"""

from datetime import datetime

import pytest

from app.tools.calendar_tool import (
    SESSION_ID_PROP,
    _is_declined,
    _own_session_id,
    delete_orphan_events,
)


def event(session_id: str = "", **extra) -> dict:
    ev: dict = {"summary": "x", **extra}
    if session_id:
        ev["extendedProperties"] = {"private": {SESSION_ID_PROP: session_id}}
    return ev


def test_own_session_id_reads_extended_property():
    sid = "demo-user-cert-a-initial-s1-b1"
    assert _own_session_id(event(sid)) == sid
    assert _own_session_id(event()) == ""  # 通常の予定は空文字


def test_own_prefix_distinguishes_users_blocks():
    """own_prefix は uid 単位。他ユーザーの学習ブロックは busy として尊重される。"""
    mine = _own_session_id(event("demo-user-cert-a-initial-s1-b1"))
    theirs = _own_session_id(event("other-user-cert-a-initial-s1-b1"))
    assert mine.startswith("demo-user-")
    assert not theirs.startswith("demo-user-")


def test_declined_events_do_not_block_study_time():
    declined = event(attendees=[{"self": True, "responseStatus": "declined"}])
    accepted = event(attendees=[{"self": True, "responseStatus": "accepted"}])
    other_declined = event(attendees=[{"email": "x@example.com", "responseStatus": "declined"}])
    assert _is_declined(declined)
    assert not _is_declined(accepted)
    assert not _is_declined(other_declined)  # 他人の辞退は関係ない
    assert not _is_declined(event())


def test_delete_orphans_refuses_empty_keep_set():
    """全消し事故の防止: 残す集合が空なら削除に進まない。"""
    with pytest.raises(ValueError, match="全削除の防止"):
        delete_orphan_events("demo-user-", set(), datetime(2026, 9, 1), datetime(2026, 9, 15))
