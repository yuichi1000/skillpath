// 制約とインデックス (設計書 §3.3)
CREATE CONSTRAINT skill_id IF NOT EXISTS
  FOR (s:Skill) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT resource_id IF NOT EXISTS
  FOR (r:Resource) REQUIRE r.id IS UNIQUE;
CREATE CONSTRAINT user_uid IF NOT EXISTS
  FOR (u:User) REQUIRE u.uid IS UNIQUE;
CREATE CONSTRAINT cert_id IF NOT EXISTS
  FOR (c:Certification) REQUIRE c.id IS UNIQUE;
CREATE INDEX skill_name IF NOT EXISTS FOR (s:Skill) ON (s.name);
// 名寄せの索引。表記ゆれを吸収した正規化キーで引く (entity.match_key)
CREATE INDEX skill_match_key IF NOT EXISTS FOR (s:Skill) ON (s.match_key);
CREATE INDEX skill_domain IF NOT EXISTS FOR (s:Skill) ON (s.domain);
