// -- name: dependency_subgraph --
// 設計書 §4.2 から2点変更:
//   1. 循環解消に strength が必要なため、前提を {id, strength} のマップで返す
//   2. 直接エッジではなく可変長パス (*1..4) で辿る。対象集合外のスキル (習熟済みで
//      クラスタから除外されたもの) を経由する推移的な依存が消えると、前提が後ろに
//      並ぶ順序バグが起きるため。strength はパス上の最小値
MATCH (s:Skill)
WHERE s.id IN $target_skill_ids
OPTIONAL MATCH path = (pre:Skill)-[:PREREQUISITE_OF*1..4]->(s)
WHERE pre.id IN $target_skill_ids AND pre.id <> s.id
WITH s, collect(CASE WHEN pre IS NOT NULL
                THEN {id: pre.id,
                      strength: reduce(m = 1.0, r IN relationships(path) |
                                CASE WHEN coalesce(r.strength, 1.0) < m
                                     THEN coalesce(r.strength, 1.0) ELSE m END)} END) AS deps
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
