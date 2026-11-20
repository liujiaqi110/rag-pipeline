"""Configuration for the RAGAS evaluation module.

The API key is never stored on the config object; only whether it is present.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = PACKAGE_DIR / "reports"
RAG_CONFIG_PATH = PROJECT_ROOT / "config" / "rag_config.yaml"
DEFAULT_QUESTIONS_PATH = PROJECT_ROOT / "eval" / "eval_questions.jsonl"
# Optional, human-written reference answers. Never auto-generated.
DEFAULT_REFERENCES_PATH = PROJECT_ROOT / "eval" / "reference_answers.jsonl"
RETRIEVAL_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "10_eval_retrieval.py"

API_KEY_ENV = "DEEPSEEK_API_KEY"

STRATEGIES = ("bm25", "dense", "hybrid")


@dataclass
class EvalConfig:
    # --- LLM judge / generator (DeepSeek, OpenAI-compatible) ---
    base_url: str = "https://api.deepseek.com"
    model_candidates: List[str] = field(
        default_factory=lambda: ["deepseek-flash", "deepseek-v4-pro"]
    )
    model: Optional[str] = None  # resolved at runtime against /models
    request_timeout_s: float = 60.0
    max_retries: int = 3  # OpenAI SDK exponential backoff on 429/5xx/timeouts
    concurrency: int = 2
    judge_temperature: float = 0.01
    judge_max_tokens: int = 2048
    instructor_mode: str = "json"  # json | md_json
    gen_temperature: float = 0.0
    gen_max_tokens: int = 2048  # deepseek-flash is a reasoning model; leave room for reasoning tokens
    max_api_calls: int = 80  # hard budget per run, enforced in api_client

    # --- Embeddings for RAGAS (reuse the project's dense model) ---
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"

    # --- Retrieval ---
    retrieval_top_k: int = 10  # same default as scripts/10_eval_retrieval.py
    context_k: int = 3  # how many retrieved chunks are passed to generation/RAGAS
    recall_ks: List[int] = field(default_factory=lambda: [3, 5])
    # Experimental hybrid_rrf strategy (opt-in via --strategies hybrid_rrf)
    rrf_k: int = 60
    rrf_bm25_pool: int = 25
    rrf_dense_pool: int = 25

    # --- Metrics ---
    answer_relevancy_strictness: int = 1
    low_score_threshold: float = 0.5
    regression_tolerance: float = 0.05

    # --- Data ---
    questions_path: Path = DEFAULT_QUESTIONS_PATH
    references_path: Path = DEFAULT_REFERENCES_PATH
    reports_dir: Path = REPORTS_DIR

    @property
    def api_key_present(self) -> bool:
        return bool(os.environ.get(API_KEY_ENV, "").strip())

    def to_public_dict(self) -> Dict:
        """Serializable view safe to write into reports (no secrets)."""
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Path):
                d[k] = _rel(v)
        d["api_key_env"] = API_KEY_ENV
        d["api_key_present"] = self.api_key_present
        return d


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def load_rag_config(path: Path = RAG_CONFIG_PATH) -> Dict:
    """Read the project's rag_config.yaml (read-only). Returns {} if unavailable."""
    try:
        import yaml
    except ImportError:
        return {}
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_config(**overrides) -> EvalConfig:
    """Build config: defaults <- rag_config.yaml (embedding/retrieval) <- env <- overrides."""
    cfg = EvalConfig()
    rag = load_rag_config()
    emb = rag.get("embedding", {}) or {}
    if emb.get("model_name"):
        cfg.embedding_model = emb["model_name"]
    if emb.get("device"):
        cfg.embedding_device = emb["device"]

    env_model = os.environ.get("RAGAS_EVAL_MODEL")
    if env_model:
        cfg.model_candidates = [m.strip() for m in env_model.split(",") if m.strip()]
    env_base = os.environ.get("DEEPSEEK_BASE_URL")
    if env_base:
        cfg.base_url = env_base

    for k, v in overrides.items():
        if v is None:
            continue
        if not hasattr(cfg, k):
            raise AttributeError(f"Unknown config key: {k}")
        setattr(cfg, k, v)
    return cfg
