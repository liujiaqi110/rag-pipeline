"""Knowledge-platform ACL (phase 1): identities, document-level policies, default-deny decisions, access scopes.

Design rules (see docs/KNOWLEDGE_PLATFORM.md):

* **Identity comes from the trusted directory** in the policy file. Callers pass a ``user_id`` only; roles,
  departments and clearance are never taken from the request.
* **Default deny.** A document without a policy, an unknown user, an inactive user, or a policy that grants nothing
  → no access. There is no built-in superuser; even ``executive`` roles only see what a policy grants.
* **Deny overrides allow.** ``deny.users / deny.roles / deny.departments`` win over every grant.
* **Clearance is an AND condition.** A grant only counts if ``user.clearance >= classification level``.
* **Wildcards are rejected** (``"*"`` in any list). Company-wide documents use ``"all_authenticated": true``.
* **Fail closed on bad policy files.** Unknown keys, unknown classifications or malformed values raise PolicyError;
  the service refuses to start rather than guessing.
* The unit of protection is the document (``source_file`` of every chunk). Chunk → document comes from the BM25
  index documents; a chunk whose Dense entry disagrees about its document is excluded (fail closed).

An :class:`AccessScope` is the per-request, per-principal set of authorised documents and chunk IDs. Retrieval uses
it *before* candidate pools are cut (see ``acl_retrieval``), so unauthorised chunks never occupy a candidate slot and
never reach fusion, context selection or the prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional

CLASSIFICATIONS: Dict[str, int] = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
POLICY_ENV = "RAG_ACL_POLICY"

_USER_KEYS = {"roles", "departments", "clearance", "active", "display_name"}
_DOC_KEYS = {"classification", "allow", "deny", "owner_department", "note"}
_ALLOW_KEYS = {"users", "roles", "departments", "all_authenticated"}
_DENY_KEYS = {"users", "roles", "departments"}
_TOP_KEYS = {"policy_version", "note", "users", "documents"}


class PolicyError(ValueError):
    """The policy file is malformed. The service must not start (fail closed)."""


class AclViolation(RuntimeError):
    """An unauthorised chunk reached a stage it must never reach. The request is aborted without details."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: FrozenSet[str] = frozenset()
    departments: FrozenSet[str] = frozenset()
    clearance: int = 0
    active: bool = True

    def fingerprint(self) -> str:
        raw = json.dumps([self.user_id, sorted(self.roles), sorted(self.departments), self.clearance, self.active])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DocumentPolicy:
    doc_id: str
    classification: str = "internal"
    allow_users: FrozenSet[str] = frozenset()
    allow_roles: FrozenSet[str] = frozenset()
    allow_departments: FrozenSet[str] = frozenset()
    all_authenticated: bool = False
    deny_users: FrozenSet[str] = frozenset()
    deny_roles: FrozenSet[str] = frozenset()
    deny_departments: FrozenSet[str] = frozenset()
    owner_department: Optional[str] = None

    @property
    def required_clearance(self) -> int:
        return CLASSIFICATIONS[self.classification]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def decide(principal: Optional[Principal], policy: Optional[DocumentPolicy]) -> Decision:
    """The single access rule. Order matters: identity → policy exists → deny → clearance → grant."""
    if principal is None:
        return Decision(False, "unknown_user")
    if not principal.active:
        return Decision(False, "inactive_user")
    if policy is None:
        return Decision(False, "no_policy")
    if (principal.user_id in policy.deny_users or principal.roles & policy.deny_roles
            or principal.departments & policy.deny_departments):
        return Decision(False, "explicit_deny")
    if principal.clearance < policy.required_clearance:
        return Decision(False, "insufficient_clearance")
    if principal.user_id in policy.allow_users:
        return Decision(True, "grant_user")
    if principal.roles & policy.allow_roles:
        return Decision(True, "grant_role")
    if principal.departments & policy.allow_departments:
        return Decision(True, "grant_department")
    if policy.all_authenticated:
        return Decision(True, "grant_all_authenticated")
    return Decision(False, "no_matching_grant")


# ---------------------------------------------------------------------------------------------------------------------
def _str_set(v: Any, where: str) -> FrozenSet[str]:
    if v is None:
        return frozenset()
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise PolicyError(f"{where}: must be a list of non-empty strings")
    if any(x.strip() == "*" or "*" in x for x in v):
        raise PolicyError(f"{where}: wildcards are not allowed (use allow.all_authenticated)")
    return frozenset(x.strip() for x in v)


def _check_keys(d: Any, allowed: set, where: str) -> Dict[str, Any]:
    if not isinstance(d, dict):
        raise PolicyError(f"{where}: must be an object")
    extra = set(d) - allowed
    if extra:
        raise PolicyError(f"{where}: unknown keys {sorted(extra)}")
    return d


class AclPolicy:
    """Parsed, validated, immutable policy (users directory + document policies)."""

    def __init__(self, data: Mapping[str, Any]):
        data = _check_keys(dict(data), _TOP_KEYS, "policy")
        self.version = str(data.get("policy_version") or "")
        if not self.version:
            raise PolicyError("policy: policy_version is required")
        self.sha256 = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        self._users: Dict[str, Principal] = {}
        users, documents = data.get("users", {}), data.get("documents", {})
        if not isinstance(users, dict) or not isinstance(documents, dict):
            raise PolicyError("policy: users and documents must be objects")
        for uid, u in users.items():
            u = _check_keys(u, _USER_KEYS, f"users.{uid}")
            clr = u.get("clearance", 0)
            if not isinstance(clr, int) or isinstance(clr, bool) or not 0 <= clr <= max(CLASSIFICATIONS.values()):
                raise PolicyError(f"users.{uid}.clearance: integer 0..{max(CLASSIFICATIONS.values())} required")
            active = u.get("active", True)
            if not isinstance(active, bool):
                raise PolicyError(f"users.{uid}.active: boolean required")
            self._users[uid] = Principal(uid, _str_set(u.get("roles"), f"users.{uid}.roles"),
                                         _str_set(u.get("departments"), f"users.{uid}.departments"), clr, active)
        self._docs: Dict[str, DocumentPolicy] = {}
        for doc, p in documents.items():
            p = _check_keys(p, _DOC_KEYS, f"documents.{doc}")
            cls = p.get("classification", "internal")
            if cls not in CLASSIFICATIONS:
                raise PolicyError(f"documents.{doc}.classification: one of {sorted(CLASSIFICATIONS)}")
            al = _check_keys(p.get("allow") or {}, _ALLOW_KEYS, f"documents.{doc}.allow")
            de = _check_keys(p.get("deny") or {}, _DENY_KEYS, f"documents.{doc}.deny")
            aa = al.get("all_authenticated", False)
            if not isinstance(aa, bool):
                raise PolicyError(f"documents.{doc}.allow.all_authenticated: boolean required")
            self._docs[doc] = DocumentPolicy(
                doc, cls, _str_set(al.get("users"), f"{doc}.allow.users"), _str_set(al.get("roles"), f"{doc}.allow.roles"),
                _str_set(al.get("departments"), f"{doc}.allow.departments"), aa,
                _str_set(de.get("users"), f"{doc}.deny.users"), _str_set(de.get("roles"), f"{doc}.deny.roles"),
                _str_set(de.get("departments"), f"{doc}.deny.departments"), p.get("owner_department"))

    @classmethod
    def load(cls, path: Path) -> "AclPolicy":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise PolicyError(f"cannot read policy file: {type(e).__name__}") from None
        return cls(data)

    @classmethod
    def from_env(cls, default: Optional[Path] = None) -> "AclPolicy":
        p = os.environ.get(POLICY_ENV) or (str(default) if default else "")
        if not p:
            raise PolicyError(f"no ACL policy: set {POLICY_ENV} (the knowledge platform never runs without one)")
        return cls.load(Path(p))

    def principal(self, user_id: str) -> Optional[Principal]:
        """Directory lookup. Unknown → None (→ default deny)."""
        return self._users.get(user_id) if isinstance(user_id, str) else None

    def document(self, doc_id: str) -> Optional[DocumentPolicy]:
        return self._docs.get(doc_id)

    def decide(self, user_id: str, doc_id: str) -> Decision:
        return decide(self.principal(user_id), self.document(doc_id))

    @property
    def documents(self) -> List[str]:
        return sorted(self._docs)

    @property
    def users(self) -> List[str]:
        return sorted(self._users)


# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AccessScope:
    """Authorised documents / chunks for ONE principal under ONE policy version. Immutable, per request."""

    user_id: str
    principal_fingerprint: str
    policy_version: str
    policy_sha256: str
    allowed_docs: FrozenSet[str]
    allowed_chunks: FrozenSet[str]
    excluded_inconsistent: int = 0     # chunks dropped because BM25/Dense disagreed about their document

    @property
    def cache_key(self) -> str:
        """Any cache of retrieval results MUST be keyed by this (principal + policy), never by the query alone."""
        return f"{self.principal_fingerprint}:{self.policy_sha256[:16]}"

    def allows_chunk(self, chunk_id: Optional[str]) -> bool:
        return bool(chunk_id) and chunk_id in self.allowed_chunks

    def summary(self) -> Dict[str, Any]:
        """Safe for audit output: counts only, never the names of documents the user cannot see."""
        return {"user_id": self.user_id, "policy_version": self.policy_version,
                "policy_sha256": self.policy_sha256[:16], "authorized_documents": len(self.allowed_docs),
                "authorized_chunks": len(self.allowed_chunks)}


def build_scope(policy: AclPolicy, user_id: str, chunk_sources: Mapping[str, str],
                dense_sources: Optional[Mapping[str, str]] = None) -> AccessScope:
    """``chunk_sources``: chunk_id → source_file from the BM25 index documents (authoritative).
    ``dense_sources``: optional chunk_id → source_file from the Dense index; disagreement → excluded."""
    pr = policy.principal(user_id)
    docs = {d for d in set(chunk_sources.values()) if decide(pr, policy.document(d)).allowed}
    chunks, bad = set(), 0
    for cid, src in chunk_sources.items():
        if src not in docs:
            continue
        if dense_sources is not None and cid in dense_sources and dense_sources[cid] != src:
            bad += 1
            continue
        chunks.add(cid)
    return AccessScope(user_id=user_id if pr else "<unknown>", principal_fingerprint=pr.fingerprint() if pr else "none",
                       policy_version=policy.version, policy_sha256=policy.sha256, allowed_docs=frozenset(docs),
                       allowed_chunks=frozenset(chunks), excluded_inconsistent=bad)


# ---------------------------------------------------------------------------------------------------------------------
class FilteredBM25View:
    """Read-only view of a SimpleBM25 index restricted to authorised chunks.

    It exposes exactly the attributes ``search_bm25`` reads (documents, doc_tokens, idf, avg_doc_len, k1, b,
    tokenize), so the ORIGINAL scoring function runs unchanged, but only over authorised documents: unauthorised
    chunks are never scored and cannot take a candidate slot. Corpus statistics (idf, avg_doc_len) stay global so an
    authorised chunk keeps exactly the score it has without ACL (regression-equal ranking among authorised chunks).
    """

    def __init__(self, base: Any, scope: AccessScope):
        self._base = base
        pairs = [(d, t) for d, t in zip(base.documents, base.doc_tokens) if scope.allows_chunk(d.get("chunk_id"))]
        self.documents: List[Dict[str, Any]] = [d for d, _ in pairs]
        self.doc_tokens: List[List[str]] = [t for _, t in pairs]
        self.doc_lengths = [len(t) for t in self.doc_tokens]
        self.idf, self.avg_doc_len, self.k1, self.b = base.idf, base.avg_doc_len, base.k1, base.b
        self.N = len(self.documents)

    def tokenize(self, text: str):
        return self._base.tokenize(text)


def assert_within_scope(chunk_ids: Iterable[Optional[str]], scope: AccessScope, stage: str) -> None:
    """Defence in depth. Raises AclViolation WITHOUT naming the offending chunk (the message may be logged/shown)."""
    n = sum(1 for c in chunk_ids if not scope.allows_chunk(c))
    if n:
        raise AclViolation(f"acl gate failed at stage '{stage}'")
