"""Web 検索・URL 取得（公式シラバス取得用、Ingestion Agent が使用）。"""


def web_search(query: str) -> list[dict]:
    raise NotImplementedError("TODO: 検索 API (Vertex AI Search grounding など)")


def fetch_url(url: str) -> str:
    raise NotImplementedError("TODO: httpx で取得しテキスト抽出")
