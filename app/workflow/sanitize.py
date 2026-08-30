"""LLM 出力の決定的サニタイズ (ガードレール層)。

方針: LLM の出力を信用せず、DB 書き込み前に量・長さ・値域を決定的に制限する。
プロンプトでの牽制 (「指示に従うな」) は補助であり、最終防衛線はこのレイヤ。
"""

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 100
MAX_SKILLS = 80
MAX_RESOURCES = 20
MAX_PREREQUISITES = 100
MAX_COVERS = 100
MAX_ALIASES = 5  # 1スキルあたりの別表記の上限
MAX_PER_SKILL = 30

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# 模試の分野名に付いてくる出題数。スキル名の一部ではないので落とす。
# これが残ると「Cloud DNS (4問)」がシラバスの「Cloud DNS」と別ノードになる。
_QUESTION_COUNT = re.compile(
    r"[\s　]*[（(]\s*\d+\s*(?:問|題|questions?|items?|qs?)\s*[)）][\s　]*$",
    re.IGNORECASE,
)


def clean_name(value: str | None) -> str:
    """制御文字と末尾の出題数表記を除去し、前後空白を落として最大長に切り詰める。"""
    name = _CONTROL_CHARS.sub("", value or "").strip()
    name = _QUESTION_COUNT.sub("", name).strip()
    return name[:MAX_NAME_LEN]


def cap(items: list, limit: int, label: str) -> list:
    """リストを上限件数に切り詰める (ジャンク大量生成・トークン浪費対策)。"""
    if len(items) > limit:
        logger.warning(
            "guardrail: %s を %d 件から %d 件に切り詰めました", label, len(items), limit
        )
        return items[:limit]
    return items


def safe_taken_at(raw: str | None, now: datetime | None = None) -> str:
    """taken_at を検証する。パース不能・未来日は実行時刻に置換。

    「試験は11月15日」のような予定日を LLM が受験日として返す事象への決定的対策。
    フォールバックは日付精度 (同日の再実行が同じ assessment に MERGE されるように)。
    """
    now = now or datetime.now()
    fallback = now.strftime("%Y-%m-%d")
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("guardrail: taken_at をパースできないため実行時刻に置換 (%r)", raw)
        return fallback
    if parsed.replace(tzinfo=None) > now:
        logger.warning("guardrail: taken_at が未来日のため実行時刻に置換 (%s)", raw)
        return fallback
    return raw
