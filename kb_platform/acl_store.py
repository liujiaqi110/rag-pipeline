"""Database → phase-1 ACL engine.

Permissions are persisted in SQL, but every decision is still made by ``ragas_eval.acl`` (default deny, deny
overrides, clearance AND grant, strict validation). Per request we build an :class:`AclPolicy` that contains:

* exactly ONE directory entry — the authenticated user (from the token, never from the request);
* the ACTIVE documents of ONE knowledge base and their grants.

Anything malformed in the database (unknown classification, wildcard subject, deny-all_authenticated, …) makes
``AclPolicy`` raise ``PolicyError``; callers treat that as "no access at all" (fail closed) and audit it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragas_eval.acl import AclPolicy, PolicyError

from .models import AclState, Document, KnowledgeBase, User

SUBJECT_TYPES = ("user", "role", "department", "all_authenticated")
EFFECTS = ("allow", "deny")


def acl_version(db: Session) -> int:
    st = db.get(AclState, 1)
    if st is None:
        st = AclState(id=1, version=1)
        db.add(st)
        db.flush()
    return st.version


def bump_acl_version(db: Session) -> int:
    st = db.get(AclState, 1)
    if st is None:
        st = AclState(id=1, version=1)
        db.add(st)
    st.version = (st.version or 1) + 1
    db.flush()
    return st.version


def user_entry(u: User) -> Dict[str, Any]:
    return {"roles": sorted(r.name for r in u.roles), "departments": sorted(d.name for d in u.departments),
            "clearance": u.clearance, "active": bool(u.active)}


def document_entry(d: Document) -> Dict[str, Any]:
    allow: Dict[str, Any] = {}
    deny: Dict[str, List[str]] = {}
    key = {"user": "users", "role": "roles", "department": "departments"}
    for g in d.grants:
        if g.effect not in EFFECTS or g.subject_type not in SUBJECT_TYPES:
            raise PolicyError("invalid grant row")
        if g.subject_type == "all_authenticated":
            if g.effect != "allow":
                raise PolicyError("deny all_authenticated is not supported")
            allow["all_authenticated"] = True
            continue
        bucket = allow if g.effect == "allow" else deny
        bucket.setdefault(key[g.subject_type], []).append(g.subject_value)
    for b in (allow, deny):  # deterministic order → stable policy SHA-256
        for k, v in list(b.items()):
            if isinstance(v, list):
                b[k] = sorted(set(v))
    out: Dict[str, Any] = {"classification": d.classification, "allow": allow, "deny": deny}
    if d.owner_department:
        out["owner_department"] = d.owner_department
    return out


def policy_for(db: Session, user: User, kb: KnowledgeBase) -> AclPolicy:
    """Raises PolicyError on any configuration problem (caller must deny)."""
    docs = [d for d in kb.documents if d.status == "active"]
    # policy_version names the KB only; the policy SHA-256 covers the actual content (this user's roles/departments/
    # clearance/active + every active document, classification and grant of the KB), so scope keys change exactly
    # when something relevant to THIS user in THIS KB changes.
    data = {"policy_version": f"kb-{kb.id}",
            "users": {user.username: user_entry(user)},
            "documents": {d.doc_key: document_entry(d) for d in docs}}
    return AclPolicy(data)


def scope_key(policy: AclPolicy, username: str) -> str:
    """Same value as ``ragas_eval.acl.build_scope(...).cache_key``, without needing the retrieval index."""
    pr = policy.principal(username)
    return f"{pr.fingerprint() if pr else 'none'}:{policy.sha256[:16]}"


def readable_documents(db: Session, user: User, kb: KnowledgeBase) -> Tuple[List[Document], Optional[str]]:
    """Active documents of ``kb`` the user may read. Second value = 'policy_error' when the KB policy is broken."""
    try:
        pol = policy_for(db, user, kb)
    except PolicyError:
        return [], "policy_error"
    return [d for d in kb.documents if d.status == "active" and pol.decide(user.username, d.doc_key).allowed], None


def kb_by_id(db: Session, kb_id: str) -> Optional[KnowledgeBase]:
    return db.get(KnowledgeBase, kb_id) if isinstance(kb_id, str) and len(kb_id) <= 64 else None


def all_kbs(db: Session) -> List[KnowledgeBase]:
    return list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.name)))
