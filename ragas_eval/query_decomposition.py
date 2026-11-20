"""Deterministic, reusable query decomposition (v0.9.1). No LLM, no network, no GT access.

Inputs: the user question and corpus metadata that is available at retrieval time (the list of source document names).
Output: the original query (always first, as fallback) plus sub-queries, each optionally scoped to one document.

Rules (generic, fixed before evaluation; no question-specific cases):
  R1 multi_question   split on ？ ? ； ; into independent questions (each >= 4 chars)
  R2 entity_split     "<X>和<Y>分别<rest>" with short X / Y (<= 12 chars, no punctuation) -> "<X><rest>", "<Y><rest>"
  R3 doc_scope        the question names a document (unique alias derived from file names) -> sub-query restricted
                      to that document, with the alias removed from the query text
  R4 doc_fanout       plural-enumeration cue without named entities ("几个/各个/多个 + 项目/课程/系统/案例/文档")
                      -> the original query scoped to each document (one sub-query per document)
If no rule fires (or anything fails), only the original query is returned.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

SPLIT = re.compile(r"[？?；;]")
ENTITY = re.compile(r"^(?P<x>[^，,。；;：:？?]{1,12}?)\s*和\s*(?P<y>[^，,。；;：:？?]{1,12}?)\s*分别(?P<rest>.+)$")
FANOUT = re.compile(r"(几个|各个|多个)\s*(项目|课程|系统|案例|文档)")


@dataclass
class SubQuery:
    text: str
    scope_doc: Optional[str] = None
    rule: str = "original"


@dataclass
class Decomposition:
    original: str
    subqueries: List[SubQuery] = field(default_factory=list)
    rules_fired: List[str] = field(default_factory=list)
    fallback: bool = False
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    @property
    def is_multi(self) -> bool:
        return len(self.subqueries) > 1

    def to_dict(self):
        d = asdict(self)
        d["is_multi"] = self.is_multi
        return d


def doc_aliases(doc_names: List[str]) -> Dict[str, str]:
    """alias -> document. Aliases: file stem, and '_'-separated parts that contain a digit or a parenthesis
    (e.g. '33课', '笔记(6)', '3_笔记(1)'). Aliases shared by several documents are dropped."""
    cand: Dict[str, set] = {}
    for d in doc_names:
        stem = re.sub(r"\.(md|pdf|docx)$", "", d)
        al = {stem}
        for part in re.split(r"[_\s]+", stem):
            if len(part) >= 3 and re.search(r"[\d(（]", part) and not re.fullmatch(r"\[?[\w.]+\.com\]?", part):
                al.add(part)
        for a in al:
            cand.setdefault(a, set()).add(d)
    return {a: next(iter(ds)) for a, ds in cand.items() if len(ds) == 1}


PRONOUN = re.compile(r"^(它们|它|其|该|这|那)")


def _standalone(seg: str) -> bool:
    """A split segment is only searched on its own if it can stand alone (>= 8 chars, no leading pronoun)."""
    return len(seg) >= 8 and not PRONOUN.match(seg)


def _strip(text: str, alias: str) -> str:
    t = text.replace("第" + alias, " ").replace(alias, " ")
    t = re.sub(r"^[\s第中的：:]+", "", t)
    return re.sub(r"\s+", " ", t).strip() or text


def decompose(question: str, doc_names: List[str]) -> Decomposition:
    t0 = time.perf_counter()
    dec = Decomposition(original=question, subqueries=[SubQuery(question)])
    try:
        parts = [p.strip() for p in SPLIT.split(question) if len(p.strip()) >= 4]
        if len(parts) > 1:
            dec.rules_fired.append("R1_multi_question")
        else:
            parts = [question.rstrip("？?").strip()]
        segs: List[SubQuery] = []
        for p in parts:
            m = ENTITY.match(p)
            if m:
                dec.rules_fired.append("R2_entity_split")
                segs += [SubQuery(m.group("x").strip() + m.group("rest"), rule="R2_entity_split"),
                         SubQuery(m.group("y").strip() + m.group("rest"), rule="R2_entity_split")]
            elif len(parts) > 1 and _standalone(p):
                segs.append(SubQuery(p, rule="R1_multi_question"))
        aliases = doc_aliases(doc_names)
        named = sorted({d for a, d in aliases.items() if a in question})
        # R3 is applied to the smallest unit that names the document: a split segment if any, else the whole question
        units = [s.text for s in segs] or [question]
        for d in named:
            als = sorted((a for a, dd in aliases.items() if dd == d), key=len, reverse=True)
            for u in units:
                hit = next((a for a in als if a in u), None)
                if hit:
                    segs.append(SubQuery(_strip(u, hit), scope_doc=d, rule="R3_doc_scope"))
            if not any(a in u for a in als for u in units):  # the alias sits in a dropped fragment: scope the whole question
                hit = next(a for a in als if a in question)
                segs.append(SubQuery(_strip(question, hit), scope_doc=d, rule="R3_doc_scope"))
        if named:
            dec.rules_fired.append("R3_doc_scope")
        if not named and FANOUT.search(question):
            dec.rules_fired.append("R4_doc_fanout")
            segs += [SubQuery(question, scope_doc=d, rule="R4_doc_fanout") for d in doc_names]
        seen = {(question, None)}
        for s in segs:
            if (s.text, s.scope_doc) not in seen:
                seen.add((s.text, s.scope_doc))
                dec.subqueries.append(s)
    except Exception as e:  # never fail retrieval because of decomposition
        dec.subqueries, dec.fallback, dec.error = [SubQuery(question)], True, type(e).__name__
    dec.rules_fired = sorted(set(dec.rules_fired))
    dec.fallback = dec.fallback or not dec.is_multi
    dec.elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
    return dec
