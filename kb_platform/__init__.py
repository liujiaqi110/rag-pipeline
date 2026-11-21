"""Enterprise knowledge-base backend (knowledge platform, phase 2): FastAPI + Pydantic + SQLAlchemy.

The question-answering path goes ONLY through the phase-1 ACL service (``ragas_eval.kb_service``); the legacy
single-tenant ``ragas_eval.rag_service`` is never imported or exposed here.
"""

__version__ = "0.2.0"
