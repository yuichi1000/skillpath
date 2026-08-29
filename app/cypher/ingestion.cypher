// -- name: dedupe_skill --
// 既存スキルとの重複チェック（名前完全一致 or エイリアス一致）
MATCH (s:Skill)
WHERE toLower(s.name) = toLower($name)
   OR $name IN coalesce(s.aliases, [])
RETURN s.id AS id
LIMIT 1;

// -- name: merge_skills --
UNWIND $skills AS sk
MERGE (s:Skill {id: sk.id})
  ON CREATE SET s.name = sk.name,
                s.description = sk.description,
                s.estimated_hours = sk.estimated_hours,
                s.domain = sk.domain,
                s.created_at = datetime()
  ON MATCH  SET s.description = coalesce(sk.description, s.description),
                s.updated_at = datetime();

// -- name: merge_prerequisites --
UNWIND $prerequisites AS p
MATCH (from:Skill {id: p.from}), (to:Skill {id: p.to})
MERGE (from)-[r:PREREQUISITE_OF]->(to)
  ON CREATE SET r.strength = p.strength;

// -- name: merge_covers --
UNWIND $covers AS c
MATCH (r:Resource {id: c.resource_id}), (s:Skill {id: c.skill_id})
MERGE (r)-[rel:COVERS]->(s)
  ON CREATE SET rel.depth = c.depth, rel.section = c.section;

// -- name: merge_resources --
// 設計書 §4.2 の出力スキーマに resources があるが書き込みクエリが無かったため追加
UNWIND $resources AS rs
MERGE (r:Resource {id: rs.id})
  ON CREATE SET r.title = rs.title,
                r.type = rs.type,
                r.url = rs.url,
                r.pages = rs.pages,
                r.estimated_hours = rs.estimated_hours,
                r.created_at = datetime()
  ON MATCH  SET r.estimated_hours = coalesce(rs.estimated_hours, r.estimated_hours);

// -- name: unmastered_targets --
// 登録したスキルのうち、ユーザーが未習熟のものだけを初期計画の対象にする
MATCH (s:Skill) WHERE s.id IN $skill_ids
OPTIONAL MATCH (u:User {uid: $uid})-[c:COMPLETED]->(s)
WITH s, coalesce(c.mastery, 0.0) AS mastery
WHERE mastery < $threshold
RETURN collect(s.id) AS ids;
