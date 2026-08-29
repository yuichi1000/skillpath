"""Notifier / Report Node — 結果サマリの生成 (設計書 §4.2)。

作成イベント数・スケジュール・警告を含む。期限充足状況は deadline 対応時に追加する。
"""

from app.models.schemas import PlannerOutput, WeaknessOutput
from app.workflow.scheduler import SessionDraft

_WEEKDAYS_JP = "月火水木金土日"


def build_summary(
    weakness: WeaknessOutput,
    plan: PlannerOutput,
    sessions: list[SessionDraft] | None = None,
    schedule_warnings: list[str] | None = None,
) -> str:
    # plan/sessions は skill_id しか持たないため、weakness 側の情報から表示名を引く
    names = {w.weak_skill_id: w.weak_skill_name for w in weakness.weak_skills}
    for w in weakness.weak_skills:
        names.update({p.id: p.name for p in w.unmastered_prerequisites})

    lines = ["弱点クラスタを検出し、復習計画を作成しました。", ""]
    for w in weakness.weak_skills:
        pres = ", ".join(p.name for p in w.unmastered_prerequisites) or "なし"
        lines.append(f"- {w.weak_skill_name} (スコア {w.score:.0%}) / 未習熟の前提: {pres}")
    lines.append("")
    lines.append(f"学習順序 ({len(plan.plan)} 件):")
    for item in plan.plan:
        lines.append(f"  {item.order}. {names.get(item.skill_id, item.skill_id)} ({item.estimated_minutes}分)")
    for w in plan.warnings:
        lines.append(f"⚠ {w}")

    if sessions:
        lines.append("")
        lines.append(f"📅 学習スケジュール ({len(sessions)} ブロック):")
        for s in sessions:
            wd = _WEEKDAYS_JP[s.start.weekday()]
            lines.append(
                f"  {s.start:%m/%d}({wd}) {s.start:%H:%M}-{s.end:%H:%M}"
                f" {names.get(s.skill_id, s.skill_id)}"
            )
    for w in schedule_warnings or []:
        lines.append(f"⚠ {w}")
    return "\n".join(lines)


def build_no_weakness_report(weakness: WeaknessOutput) -> str:
    return "今回の模試に閾値未満の弱点スキルはありませんでした。現在の計画を継続してください。"
