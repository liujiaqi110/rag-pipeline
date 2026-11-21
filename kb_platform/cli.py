"""Operator CLI (the only way to create a platform admin — there is no API for privilege escalation).

    python -m kb_platform.cli init-db                 # = db-upgrade (Alembic; existing data is kept)
    python -m kb_platform.cli db-upgrade | db-current
    python -m kb_platform.cli create-admin --username admin          # password read from env KB_ADMIN_PASSWORD
    python -m kb_platform.cli seed-example --credentials-out build/kb_example_credentials.json
    python -m kb_platform.cli export-openapi --out docs/openapi.json

``seed-example`` loads the SYNTHETIC users/documents of examples/acl_corpus/acl_policy.json into the database with
random passwords. The passwords are written only to the local credentials file you name (keep it under build/, which
is git-ignored) and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

from . import security
from .acl_store import bump_acl_version
from .db import init_db, make_engine, make_sessionmaker
from .models import Department, Document, DocumentGrant, KnowledgeBase, Role, User
from .settings import Settings

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_POLICY = ROOT / "examples" / "acl_corpus" / "acl_policy.json"


def _factory():
    st = Settings()
    if st.database_url.startswith("sqlite:///./"):
        (ROOT / "build").mkdir(exist_ok=True)
    eng = make_engine(st.database_url)
    init_db(eng)
    return make_sessionmaker(eng)


def _get_or_create(db, model, name):
    row = db.scalar(select(model).where(model.name == name))
    if row is None:
        row = model(name=name)
        db.add(row)
        db.flush()
    return row


def seed_example(factory, credentials_out: Path, kb_name: str = "示例企业知识库") -> dict:
    """Idempotent for an empty DB; refuses to overwrite existing users."""
    data = json.loads(EXAMPLE_POLICY.read_text(encoding="utf-8"))
    creds = {}
    with factory() as db:
        kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == kb_name)) or KnowledgeBase(name=kb_name)
        db.add(kb)
        db.flush()
        for uname, u in data["users"].items():
            if db.scalar(select(User).where(User.username == uname)):
                raise SystemExit(f"user {uname} already exists; refusing to overwrite")
            pw = secrets.token_urlsafe(12) + "a1"
            creds[uname] = pw
            db.add(User(username=uname, password_hash=security.hash_password(pw), clearance=u.get("clearance", 0),
                        active=u.get("active", True), roles=[_get_or_create(db, Role, r) for r in u.get("roles", [])],
                        departments=[_get_or_create(db, Department, d) for d in u.get("departments", [])]))
        for doc_key in sorted(p.name for p in (ROOT / "examples" / "acl_corpus" / "docs").glob("*.md")):
            p = data["documents"].get(doc_key)
            d = Document(kb_id=kb.id, doc_key=doc_key, title=doc_key, classification=(p or {}).get("classification", "internal"),
                         owner_department=(p or {}).get("owner_department"))
            if p:  # documents without a policy get NO grants (default deny), like legal_unlabeled_draft.md
                al, de = p.get("allow", {}), p.get("deny", {})
                for t, key in (("user", "users"), ("role", "roles"), ("department", "departments")):
                    d.grants += [DocumentGrant(effect="allow", subject_type=t, subject_value=v) for v in al.get(key, [])]
                    d.grants += [DocumentGrant(effect="deny", subject_type=t, subject_value=v) for v in de.get(key, [])]
                    for v in al.get(key, []) + de.get(key, []):
                        if t == "role":
                            _get_or_create(db, Role, v)
                        elif t == "department":
                            _get_or_create(db, Department, v)
                if al.get("all_authenticated"):
                    d.grants.append(DocumentGrant(effect="allow", subject_type="all_authenticated", subject_value=""))
            db.add(d)
        bump_acl_version(db)
        db.commit()
        kb_id = kb.id
    if credentials_out:
        credentials_out.parent.mkdir(parents=True, exist_ok=True)
        credentials_out.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    return {"kb_id": kb_id, "users": sorted(creds)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kb_platform.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    a = sub.add_parser("create-admin")
    a.add_argument("--username", required=True)
    s = sub.add_parser("seed-example")
    s.add_argument("--credentials-out", default=str(ROOT / "build" / "kb_example_credentials.json"))
    sub.add_parser("db-upgrade", help="apply Alembic migrations (existing data is kept)")
    sub.add_parser("db-current", help="print the current migration revision")
    o = sub.add_parser("export-openapi")
    o.add_argument("--out", default=str(ROOT / "docs" / "openapi.json"))
    args = ap.parse_args(argv)
    if args.cmd == "export-openapi":
        from .app import create_app
        spec = create_app(Settings(database_url="sqlite://")).openapi()
        Path(args.out).write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.cmd in ("db-upgrade", "db-current"):
        from . import migrate
        st = Settings()
        eng = make_engine(st.database_url)
        rev = migrate.upgrade(eng) if args.cmd == "db-upgrade" else migrate.current(eng)
        print(f"revision {rev} (head {migrate.head()})")
        return 0
    factory = _factory()
    if args.cmd == "init-db":
        print("database ready")
    elif args.cmd == "create-admin":
        pw = os.environ.get("KB_ADMIN_PASSWORD", "")
        problem = security.password_problems(pw)
        if problem:
            print(f"set KB_ADMIN_PASSWORD first ({problem})", file=sys.stderr)
            return 2
        with factory() as db:
            if db.scalar(select(User).where(User.username == args.username)):
                print("user exists", file=sys.stderr)
                return 2
            db.add(User(username=args.username, password_hash=security.hash_password(pw), is_admin=True))
            bump_acl_version(db)
            db.commit()
        print(f"admin {args.username} created")
    elif args.cmd == "seed-example":
        out = seed_example(factory, Path(args.credentials_out))
        print(json.dumps({"kb_id": out["kb_id"], "users": out["users"], "credentials_file": args.credentials_out},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
