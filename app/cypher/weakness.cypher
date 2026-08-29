// -- name: weakness_cluster --
// 設計書 §4.2 から2点変更:
//   1. uid 分離のため TOOK 経由で本人の Assessment に限定 (§6 の不変条件)
//   2. 未習熟前提の絞り込みを行レベル WHERE から collect 内の CASE に変更。
//      設計書のクエリは前提が全て習熟済みの場合に弱点スキル自体が消えるバグがあった

// 1. 本人の模試に紐づく、閾値未満の直接的な弱点スキル
MATCH (u:User {uid: $uid})-[:TOOK]->(a:Assessment {id: $assessment_id})
MATCH (a)-[r:ASSESSED]->(weak:Skill)
WHERE r.score < $threshold

// 2. その前提を最大4階層まで遡る
OPTIONAL MATCH (pre:Skill)-[:PREREQUISITE_OF*1..4]->(weak)

// 3. 前提の習熟度を取得し、未習熟のものだけを集める
//    (collect は null を無視するため、CASE の条件を満たさない前提は自然に落ちる)
WITH u, weak, r.score AS weak_score, pre
OPTIONAL MATCH (u)-[c:COMPLETED]->(pre)
WITH weak, weak_score, pre, coalesce(c.mastery, 0.0) AS pre_mastery
WITH weak, weak_score,
     collect(DISTINCT CASE WHEN pre IS NOT NULL AND pre_mastery < $threshold
             THEN {id: pre.id, name: pre.name, mastery: pre_mastery} END) AS unmastered

RETURN weak.id    AS weak_skill_id,
       weak.name  AS weak_skill_name,
       weak_score AS score,
       unmastered AS unmastered_prerequisites
ORDER BY weak_score ASC;
