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

// -- name: user_pursuits --
// ユーザーが目指している全資格 (複数資格の一括再配置に使う)
MATCH (u:User {uid: $uid})-[p:PURSUES]->(c:Certification)
RETURN c.id AS cert_id,
       c.name AS name,
       CASE WHEN p.deadline   IS NULL THEN '' ELSE toString(p.deadline)   END AS deadline,
       CASE WHEN p.start_date IS NULL THEN '' ELSE toString(p.start_date) END AS start_date,
       coalesce(c.calendar_id, '') AS calendar_id;

// -- name: cert_unmastered_targets --
// その資格が要求するスキルのうち、まだ習熟していないもの
MATCH (c:Certification {id: $cert_id})-[:REQUIRES]->(s:Skill)
OPTIONAL MATCH (u:User {uid: $uid})-[co:COMPLETED]->(s)
WITH s, coalesce(co.mastery, 0.0) AS mastery
WHERE mastery < $threshold
RETURN collect(s.id) AS ids;

// -- name: skill_certs --
// スキル → そのユーザーが目指す資格 (復習ブロックをどの資格の暦に置くか決める)
MATCH (u:User {uid: $uid})-[:PURSUES]->(c:Certification)-[:REQUIRES]->(s:Skill)
WHERE s.id IN $skill_ids
RETURN s.id AS skill_id, collect(c.id) AS cert_ids;

// -- name: delete_orphan_sessions --
// 再配置で消えた学習ブロックを取り除く (keep_ids が空のときは呼ばないこと)
MATCH (u:User {uid: $uid})-[:SCHEDULED]->(ls:LearningSession)
WHERE NOT ls.id IN $keep_ids
DETACH DELETE ls;

// -- name: set_cert_calendar --
// 資格専用カレンダーの ID と公開用 URL を記録する (作り直しを防ぐ)
MATCH (c:Certification {id: $cert_id})
  SET c.calendar_id = $calendar_id, c.calendar_url = $calendar_url;

// -- name: cert_calendar --
MATCH (c:Certification {id: $cert_id})
RETURN c.id                          AS cert_id,
       c.name                        AS name,
       coalesce(c.vendor, '')        AS vendor,
       coalesce(c.calendar_id, '')   AS calendar_id,
       coalesce(c.calendar_url, '')  AS calendar_url;
