"""Ingestion errors carry a stable, user-safe ``code``; the message never contains file content or local paths."""


class IngestError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(code)
        self.code, self.message, self.status = code, message, status


class EmbeddingUnavailable(IngestError):
    def __init__(self, message: str = "向量模型不可用"):
        super().__init__("embedding_model_unavailable", message, 503)
