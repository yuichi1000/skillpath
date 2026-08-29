// -- name: merge_assessment --
MERGE (a:Assessment {id: $assessment_id})
  SET a.taken_at = datetime($taken_at),
      a.source = $source,
      a.total_score = $total_score
WITH a
MATCH (u:User {uid: $uid})
MERGE (u)-[:TOOK]->(a);

// -- name: merge_assessed --
UNWIND $per_skill AS ps
MATCH (a:Assessment {id: $assessment_id})
MATCH (s:Skill {id: ps.skill_id})
MERGE (a)-[r:ASSESSED]->(s)
  SET r.score = ps.score, r.correct = ps.correct, r.total = ps.total;

// -- name: update_mastery --
// 習熟度を指数移動平均で更新（過去の結果を減衰させる）
UNWIND $per_skill AS ps
MATCH (u:User {uid: $uid}), (s:Skill {id: ps.skill_id})
MERGE (u)-[c:COMPLETED]->(s)
  ON CREATE SET c.mastery = ps.score, c.updated_at = datetime()
  ON MATCH  SET c.mastery = 0.4 * c.mastery + 0.6 * ps.score,
                c.updated_at = datetime();
