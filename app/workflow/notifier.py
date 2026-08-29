"""Notifier / Report Node — 結果サマリの生成 (設計書 §4.2)。

現状はテキストサマリのみ。Scheduler 実装後にイベント数・期限充足状況を追加する。
"""

from app.models.schemas import PlannerOutput, WeaknessOutput


def build_summary(weakness: WeaknessOutput, plan: PlannerOutput) -> str:
    lines = ["弱点クラスタを検出し、復習計画を作成しました。", ""]
    for w in weakness.weak_skills:
        pres = ", ".join(p.name for p in w.unmastered_prerequisites) or "なし"
        lines.append(f"- {w.weak_skill_name} (スコア {w.score:.0%}) / 未習熟の前提: {pres}")
    lines.append("")
    lines.append(f"学習順序 ({len(plan.plan)} 件):")
    for item in plan.plan:
        lines.append(f"  {item.order}. {item.skill_id} ({item.estimated_minutes}分)")
    for w in plan.warnings:
        lines.append(f"⚠ {w}")
    return "\n".join(lines)


def build_no_weakness_report(weakness: WeaknessOutput) -> str:
    return "今回の模試に閾値未満の弱点スキルはありませんでした。現在の計画を継続してください。"
