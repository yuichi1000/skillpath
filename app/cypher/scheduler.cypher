// -- name: create_sessions --
// 設計書 §4.2 から1点変更: CREATE → MERGE (再実行で LearningSession が重複しないように。§5)
UNWIND $sessions AS sess
MATCH (u:User {uid: $uid}), (s:Skill {id: sess.skill_id})
MERGE (ls:LearningSession {id: sess.id})
  SET ls.scheduled_at = datetime(sess.start),
      ls.duration_min = sess.duration_min,
      ls.calendar_event_id = sess.event_id,
      ls.status = 'scheduled',
      ls.kind = sess.kind
MERGE (u)-[:SCHEDULED]->(ls)
MERGE (ls)-[:TARGETS]->(s)
WITH ls, sess
MATCH (r:Resource {id: sess.resource_id})
MERGE (ls)-[us:USES]->(r)
  SET us.from_page = sess.from_page, us.to_page = sess.to_page;
