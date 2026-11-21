"""P4 background jobs (real worker thread, file SQLite): non-blocking uploads, progress, cancellation (queued and
running), index-build failure recovery, restart recovery, permissions."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from sqlalchemy import select

from kb_platform.models import DocumentVersion, IndexGeneration, Job
from kb_platform.tests.helpers import ADMIN
from kb_platform.tests.ingest_fixtures import IngestCase, md_doc


class ThreadCase(IngestCase):
    jobs_mode = "thread"

    @classmethod
    def database_url(cls):
        cls._dbdir = Path(tempfile.mkdtemp(prefix="kbjobs_"))
        return f"sqlite:///{cls._dbdir / 'jobs.db'}"

    def setUp(self):
        self.app.state.jobs.hooks.clear()

    def wait(self, job_id, statuses=("succeeded", "failed", "cancelled"), timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.client.get(f"/api/v1/jobs/{job_id}", headers=self.h(ADMIN)).json()
            if j["status"] in statuses:
                return j
            time.sleep(0.05)
        self.fail(f"job {job_id} did not reach {statuses}: {j}")

    def block_at(self, checkpoint):
        """Make the worker stop at a checkpoint until the returned event is set."""
        entered, release = threading.Event(), threading.Event()

        def hook(job_id):
            entered.set()
            release.wait(30)
        self.app.state.jobs.hooks[checkpoint] = hook
        return entered, release

    def active_gen(self):
        with self.factory() as db:
            g = db.scalar(select(IndexGeneration).where(IndexGeneration.status == "active"))
            return g.id if g else None


class TestAsyncUpload(ThreadCase):
    def test_upload_returns_immediately_and_progress_is_queryable(self):
        entered, release = self.block_at("before_parse")
        t0 = time.time()
        r = self.upload("async.md", md_doc("异步", "CANARY-ASYNC", "异步专项"))
        took = time.time() - t0
        self.assertEqual(r.status_code, 202, r.text)                      # accepted, not finished
        b = r.json()
        self.assertTrue(b["process_job_id"] and b["index_job_id"])
        self.assertEqual(b["index"]["status"], "pending")
        self.assertTrue(entered.wait(10))
        j = self.client.get(f"/api/v1/jobs/{b['process_job_id']}", headers=self.h(ADMIN)).json()
        self.assertEqual((j["status"], j["stage"]), ("running", "parsing"))
        self.assertLess(took, 5)
        release.set()
        self.assertEqual(self.wait(b["process_job_id"])["status"], "succeeded")
        ij = self.wait(b["index_job_id"])
        self.assertEqual((ij["status"], ij["progress"]), ("succeeded", 100))
        self.assertTrue(ij["result"]["generation_id"])
        st = self.client.get(f"/api/v1/documents/{b['document']['id']}/status", headers=self.h(ADMIN)).json()
        self.assertEqual(st["current_version"]["status"], "indexed")
        self.grant(b["document"]["id"], departments=["engineering"])
        self.assertIn("CANARY-ASYNC", self.prompts_for("eng_alice", "异步专项 要求？")[1])

    def test_cancel_queued_job(self):
        entered, release = self.block_at("before_parse")
        first = self.upload("first.md", md_doc("一", "CANARY-FIRST", "第一专项")).json()
        self.assertTrue(entered.wait(10))
        second = self.upload("second.md", md_doc("二", "CANARY-SECOND", "第二专项")).json()
        r = self.client.post(f"/api/v1/jobs/{second['process_job_id']}/cancel", headers=self.h(ADMIN))
        self.assertEqual(r.json()["status"], "cancelled")
        self.app.state.jobs.hooks.clear()
        release.set()
        self.wait(first["index_job_id"])
        self.wait(second["index_job_id"])
        with self.factory() as db:
            self.assertEqual(db.get(DocumentVersion, second["version"]["id"]).status, "cancelled")
            self.assertEqual(db.get(DocumentVersion, first["version"]["id"]).status, "indexed")
        self.assert_index_consistent()
        # retry the cancelled processing job
        jobs = self.client.post(f"/api/v1/jobs/{second['process_job_id']}/retry", headers=self.h(ADMIN)).json()
        self.assertEqual([j["kind"] for j in jobs], ["process", "index"])
        self.wait(jobs[1]["id"])
        with self.factory() as db:
            self.assertEqual(db.get(DocumentVersion, second["version"]["id"]).status, "indexed")
        self.assertEqual(self.client.post(f"/api/v1/jobs/{jobs[1]['id']}/cancel", headers=self.h(ADMIN)).status_code, 409)

    def test_cancel_running_index_build_keeps_active_index(self):
        base = self.upload("base.md", md_doc("基", "CANARY-BASE", "基础专项")).json()
        self.wait(base["index_job_id"])
        gen_before = self.active_gen()
        entered, release = self.block_at("before_activate")
        nxt = self.upload("next.md", md_doc("新", "CANARY-NEXT", "新增专项")).json()
        self.assertTrue(entered.wait(20))                                 # build written, about to activate
        r = self.client.post(f"/api/v1/jobs/{nxt['index_job_id']}/cancel", headers=self.h(ADMIN)).json()
        self.assertTrue(r["cancel_requested"])
        release.set()
        j = self.wait(nxt["index_job_id"])
        self.assertEqual(j["status"], "cancelled")
        self.assertEqual(self.active_gen(), gen_before)                   # cancelled build never activated
        with self.factory() as db:
            self.assertEqual(db.get(DocumentVersion, nxt["version"]["id"]).status, "chunked")
            self.assertTrue(db.scalar(select(IndexGeneration).where(IndexGeneration.status == "cancelled")))
        self.assertEqual(list((self.storage / "index").glob(".tmp-*")), [])
        self.assert_index_consistent()
        self.app.state.jobs.hooks.clear()
        rj = self.client.post(f"/api/v1/jobs/{nxt['index_job_id']}/retry", headers=self.h(ADMIN)).json()[0]
        self.assertEqual(self.wait(rj["id"])["status"], "succeeded")
        self.assertNotEqual(self.active_gen(), gen_before)
        self.assert_index_consistent()

    def test_index_failure_then_retry(self):
        d = self.upload("fail.md", md_doc("失败", "CANARY-FAIL-V1", "失败专项")).json()
        self.wait(d["index_job_id"])
        gen = self.active_gen()
        self.index.fail_next_build = "after_write"
        v2 = self.client.post(f"/api/v1/documents/{d['document']['id']}/versions",
                              files={"file": ("fail.md", md_doc("失败", "CANARY-FAIL-V2", "失败专项"))}, headers=self.h(ADMIN)).json()
        j = self.wait(v2["index_job_id"])
        self.assertEqual((j["status"], j["error_code"]), ("failed", "index_build_failed"))
        self.assertEqual(self.active_gen(), gen)
        rj = self.client.post(f"/api/v1/jobs/{v2['index_job_id']}/retry", headers=self.h(ADMIN)).json()[0]
        self.assertEqual(self.wait(rj["id"])["status"], "succeeded")
        with self.factory() as db:
            self.assertEqual(db.get(DocumentVersion, v2["version"]["id"]).status, "indexed")
        self.assert_index_consistent()

    def test_job_visibility(self):
        d = self.upload("vis.md", md_doc("可见", "CANARY-VIS")).json()
        self.wait(d["index_job_id"])
        self.assertEqual(self.client.get(f"/api/v1/jobs/{d['process_job_id']}", headers=self.h("eng_alice")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{d['process_job_id']}/cancel", headers=self.h("eng_alice")).status_code, 403)
        jobs = self.client.get("/api/v1/jobs", headers=self.h(ADMIN)).json()
        self.assertIn(d["process_job_id"], {j["id"] for j in jobs})
        self.assertFalse(any("CANARY" in str(j) for j in jobs))


class TestRestartRecovery(ThreadCase):
    def test_interrupted_jobs_are_marked_and_retryable(self):
        d = self.upload("crash.md", md_doc("重启", "CANARY-CRASH", "重启专项")).json()
        self.wait(d["index_job_id"])
        with self.factory() as db:                      # simulate a process that died mid-build
            j = Job(kind="index", status="running", stage="indexing", created_by=ADMIN, kb_id=self.kb_id)
            db.add(j)
            db.commit()
            jid = j.id
        from kb_platform.jobs import JobRunner
        runner = JobRunner(self.factory, self.backend, self.settings_obj)
        self.assertEqual(runner.recover(), 1)
        jj = self.client.get(f"/api/v1/jobs/{jid}", headers=self.h(ADMIN)).json()
        self.assertEqual((jj["status"], jj["error_code"]), ("failed", "interrupted"))
        rj = self.client.post(f"/api/v1/jobs/{jid}/retry", headers=self.h(ADMIN)).json()[0]
        self.assertEqual(self.wait(rj["id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
