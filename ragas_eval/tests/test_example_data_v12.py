"""v1.2 lightweight example-data validation (runs in CI): the bundled example corpus builds into a BM25 index with the
unchanged pipeline scripts, the annotations resolve to chunks, the build is reproducible, and the BM25 evaluation runs.
No embedding model, no Ollama, no private data."""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from ragas_eval import retrieval_eval as ev
from ragas_eval.config import PROJECT_ROOT

DOCS = PROJECT_ROOT / "examples" / "docs"
QUESTIONS = PROJECT_ROOT / "examples" / "questions.jsonl"


def build(out: Path) -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_example_index", PROJECT_ROOT / "scripts" / "build_example_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(["--out", str(out)])


class TestExampleData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="example_build_"))
        cls.data = cls.tmp / "a"
        cls.rc = build(cls.data)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_ok(self):
        self.assertEqual(self.rc, 0)
        man = json.loads((self.data / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(len(man["docs"]), 6)
        self.assertFalse(man["dense"])
        self.assertTrue((self.data / "index" / "sparse" / "bm25_index.pkl").exists())

    def test_reproducible_chunks(self):
        b = self.tmp / "b"
        self.assertEqual(build(b), 0)
        m1 = json.loads((self.data / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))
        m2 = json.loads((b / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(m1["chunks_content_sha256"], m2["chunks_content_sha256"])

    def test_refuses_to_overwrite(self):
        self.assertEqual(build(self.data), 2)

    def test_annotations_resolve(self):
        qs = ev.load_questions(QUESTIONS)
        slots, errs = ev.resolve(qs, ev.load_chunks(self.data))
        self.assertEqual(errs, [])
        cats = {q["category"] for q in qs}
        for c in ("single_fact", "multi_step", "cross_document", "doc_scoped", "numeric_or_code", "unanswerable"):
            self.assertIn(c, cats)
        self.assertTrue(all(it for s in slots.values() for sl in s for g in sl for it in g))

    def test_bad_annotation_is_reported(self):
        qs = ev.load_questions(QUESTIONS)
        qs[0] = json.loads(json.dumps(qs[0]))
        qs[0]["evidence_slots"][0]["groups"][0][0]["quote"] = "这句话不在任何文档里"
        _, errs = ev.resolve(qs, ev.load_chunks(self.data))
        self.assertTrue(any("quote not found" in e for e in errs))

    def test_bm25_evaluation_runs(self):
        rep = ev.evaluate(self.data, QUESTIONS, ["bm25"])
        agg = rep["modes"]["bm25"]["aggregate"]
        self.assertEqual(agg["n"], 18)
        for k in ("hit@5", "recall@5", "mrr@10", "ndcg@10", "slot_fullcov@5"):
            self.assertGreaterEqual(agg[k], 0.0)
            self.assertLessEqual(agg[k], 1.0)
        self.assertEqual(rep["labels"]["evaluation_status"], "provisional")
        self.assertIn("# 示例知识库检索评估", ev.to_markdown(rep))


class TestExampleContentIsClean(unittest.TestCase):
    def test_no_secrets_or_personal_paths(self):
        pats = [r"sk-[A-Za-z0-9_-]{20,}", r"inviteCode=", r"BEGIN [A-Z ]*PRIVATE KEY", r"[A-Za-z]:\\Users\\[A-Za-z]",
                r"ssh -p \d+", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"]
        for p in list(DOCS.glob("*.md")) + [QUESTIONS]:
            t = p.read_text(encoding="utf-8")
            for pat in pats:
                self.assertIsNone(re.search(pat, t), f"{p.name}: {pat}")

    def test_docs_declare_original_example(self):
        for p in DOCS.glob("*.md"):
            self.assertIn("原创示例文档", p.read_text(encoding="utf-8"), p.name)


if __name__ == "__main__":
    unittest.main()
