#!/bin/bash
set -euo pipefail
exec > /var/log/neo4j-startup.log 2>&1

# 冪等ガード: 設定済みなら neo4j 起動だけ確認して終了
if [ -f /etc/neo4j/.skillpath-initialized ]; then
  systemctl start neo4j || true
  exit 0
fi

# データディスクのマウント
if ! blkid /dev/disk/by-id/google-neo4j-data; then
  mkfs.ext4 -F /dev/disk/by-id/google-neo4j-data
fi
mkdir -p /var/lib/neo4j-data
mount -o discard,defaults /dev/disk/by-id/google-neo4j-data /var/lib/neo4j-data || true
grep -q neo4j-data /etc/fstab || \
  echo '/dev/disk/by-id/google-neo4j-data /var/lib/neo4j-data ext4 discard,defaults,nofail 0 2' >> /etc/fstab

# Neo4j 5 LTS をインストール (最新 calver は Java 21+ 要求で Debian 12 に入らないため
# stable 5 チャンネルを使う。5.26 LTS は Java 17 = Debian 12 標準で動く)
apt-get update
apt-get install -y wget gnupg
wget -qO- https://debian.neo4j.com/neotechnology.gpg.key | gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
echo 'deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable 5' > /etc/apt/sources.list.d/neo4j.list
apt-get update
apt-get install -y neo4j

# deb は自動起動することがあるため必ず止める (初期パスワードは初回起動前にしか設定できない)
systemctl stop neo4j || true

# データディレクトリを永続ディスクへ / VPC 内からの接続を許可
sed -i 's|^#server.directories.data=.*|server.directories.data=/var/lib/neo4j-data|' /etc/neo4j/neo4j.conf
sed -i 's|^#server.default_listen_address=.*|server.default_listen_address=0.0.0.0|' /etc/neo4j/neo4j.conf
chown -R neo4j:neo4j /var/lib/neo4j-data

# 初期パスワード設定はデータディレクトリ確定後に行う (認証情報は data 配下に書かれるため。
# 設計書の元スクリプトはこの順序が逆で、設定したパスワードが使われないバグがあった)
PASSWORD=$(gcloud secrets versions access latest \
  --secret="${password_secret_id}" --project="${project_id}")
neo4j-admin dbms set-initial-password "$PASSWORD"
chown -R neo4j:neo4j /var/lib/neo4j-data

systemctl enable neo4j
systemctl start neo4j
touch /etc/neo4j/.skillpath-initialized
