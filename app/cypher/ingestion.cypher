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
