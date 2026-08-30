// デモ用シードデータ (G検定風)。すべて MERGE で冪等 — make seed を何度実行しても増殖しない。
// id は 'demo-' プレフィックス。削除は app/tools/seed_demo.py の --clean を参照。

MERGE (u:User {uid: 'demo-user'})
  SET u.timezone = 'Asia/Tokyo', u.weekly_capacity_hours = 8;

UNWIND [
  {id: 'demo-math',      name: '数学基礎(線形代数・微分)', domain: 'foundation', hours: 10},
  {id: 'demo-stats',     name: '確率・統計',               domain: 'foundation', hours: 8},
  {id: 'demo-ml-basics', name: '機械学習基礎',             domain: 'ml',         hours: 12},
  {id: 'demo-nn',        name: 'ニューラルネットワーク基礎', domain: 'dl',        hours: 8},
  {id: 'demo-dl',        name: 'ディープラーニング手法',    domain: 'dl',         hours: 10},
  {id: 'demo-cnn',       name: 'CNN(画像認識)',            domain: 'dl',         hours: 6},
  {id: 'demo-rnn',       name: 'RNN・系列モデル',          domain: 'dl',         hours: 6},
  {id: 'demo-nlp',       name: '自然言語処理',             domain: 'dl',         hours: 8},
  {id: 'demo-llm',       name: '生成AI・大規模言語モデル',  domain: 'dl',         hours: 8},
  {id: 'demo-ethics',    name: 'AI倫理・法律',             domain: 'governance', hours: 4}
] AS sk
MERGE (s:Skill {id: sk.id})
  SET s.name = sk.name, s.domain = sk.domain, s.estimated_hours = sk.hours;

UNWIND [
  {from: 'demo-math',      to: 'demo-stats',     strength: 0.8},
  {from: 'demo-math',      to: 'demo-ml-basics', strength: 0.7},
  {from: 'demo-stats',     to: 'demo-ml-basics', strength: 0.9},
  {from: 'demo-ml-basics', to: 'demo-nn',        strength: 0.9},
  {from: 'demo-nn',        to: 'demo-dl',        strength: 0.9},
  {from: 'demo-dl',        to: 'demo-cnn',       strength: 0.8},
  {from: 'demo-dl',        to: 'demo-rnn',       strength: 0.8},
  {from: 'demo-rnn',       to: 'demo-nlp',       strength: 0.7},
  {from: 'demo-dl',        to: 'demo-llm',       strength: 0.6},
  {from: 'demo-nlp',       to: 'demo-llm',       strength: 0.8}
] AS p
MATCH (a:Skill {id: p.from}), (b:Skill {id: p.to})
MERGE (a)-[r:PREREQUISITE_OF]->(b)
  SET r.strength = p.strength;

MERGE (c:Certification {id: 'demo-cert-gkentei'})
  SET c.name = 'G検定(デモ)', c.vendor = 'JDLA';
WITH 1 AS _
UNWIND ['demo-ml-basics','demo-dl','demo-cnn','demo-rnn','demo-nlp','demo-llm','demo-ethics'] AS sid
MATCH (c:Certification {id: 'demo-cert-gkentei'}), (s:Skill {id: sid})
MERGE (c)-[r:REQUIRES]->(s)
  SET r.weight = 1.0;

// 受験予定 (試験日は受験者ごとに違うのでユーザー側のエッジに持たせる)
MATCH (u:User {uid: 'demo-user'}), (c:Certification {id: 'demo-cert-gkentei'})
MERGE (u)-[p:PURSUES]->(c)
  SET p.deadline = date('2026-11-15');

UNWIND [
  {id: 'demo-book-zero', title: 'ゼロから作るDeep Learning(デモ)', type: 'book', hours: 30},
  {id: 'demo-book-text', title: 'G検定公式テキスト(デモ)',          type: 'book', hours: 25}
] AS bk
MERGE (r:Resource {id: bk.id})
  SET r.title = bk.title, r.type = bk.type, r.estimated_hours = bk.hours;

UNWIND [
  {rid: 'demo-book-zero', sid: 'demo-nn',        depth: 'deep',     section: '3-5章'},
  {rid: 'demo-book-zero', sid: 'demo-dl',        depth: 'standard', section: '6-7章'},
  {rid: 'demo-book-zero', sid: 'demo-cnn',       depth: 'standard', section: '7-8章'},
  {rid: 'demo-book-text', sid: 'demo-ml-basics', depth: 'intro',    section: '2章'},
  {rid: 'demo-book-text', sid: 'demo-dl',        depth: 'intro',    section: '3章'},
  {rid: 'demo-book-text', sid: 'demo-cnn',       depth: 'intro',    section: '4章'},
  {rid: 'demo-book-text', sid: 'demo-rnn',       depth: 'intro',    section: '4章'},
  {rid: 'demo-book-text', sid: 'demo-nlp',       depth: 'intro',    section: '5章'},
  {rid: 'demo-book-text', sid: 'demo-llm',       depth: 'intro',    section: '6章'},
  {rid: 'demo-book-text', sid: 'demo-ethics',    depth: 'standard', section: '7章'}
] AS cv
MATCH (r:Resource {id: cv.rid}), (s:Skill {id: cv.sid})
MERGE (r)-[rel:COVERS]->(s)
  SET rel.depth = cv.depth, rel.section = cv.section;

UNWIND [
  {sid: 'demo-math',      mastery: 0.9},
  {sid: 'demo-stats',     mastery: 0.75},
  {sid: 'demo-ml-basics', mastery: 0.7},
  {sid: 'demo-nn',        mastery: 0.5},
  {sid: 'demo-ethics',    mastery: 0.8}
] AS m
MATCH (u:User {uid: 'demo-user'}), (s:Skill {id: m.sid})
MERGE (u)-[c:COMPLETED]->(s)
  SET c.mastery = m.mastery, c.updated_at = datetime();

MERGE (a:Assessment {id: 'demo-assess-1'})
  SET a.taken_at = datetime('2026-08-25T10:00:00+09:00'),
      a.source = 'G検定 模試(デモ)', a.total_score = 0.58;
WITH 1 AS _
MATCH (u:User {uid: 'demo-user'}), (a:Assessment {id: 'demo-assess-1'})
MERGE (u)-[:TOOK]->(a);

UNWIND [
  {sid: 'demo-ml-basics', score: 0.7,  correct: 7, total: 10},
  {sid: 'demo-dl',        score: 0.55, correct: 11, total: 20},
  {sid: 'demo-cnn',       score: 0.35, correct: 7,  total: 20},
  {sid: 'demo-nlp',       score: 0.45, correct: 9,  total: 20},
  {sid: 'demo-ethics',    score: 0.9,  correct: 9,  total: 10}
] AS ps
MATCH (a:Assessment {id: 'demo-assess-1'}), (s:Skill {id: ps.sid})
MERGE (a)-[r:ASSESSED]->(s)
  SET r.score = ps.score, r.correct = ps.correct, r.total = ps.total;
