// -- name: create_sessions --
UNWIND $sessions AS sess
MATCH (u:User {uid: $uid}), (s:Skill {id: sess.skill_id})
CREATE (ls:LearningSession {
  id: sess.id,
  scheduled_at: datetime(sess.start),
  duration_min: sess.duration_min,
  calendar_event_id: sess.event_id,
  status: 'scheduled',
  kind: sess.kind   // 'initial' | 'review'
})
MERGE (u)-[:SCHEDULED]->(ls)
MERGE (ls)-[:TARGETS]->(s)
WITH ls, sess
MATCH (r:Resource {id: sess.resource_id})
MERGE (ls)-[us:USES]->(r)
  SET us.from_page = sess.from_page, us.to_page = sess.to_page;
