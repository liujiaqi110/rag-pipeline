"""RAGAS-based automated evaluation for the rag_project retrieval pipeline.

This package is additive: it imports the existing retrieval functions from
``scripts/10_eval_retrieval.py`` read-only and never writes to ``index/``,
``chunks/`` or ``eval/``. All output goes to ``ragas_eval/reports/<run_id>/``.
"""

__version__ = "0.1.0"
