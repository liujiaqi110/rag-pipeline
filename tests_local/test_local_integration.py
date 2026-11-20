"""HEAVY local integration tests — NOT run in CI. Need a locally cached BAAI/bge-m3 and a running Ollama with
qwen2.5:7b (never downloaded or pulled by these tests).

    $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"
    python -m unittest discover -s tests_local -t .

Builds the example knowledge base with the Dense index in a temp dir, checks Dense/RRF retrieval through the formal
service retriever, and runs real local question answering on it (deterministic functional checks only).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ollama_ready() -> bool:
    try:
        sys.path.insert(0, str(ROOT))
        from ragas_eval.local_llm import OllamaClient
        return OllamaClient().model_info().get("model") == "qwen2.5:7b"
    except Exception:  # noqa: BLE001
        return False


class TestLocalIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="example_dense_"))
        cls.data = cls.tmp / "data"
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, "scripts/build_example_index.py", "--dense", "--out", str(cls.data)], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise unittest.SkipTest("Dense example index could not be built (bge-m3 not cached locally?)")
        cls.env = env

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_py(self, *args):
        return subprocess.run([sys.executable, *args], cwd=ROOT, env=self.env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800)

    def test_dense_and_rrf_retrieval(self):
        r = self.run_py("-m", "ragas_eval.retrieval_eval", "run", "--data", str(self.data), "--modes", "bm25,dense,rrf")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        agg = json.loads(r.stdout.strip().splitlines()[-1])
        for m in ("bm25", "dense", "rrf"):
            self.assertEqual(agg[m]["n"], 18)

    @unittest.skipUnless(ollama_ready(), "Ollama with qwen2.5:7b not available")
    def test_real_question_answering(self):
        r = self.run_py("-m", "ragas_eval.example_qa", "--data", str(self.data))
        summary = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["invalid_citations_left"], 0)
        self.assertEqual(summary["refusals_passed"], summary["unanswerable"])
        self.assertEqual(summary["passed"], summary["questions"], r.stdout[-1500:])


if __name__ == "__main__":
    unittest.main()
