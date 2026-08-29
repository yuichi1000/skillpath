"""Cloud Storage 読み書き（模試 PDF / 画像などのアップロードファイル）。"""


def read_upload(gcs_path: str) -> bytes:
    """gs://<bucket>/<path> のオブジェクトを読む。非公開バケット前提 (設計書 §6)。"""
    raise NotImplementedError("TODO: google-cloud-storage で実装")
