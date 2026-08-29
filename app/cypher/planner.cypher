// -- name: dependency_subgraph --
// 対象スキル群の依存部分グラフを取得
MATCH (s:Skill)
WHERE s.id IN $target_skill_ids
OPTIONAL MATCH (s)<-[:PREREQUISITE_OF]-(pre:Skill)
WHERE pre.id IN $target_skill_ids
WITH s, collect(pre.id) AS deps
OPTIONAL MATCH (u:User {uid: $uid})-[c:COMPLETED]->(s)
RETURN s.id              AS skill_id,
       s.name            AS name,
       s.estimated_hours AS estimated_hours,
       deps              AS depends_on,
       coalesce(c.mastery, 0.0) AS mastery;

// -- name: resource_candidates --
MATCH (r:Resource)-[c:COVERS]->(s:Skill)
WHERE s.id IN $target_skill_ids
RETURN s.id AS skill_id,
       collect({
         resource_id: r.id,
         title: r.title,
         type: r.type,
         depth: c.depth,
         section: c.section,
         estimated_hours: r.estimated_hours
       }) AS candidates;
