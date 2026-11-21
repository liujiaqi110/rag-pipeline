"""Ingestion orchestration: storage, per-file processing, index generations, consistency rules.

Consistency model (the database is the source of truth):

1. Upload → a ``DocumentVersion`` (``uploaded``); the file is written under ``KB_STORAGE_DIR/uploads/<doc>/<n>/``
   with a server-chosen name (the client filename is metadata only, never a path).
2. Processing → parse → clean → chunk (scripts 03/04/05) → ``Chunk`` rows → version ``chunked`` (or ``failed`` with a
   stable error code; nothing else changes).
3. Index build → a NEW generation directory is written from the database: for every ``active`` uploaded document its
   newest ``chunked``/``index_failed`` version if any, else its current ``indexed`` version. It is loaded back and
   checked, and only then, in ONE transaction: the generation becomes ``active``, those versions become ``indexed``
   and current, older versions ``superseded``, deleted documents' chunks/files purged. Any failure before that commit
   leaves the previous generation and every current version untouched (candidates → ``index_failed``, retryable via
   ``POST /api/v1/index/rebuild``).
4. Disabled / deleted / archived documents are excluded from the ACL policy immediately (no access even before the
   rebuild), and from the next generation.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..acl_store import bump_acl_version
from ..models import Chunk, Document, DocumentVersion, IndexGeneration, KnowledgeBase, utcnow
from ..settings import PROJECT_ROOT, Settings
from . import chunker, parsers
from .errors import EmbeddingUnavailable, IngestError
from .index_store import PlatformRetriever, atomic_write, retrieval_texts, safe_rmtree, write_generation

INDEXABLE = ("chunked", "index_failed")


def storage_root(settings: Settings) -> Path:
    """The storage directory must be outside the repository or inside the git-ignored ``build/`` directory."""
    root = Path(settings.storage_dir).resolve()
    repo = PROJECT_ROOT.resolve()
    if (root == repo or repo in root.parents) and (repo / "build") not in [root, *root.parents]:
        raise RuntimeError("KB_STORAGE_DIR inside the repository must be under build/ (git-ignored)")
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_filename(name: str) -> str:
    """Display name only (never used as a path): basename, NFC, no control / path / wildcard characters."""
    name = unicodedata.normalize("NFC", (name or "").replace("\\", "/").rsplit("/", 1)[-1])
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .")
    return (name or "document")[:150]


def default_embedder_factory(settings: Settings) -> Callable[[], Any]:
    def load():
        try:
            from ragas_eval.dataset_adapter import get_sentence_transformer
            return get_sentence_transformer(settings.embedding_model, "cpu")
        except Exception:  # noqa: BLE001 — not installed / not cached locally / offline
            raise EmbeddingUnavailable("本地向量模型不可用（未安装 sentence-transformers 或本机没有缓存模型）") from None
    return load


class IndexManager:
    def __init__(self, settings: Settings, embedder_factory: Optional[Callable[[], Any]] = None):
        self.settings = settings
        self.root = storage_root(settings)
        self._embedder_factory = embedder_factory or default_embedder_factory(settings)
        self._embedder = None
        self._embedder_error: Optional[str] = None
        self.build_lock = threading.Lock()
        self._retrievers: "OrderedDict[str, PlatformRetriever]" = OrderedDict()
        self._rlock = threading.Lock()
        self.fail_next_build: Optional[str] = None   # test hook: simulate a crash at a build stage

    # paths -----------------------------------------------------------------------------------------------------------
    def gen_dir(self, gen_id: str) -> Path:
        return self.root / "index" / f"gen-{gen_id}"

    def version_dir(self, v: DocumentVersion) -> Path:
        return self.root / "uploads" / v.document_id / str(v.version_no)

    # embeddings ------------------------------------------------------------------------------------------------------
    def embedder(self):
        if self.settings.dense_mode == "off":
            raise EmbeddingUnavailable("Dense 已关闭（KB_DENSE=off）")
        if self._embedder is None:
            self._embedder = self._embedder_factory()
        return self._embedder

    def embedder_status(self) -> Dict[str, Any]:
        if self.settings.dense_mode == "off":
            return {"available": False, "reason": "disabled"}
        try:
            self.embedder()
            return {"available": True, "model": self.settings.embedding_model}
        except EmbeddingUnavailable:
            return {"available": False, "reason": "embedding_model_unavailable"}

    def _version_embeddings(self, v: DocumentVersion, children: List[Dict[str, Any]], model) -> Any:
        import numpy as np
        texts = retrieval_texts(children)
        key = hashlib.sha256(json.dumps([self.settings.embedding_model, texts], ensure_ascii=False).encode()).hexdigest()
        d = self.version_dir(v)
        npy, meta = d / "embeddings.npy", d / "embeddings.json"
        if npy.exists() and meta.exists() and json.loads(meta.read_text(encoding="utf-8")).get("key") == key:
            return np.load(npy)
        emb = np.asarray(model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False),
                         dtype="float32") if texts else np.zeros((0, 1), dtype="float32")
        d.mkdir(parents=True, exist_ok=True)
        np.save(npy, emb)
        meta.write_text(json.dumps({"key": key, "model": self.settings.embedding_model, "n": len(texts)}), encoding="utf-8")
        return emb

    # serving ---------------------------------------------------------------------------------------------------------
    def retriever(self, gen: IndexGeneration) -> PlatformRetriever:
        with self._rlock:
            r = self._retrievers.get(gen.id)
            if r is None:
                model = None
                if gen.dense:
                    try:
                        model = self.embedder()
                    except EmbeddingUnavailable:
                        model = None
                r = PlatformRetriever(self.gen_dir(gen.id), model)
                self._retrievers[gen.id] = r
                while len(self._retrievers) > 2:
                    self._retrievers.popitem(last=False)
            return r

    @staticmethod
    def active_generation(db: Session) -> Optional[IndexGeneration]:
        return db.scalar(select(IndexGeneration).where(IndexGeneration.status == "active"))

    # building --------------------------------------------------------------------------------------------------------
    def _plan(self, db: Session) -> Tuple[List[Tuple[Document, DocumentVersion]], List[Document]]:
        plan, deleted = [], []
        for d in db.scalars(select(Document).where(Document.source == "upload")):
            if d.status == "deleted":
                deleted.append(d)
                continue
            if d.status != "active":
                continue
            versions = sorted(db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == d.id)),
                              key=lambda v: v.version_no)
            current = next((v for v in versions if v.id == d.current_version_id and v.status == "indexed"), None)
            pending = [v for v in versions if v.status in INDEXABLE and (current is None or v.version_no > current.version_no)]
            chosen = pending[-1] if pending else current
            if chosen is not None:
                plan.append((d, chosen))
        return plan, deleted

    def build(self, db: Session, actor: str) -> IndexGeneration:
        """Build and activate a new generation, or raise IngestError leaving the active one in place."""
        with self.build_lock:
            plan, deleted = self._plan(db)
            gen = IndexGeneration(status="building", built_by=actor[:64])
            db.add(gen)
            db.commit()
            tmp = self.root / "index" / f".tmp-{gen.id}"
            try:
                children, per_version = [], []
                for d, v in plan:
                    rows = list(db.scalars(select(Chunk).where(Chunk.version_id == v.id, Chunk.is_parent.is_(False))
                                           .order_by(Chunk.ordinal)))
                    recs = [dict(r.meta, chunk_id=r.chunk_id, text=r.text, source_file=d.doc_key) for r in rows]
                    per_version.append((v, recs))
                    children += recs
                if self.fail_next_build == "before_write":
                    self.fail_next_build = None
                    raise IngestError("index_build_failed", "索引构建失败（模拟故障）", 500)
                emb, model_name, dense_error = None, None, None
                if self.settings.dense_mode != "off":
                    try:
                        model = self.embedder()
                        import numpy as np
                        parts = [self._version_embeddings(v, recs, model) for v, recs in per_version if recs]
                        emb = np.concatenate(parts) if parts else None
                        model_name = self.settings.embedding_model
                    except EmbeddingUnavailable:
                        if self.settings.dense_mode == "required":
                            raise
                        dense_error = "embedding_model_unavailable"
                safe_rmtree(tmp, self.root)
                man = write_generation(tmp, children, emb, model_name)
                if self.fail_next_build == "after_write":
                    self.fail_next_build = None
                    raise IngestError("index_build_failed", "索引构建失败（模拟故障）", 500)
                check = PlatformRetriever(tmp, None)               # load back before activating
                if check.bm25.N != len(children):
                    raise IngestError("index_build_failed", "索引校验失败", 500)
                final = self.gen_dir(gen.id)
                tmp.rename(final)
            except Exception as e:  # noqa: BLE001
                safe_rmtree(tmp, self.root)
                db.rollback()
                code = e.code if isinstance(e, IngestError) else "index_build_failed"
                g = db.get(IndexGeneration, gen.id)
                g.status, g.error_code = "failed", code
                for _, v in plan:
                    vv = db.get(DocumentVersion, v.id)
                    if vv.status in INDEXABLE:
                        vv.status, vv.error_code, vv.error_message = "index_failed", code, "索引构建失败，旧版本仍在服务，可重新构建"
                db.commit()
                if isinstance(e, IngestError):
                    raise
                raise IngestError("index_build_failed", "索引构建失败，旧索引保持不变，可重新构建", 500) from None
            # --- activate atomically ---------------------------------------------------------------------------------
            now = utcnow()
            for old in db.scalars(select(IndexGeneration).where(IndexGeneration.status == "active")):
                old.status = "retired"
            g = db.get(IndexGeneration, gen.id)
            g.status, g.activated_at = "active", now
            g.chunk_count, g.document_count = man["chunks"], man["documents"]
            g.dense, g.dense_model, g.dense_error = man["dense"], man["dense_model"], dense_error
            for d, v in plan:
                vv = db.get(DocumentVersion, v.id)
                dd = db.get(Document, d.id)
                if dd.current_version_id and dd.current_version_id != vv.id:
                    prev = db.get(DocumentVersion, dd.current_version_id)
                    if prev is not None and prev.status == "indexed":
                        prev.status = "superseded"
                vv.status, vv.indexed_at, vv.generation_id = "indexed", now, gen.id
                vv.error_code = vv.error_message = None
                dd.current_version_id = vv.id
                for older in db.scalars(select(DocumentVersion).where(  # older pending uploads lost to a newer one
                        DocumentVersion.document_id == dd.id, DocumentVersion.version_no < vv.version_no,
                        DocumentVersion.status.in_(INDEXABLE))):
                    older.status = "superseded"
            purge_dirs = []
            for d in deleted:
                for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == d.id)):
                    for c in db.scalars(select(Chunk).where(Chunk.version_id == v.id)):
                        db.delete(c)
                    v.status = "purged"
                    purge_dirs.append(self.root / "uploads" / d.id)
                d.current_version_id = None
            bump_acl_version(db)
            db.commit()
            for p in purge_dirs:
                safe_rmtree(p, self.root)
            self._prune(db)
            return g

    def _prune(self, db: Session) -> None:
        keep = {g.id for g in db.scalars(select(IndexGeneration).where(IndexGeneration.status == "active"))}
        last_retired = db.scalars(select(IndexGeneration).where(IndexGeneration.status == "retired")
                                  .order_by(IndexGeneration.activated_at.desc()).limit(1)).first()
        if last_retired:
            keep.add(last_retired.id)
        base = self.root / "index"
        for p in base.glob("gen-*") if base.exists() else []:
            if p.name[4:] not in keep:
                safe_rmtree(p, self.root)


# ---------------------------------------------------------------------------------------------------------------------
class Ingestor:
    """Upload + processing of one file (no index build; callers decide when to build)."""

    def __init__(self, settings: Settings, index: IndexManager):
        self.settings, self.index = settings, index

    def read_limited(self, data: bytes) -> bytes:
        if len(data) > self.settings.max_upload_bytes:
            raise IngestError("file_too_large", f"文件超过 {self.settings.max_upload_bytes // (1024 * 1024)} MB 上限", 413)
        return data

    def find_same_name(self, db: Session, kb: KnowledgeBase, filename: str) -> Optional[Document]:
        return db.scalar(select(Document).where(Document.kb_id == kb.id, Document.source == "upload",
                                                Document.status != "deleted", Document.original_filename == filename))

    def find_same_content(self, db: Session, kb: KnowledgeBase, sha: str) -> Optional[Document]:
        for d, v in db.execute(select(Document, DocumentVersion).join(DocumentVersion, DocumentVersion.document_id == Document.id)
                               .where(Document.kb_id == kb.id, Document.status != "deleted", DocumentVersion.sha256 == sha,
                                      DocumentVersion.status.notin_(("failed", "purged", "superseded")))):
            return d
        return None

    def new_document(self, db: Session, kb: KnowledgeBase, filename: str, title: str, classification: str,
                     owner_department: Optional[str]) -> Document:
        d = Document(kb_id=kb.id, doc_key="", title=title or filename, classification=classification,
                     owner_department=owner_department, source="upload", original_filename=filename, status="active")
        db.add(d)
        db.flush()
        d.doc_key = f"{d.id[:8]}_{filename}"[:255]  # unique per document → unique source_file and chunk IDs
        bump_acl_version(db)
        db.flush()
        return d

    def add_version(self, db: Session, d: Document, filename: str, data: bytes, actor: str) -> DocumentVersion:
        fmt = parsers.detect_format(filename, data)
        n = (db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == d.id)) or 0) + 1
        v = DocumentVersion(document_id=d.id, version_no=n, original_filename=filename, file_format=fmt,
                            size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), created_by=actor[:64])
        ext = {"markdown": ".md", "txt": ".txt", "pdf": ".pdf", "docx": ".docx"}[fmt]
        v.storage_relpath = f"uploads/{d.id}/{n}/original{ext}"
        target = (self.index.root / v.storage_relpath).resolve()
        if self.index.root.resolve() not in target.parents:
            raise IngestError("invalid_path", "存储路径非法", 500)
        atomic_write(target, data)
        db.add(v)
        db.flush()
        return v

    def process(self, db: Session, d: Document, v: DocumentVersion) -> DocumentVersion:
        """parse → clean → chunk → store. Failures are recorded on the version only."""
        try:
            data = (self.index.root / v.storage_relpath).read_bytes()
            parsed = parsers.parse(v.original_filename, data)
            cleaned, pages = chunker.clean(parsed)
            if not cleaned.strip():
                raise IngestError("no_text", "清洗后没有文字内容")
            recs = chunker.chunk_text(cleaned, pages, d.doc_key, f"kb://{d.id}/v{v.version_no}")
            if not any(not r.get("is_parent") for r in recs):
                raise IngestError("no_chunks", "没有生成任何 Chunk")
            for c in db.scalars(select(Chunk).where(Chunk.version_id == v.id)):
                db.delete(c)
            for i, r in enumerate(recs):
                db.add(Chunk(version_id=v.id, chunk_id=r["chunk_id"], ordinal=i, is_parent=bool(r.get("is_parent")),
                             parent_id=r.get("parent_id"), section_heading=(r.get("section_heading") or None),
                             page=r.get("page"), paragraph=r.get("paragraph"), char_count=len(r.get("text", "")),
                             text=r.get("text", ""), meta={k: r[k] for k in r if k not in ("text",)}))
            v.status, v.chunk_count, v.page_count = "chunked", sum(1 for r in recs if not r.get("is_parent")), parsed.page_count
            v.error_code = v.error_message = None
        except IngestError as e:
            v.status, v.error_code, v.error_message = "failed", e.code, e.message[:255]
        except Exception:  # noqa: BLE001 — unexpected: record a generic code, never the exception text
            v.status, v.error_code, v.error_message = "failed", "processing_error", "文档处理失败"
        db.flush()
        return v
