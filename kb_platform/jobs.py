"""P4 background jobs: document processing (parse → clean → chunk) and index builds run outside the HTTP request.

    POST upload ─► version stored ─► job "process" (queued) ─► job "index" (queued) ─► 202 + job ids
                                       worker: parsing → chunking        worker: embedding → writing → activating

* Jobs are rows in ``jobs`` (status queued → running → succeeded | failed | cancelled, ``stage``, ``progress``) so
  their state can be queried (``GET /api/v1/jobs/{id}``) and survives a restart.
* ONE worker thread processes jobs in FIFO order (index builds are serialised anyway by ``IndexManager.build_lock``).
  ``KB_JOBS_MODE=inline`` runs a job synchronously right after it is enqueued (tests / tiny installs) — same code path.
* Cancellation: a queued job is cancelled immediately; a running job stops at the next checkpoint. A cancelled index
  job never activates a generation (the active index stays as it was); a cancelled processing job leaves its version
  ``cancelled`` (retryable).
* Restart recovery: jobs still ``running`` when the process starts were interrupted → ``failed`` / ``interrupted``;
  they can be retried. Index builds are atomic (P3), so an interrupted build never leaves a half index active.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from sqlalchemy import select

from .ingest.errors import IngestError
from .models import Document, DocumentVersion, Job, utcnow

log = logging.getLogger("kb_platform")
TERMINAL = ("succeeded", "failed", "cancelled")


class JobCancelled(BaseException):
    """BaseException on purpose: it must pass through the pipeline's broad ``except Exception`` handlers."""


class JobRunner:
    def __init__(self, sessionmaker, backend, settings):
        self.sm, self.backend, self.settings = sessionmaker, backend, settings
        self.mode = settings.jobs_mode
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.hooks: Dict[str, Callable[[str], None]] = {}   # test hooks: checkpoint name → fn(job_id)

    # lifecycle -------------------------------------------------------------------------------------------------------
    def recover(self) -> int:
        """Mark jobs interrupted by a previous process exit."""
        with self.sm() as db:
            n = 0
            for j in db.scalars(select(Job).where(Job.status == "running")):
                j.status, j.error_code, j.error_message, j.finished_at = "failed", "interrupted", "服务重启时任务被中断，可重试", utcnow()
                n += 1
            db.commit()
            return n

    def start(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="kb-jobs", daemon=True)
                self._thread.start()

    def stop(self, timeout: float = 10.0):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self):
        while not self._stop.is_set():
            jid = self._next()
            if jid is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                self.run(jid)
            except Exception:  # noqa: BLE001 — never kill the worker; the job is already marked failed
                log.error("job worker error (%s)", jid)

    def _next(self) -> Optional[str]:
        with self.sm() as db:
            j = db.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).limit(1)).first()
            return j.id if j else None

    def drain(self, max_jobs: int = 1000) -> int:
        """Run queued jobs in this thread until none is left (tests / CLI)."""
        n = 0
        while n < max_jobs:
            jid = self._next()
            if jid is None:
                return n
            self.run(jid)
            n += 1
        return n

    # enqueue / control -----------------------------------------------------------------------------------------------
    def enqueue(self, db, kind: str, created_by: str, kb_id: Optional[str] = None, document_id: Optional[str] = None,
                version_id: Optional[str] = None) -> Job:
        j = Job(kind=kind, status="queued", stage="queued", created_by=created_by[:64], kb_id=kb_id,
                document_id=document_id, version_id=version_id)
        db.add(j)
        db.commit()
        if self.mode == "inline":
            self.run(j.id)
            db.expire_all()
            return db.get(Job, j.id)
        self.start()
        self._wake.set()
        return j

    def cancel(self, db, job: Job) -> Job:
        if job.status == "queued":
            job.status, job.stage, job.finished_at = "cancelled", "cancelled", utcnow()
            job.error_code, job.error_message = "cancelled", "任务已取消"
            self._on_cancel(db, job)
        elif job.status == "running":
            job.cancel_requested = True
        db.commit()
        return job

    # execution -------------------------------------------------------------------------------------------------------
    def _set(self, job_id: str, **kw):
        with self.sm() as db:
            j = db.get(Job, job_id)
            for k, v in kw.items():
                setattr(j, k, v)
            db.commit()

    def _checkpoint(self, job_id: str, name: str):
        h = self.hooks.get(name)
        if h:
            h(job_id)
        with self.sm() as db:
            if db.get(Job, job_id).cancel_requested:
                raise JobCancelled(name)

    def _on_cancel(self, db, job: Job):
        if job.kind == "process" and job.version_id:
            v = db.get(DocumentVersion, job.version_id)
            if v is not None and v.status in ("uploaded", "processing"):
                v.status, v.error_code, v.error_message = "cancelled", "cancelled", "处理已取消，可重试"

    def run(self, job_id: str) -> None:
        with self.sm() as db:
            j = db.get(Job, job_id)
            if j is None or j.status != "queued":
                return
            j.status, j.started_at, j.attempts, j.stage, j.progress = "running", utcnow(), (j.attempts or 0) + 1, "starting", 5
            db.commit()
            kind = j.kind
        try:
            if kind == "process":
                result = self._process(job_id)
            elif kind == "index":
                result = self._index(job_id)
            else:
                raise IngestError("unknown_job", "未知任务类型", 500)
            status = "failed" if result.get("failed") else "succeeded"
            self._set(job_id, status=status, stage="done", progress=100, finished_at=utcnow(), result=result,
                      error_code=result.get("error_code"), error_message=result.get("error_message"))
        except JobCancelled:
            with self.sm() as db:
                j = db.get(Job, job_id)
                j.status, j.stage, j.finished_at = "cancelled", "cancelled", utcnow()
                j.error_code, j.error_message = "cancelled", "任务已取消"
                self._on_cancel(db, j)
                db.commit()
        except IngestError as e:
            self._set(job_id, status="failed", stage="failed", finished_at=utcnow(), error_code=e.code,
                      error_message=e.message[:255])
        except Exception:  # noqa: BLE001 — never store exception text (may contain content / paths)
            log.error("job %s failed with an unexpected error", job_id)
            self._set(job_id, status="failed", stage="failed", finished_at=utcnow(), error_code="job_error",
                      error_message="任务执行失败")

    def _process(self, job_id: str) -> Dict[str, Any]:
        from .ingest.manager import Ingestor
        idx = self.backend.index
        with self.sm() as db:
            j = db.get(Job, job_id)
            v = db.get(DocumentVersion, j.version_id)
            d = db.get(Document, j.document_id)
            if v is None or d is None or d.status == "deleted":
                return {"failed": True, "error_code": "document_gone", "error_message": "文档已删除"}
            v.status = "processing"
            db.commit()
        self._set(job_id, stage="parsing", progress=20)
        self._checkpoint(job_id, "before_parse")
        with self.sm() as db:
            j = db.get(Job, job_id)
            v, d = db.get(DocumentVersion, j.version_id), db.get(Document, j.document_id)
            Ingestor(self.settings, idx).process(db, d, v, checkpoint=lambda: self._checkpoint(job_id, "before_store"))
            db.commit()                      # commit BEFORE touching the job row from another session (SQLite lock)
            self._set(job_id, stage="chunked", progress=90)
            if v.status == "failed":
                return {"failed": True, "error_code": v.error_code, "error_message": v.error_message,
                        "version_status": v.status}
            return {"version_status": v.status, "chunks": v.chunk_count}

    def _index(self, job_id: str) -> Dict[str, Any]:
        idx = self.backend.index
        self._set(job_id, stage="indexing", progress=30)
        self._checkpoint(job_id, "before_index")
        with self.sm() as db:
            try:
                g = idx.build(db, self._actor(job_id), checkpoint=lambda stage: self._checkpoint(job_id, stage))
            except IngestError as e:
                active = idx.active_generation(db)
                return {"failed": True, "error_code": e.code, "error_message": e.message,
                        "generation_id": active.id if active else None, "dense": active.dense if active else None}
            self.backend.cache.clear()
            return {"generation_id": g.id, "dense": g.dense, "dense_error": g.dense_error, "chunks": g.chunk_count}

    def _actor(self, job_id: str) -> str:
        with self.sm() as db:
            return db.get(Job, job_id).created_by
