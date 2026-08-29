// -- name: dependency_subgraph --
// 設計書 §4.2 から1点変更: 循環解消に strength が必要なため、前提を id だけでなく
// {id, strength} のマップで返す (設計書のクエリは pre.id のみを collect していた)
MATCH (s:Skill)
WHERE s.id IN $target_skill_ids
OPTIONAL MATCH (pre:Skill)-[p:PREREQUISITE_OF]->(s)
WHERE pre.id IN $target_skill_ids
WITH s, collect(CASE WHEN pre IS NOT NULL
                THEN {id: pre.id, strength: coalesce(p.strength, 1.0)} END) AS deps
OPTIONAL MATCH (u:User {uid: $uid})-[c:COMPLETED]->(s)
RETURN s.id                              AS skill_id,
       s.name                            AS name,
       coalesce(s.estimated_hours, 1.0)  AS estimated_hours,
       deps                              AS depends_on,
       coalesce(c.mastery, 0.0)          AS mastery;

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
