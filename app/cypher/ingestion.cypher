// -- name: dedupe_skill --
// 名寄せ第1段: 正規化キー (空白・記号を落とした表記) の完全一致
MATCH (s:Skill)
WHERE s.match_key = $key
   OR $key IN coalesce(s.alias_keys, [])
   OR (s.match_key IS NULL AND toLower(s.name) = $name)  // 索引が無い旧ノードへの保険
RETURN s.id AS id
ORDER BY s.created_at ASC, s.id ASC  // 同じキーが複数あっても常に同じノードへ寄せる
LIMIT 1;

// -- name: fuzzy_skill --
// 名寄せ第2段: 包含関係かつ長さ比 0.6 以上。短い側が長い側に埋もれる誤爆
// (「VPC」が「VPC設計IP管理」に一致する類) を長さ比で弾く
MATCH (s:Skill)
WHERE s.match_key IS NOT NULL
  AND size(s.match_key) >= 4 AND size($key) >= 4
  AND (s.match_key CONTAINS $key OR $key CONTAINS s.match_key)
WITH s,
     toFloat(CASE WHEN size($key) < size(s.match_key) THEN size($key) ELSE size(s.match_key) END)
     / CASE WHEN size($key) > size(s.match_key) THEN size($key) ELSE size(s.match_key) END AS ratio
WHERE ratio >= 0.6
RETURN s.id AS id, ratio
ORDER BY ratio DESC
LIMIT 1;

// -- name: merge_skills --
// match_key / alias_keys は名寄せの索引。既存ノードにも必ず入れ直す
// (これが欠けると、次に来た表記ゆれが別ノードとして増えてしまう)
UNWIND $skills AS sk
MERGE (s:Skill {id: sk.id})
  ON CREATE SET s.name = sk.name,
                s.description = sk.description,
                s.estimated_hours = sk.estimated_hours,
                s.domain = sk.domain,
                s.created_at = datetime()
  ON MATCH  SET s.description = coalesce(sk.description, s.description),
                s.estimated_hours = coalesce(s.estimated_hours, sk.estimated_hours),
                s.updated_at = datetime()
  SET s.match_key = sk.match_key,
      s.alias_keys = sk.alias_keys;

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

// -- name: merge_certification --
// 資格スペシャリストが特定した Certification ノードと REQUIRES エッジ (設計書 §3.1)
MERGE (c:Certification {id: $cert_id})
  SET c.name = $name, c.vendor = $vendor
WITH c
UNWIND $skill_ids AS sid
MATCH (s:Skill {id: sid})
MERGE (c)-[r:REQUIRES]->(s)
  ON CREATE SET r.weight = 1.0;

// -- name: ensure_user --
MERGE (u:User {uid: $uid});

// -- name: create_skill_if_absent --
// 名寄せでヒットしなかったスキル名の新規作成 (entity.resolve_skill_id が使用)
MERGE (s:Skill {id: $id})
  ON CREATE SET s.name = $name, s.created_at = datetime()
  SET s.match_key = $key, s.alias_keys = $alias_keys;

// -- name: merge_pursues --
// ユーザーがその資格を目指していること + 試験日・開始日を記録する。
// 試験日は受験者ごとに違うため Certification ではなくユーザー側のエッジに持たせる。
// 空文字が来たときは既存値を消さない (毎回の実行で上書き消去しないため)。
MATCH (u:User {uid: $uid}), (c:Certification {id: $cert_id})
MERGE (u)-[p:PURSUES]->(c)
  SET p.deadline   = CASE WHEN $deadline   = '' THEN p.deadline   ELSE date($deadline)   END,
      p.start_date = CASE WHEN $start_date = '' THEN p.start_date ELSE date($start_date) END,
      p.updated_at = datetime();
