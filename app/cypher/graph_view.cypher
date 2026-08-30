// Web UI のグラフ可視化用 (uid 分離を厳守: ユーザーの文脈に繋がるスキルのみ返す)

// -- name: user_skill_ids --
// ユーザーの習熟・模試・学習ブロックに繋がるスキル + その前提近傍 (3ホップ)
MATCH (u:User {uid: $uid})
OPTIONAL MATCH (u)-[:COMPLETED]->(a:Skill)
OPTIONAL MATCH (u)-[:TOOK]->(:Assessment)-[:ASSESSED]->(b:Skill)
OPTIONAL MATCH (u)-[:SCHEDULED]->(:LearningSession)-[:TARGETS]->(c:Skill)
WITH collect(DISTINCT a.id) + collect(DISTINCT b.id) + collect(DISTINCT c.id) AS seed_ids
MATCH (s:Skill) WHERE s.id IN seed_ids
OPTIONAL MATCH (s)-[:PREREQUISITE_OF*1..3]-(nb:Skill)
WITH collect(DISTINCT s.id) + collect(DISTINCT nb.id) AS ids
UNWIND ids AS id
WITH DISTINCT id WHERE id IS NOT NULL
RETURN collect(id) AS ids;

// -- name: graph_nodes --
MATCH (s:Skill) WHERE s.id IN $skill_ids
OPTIONAL MATCH (:User {uid: $uid})-[c:COMPLETED]->(s)
OPTIONAL MATCH (:User {uid: $uid})-[:TOOK]->(a:Assessment)-[r:ASSESSED]->(s)
WITH s, c, r, a ORDER BY a.taken_at DESC
WITH s, c, collect(r.score) AS scores
RETURN s.id                        AS id,
       s.name                      AS name,
       coalesce(s.domain, '')      AS domain,
       coalesce(s.estimated_hours, 1.0) AS estimated_hours,
       coalesce(c.mastery, -1.0)   AS mastery,
       CASE WHEN size(scores) > 0 THEN scores[0] ELSE null END AS latest_score;

// -- name: graph_edges --
MATCH (f:Skill)-[r:PREREQUISITE_OF]->(t:Skill)
WHERE f.id IN $skill_ids AND t.id IN $skill_ids
RETURN f.id AS source, t.id AS target, coalesce(r.strength, 1.0) AS strength;

// -- name: graph_certs --
// 資格ごとの表示情報。skill_ids は UI が資格でグラフを絞り込むために返す
MATCH (c:Certification)-[:REQUIRES]->(s:Skill)
WHERE s.id IN $skill_ids
OPTIONAL MATCH (u:User {uid: $uid})-[p:PURSUES]->(c)
WITH c, p, collect(DISTINCT s.id) AS cert_skill_ids
RETURN c.id                          AS id,
       c.name                        AS name,
       coalesce(c.vendor, '')        AS vendor,
       coalesce(c.calendar_url, '')  AS calendar_url,
       CASE WHEN p.deadline IS NULL THEN '' ELSE toString(p.deadline) END AS deadline,
       cert_skill_ids                AS skill_ids;

// -- name: graph_sessions --
// 登録済みの学習ブロック。UI をリロードしても時間割が残るように /graph から返す
MATCH (u:User {uid: $uid})-[:SCHEDULED]->(ls:LearningSession)-[:TARGETS]->(s:Skill)
OPTIONAL MATCH (u)-[:PURSUES]->(c:Certification)-[:REQUIRES]->(s)
WITH ls, s, collect(DISTINCT c.id) AS cert_ids
RETURN ls.id                        AS id,
       toString(ls.scheduled_at)    AS start,
       ls.duration_min              AS duration_min,
       coalesce(ls.kind, 'review')  AS kind,
       s.id                         AS skill_id,
       s.name                       AS skill_name,
       cert_ids
ORDER BY ls.scheduled_at;

// -- name: graph_assessments --
MATCH (:User {uid: $uid})-[:TOOK]->(a:Assessment)
OPTIONAL MATCH (a)-[r:ASSESSED]->(s:Skill)
WITH a, collect({skill_id: s.id, name: s.name, score: r.score}) AS per_skill
RETURN a.id AS id,
       toString(a.taken_at) AS taken_at,
       coalesce(a.source, '') AS source,
       a.total_score AS total_score,
       per_skill
ORDER BY a.taken_at ASC
LIMIT 20;
