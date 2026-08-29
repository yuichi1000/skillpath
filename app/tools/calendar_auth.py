"""Google Calendar の一度きりの認可フロー (設計書 §6: トークンは Secret Manager 保管)。

前提: GCP コンソールで OAuth クライアント (デスクトップアプリ) を作成し、
クライアントシークレット JSON をプロジェクト直下に client_secret.json として置く。

  uv run python -m app.tools.calendar_auth

ブラウザで許可すると calendar-token.json が生成される。表示される
gcloud コマンドで Secret Manager に登録すれば Cloud Run からも使える。
"""

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_SECRET = Path("client_secret.json")
TOKEN_PATH = Path("calendar-token.json")


def main() -> None:
    if not CLIENT_SECRET.exists():
        sys.exit(
            "client_secret.json が見つかりません。GCP コンソールでダウンロードした"
            " OAuth クライアントの JSON をプロジェクト直下に置いてください"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nトークンを {TOKEN_PATH} に保存しました。")
    print("Cloud Run から使うには Secret Manager に登録してください:\n")
    print("  gcloud secrets create skillpath-calendar-token --data-file=calendar-token.json \\")
    print("    --project=skillpath-design 2>/dev/null || \\")
    print(
        "  gcloud secrets versions add skillpath-calendar-token"
        " --data-file=calendar-token.json \\"
    )
    print("    --project=skillpath-design")


if __name__ == "__main__":
    main()
