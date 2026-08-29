// -- name: weakness_cluster --
// 1. 閾値以下の直接的な弱点スキル
MATCH (a:Assessment {id: $assessment_id})-[r:ASSESSED]->(weak:Skill)
WHERE r.score < $threshold

// 2. その前提を最大4階層まで遡る
OPTIONAL MATCH path = (pre:Skill)-[:PREREQUISITE_OF*1..4]->(weak)

// 3. 前提のうち、まだ習熟していないものだけを対象にする
WITH weak, r.score AS weak_score, pre
OPTIONAL MATCH (u:User {uid: $uid})-[c:COMPLETED]->(pre)
WITH weak, weak_score, pre, coalesce(c.mastery, 0.0) AS pre_mastery
WHERE pre IS NULL OR pre_mastery < $threshold

RETURN weak.id           AS weak_skill_id,
       weak.name         AS weak_skill_name,
       weak_score        AS score,
       collect(DISTINCT {
         id: pre.id,
         name: pre.name,
         mastery: pre_mastery
       })                AS unmastered_prerequisites
ORDER BY weak_score ASC;
