"""Chunk-level retrieval metrics and AND-of-OR evidence-slot coverage (pure functions, no I/O, no models).

Definitions (``top`` = ranked chunk IDs returned by a retriever, ``R`` = set of relevant chunk IDs, K = cut-off):

* Hit@K          1 if at least one relevant chunk is in the top-K, else 0.
* Recall@K       |R ∩ top-K| / |R|  — chunk-level recall over the annotated relevant chunks.
* MRR@K          1 / rank of the first relevant chunk within the top-K, 0 if none (reported as MRR@10).
* nDCG@K         binary gains: DCG = Σ_{i=1..K} rel_i / log2(i + 1); IDCG = the same sum for min(|R|, K) relevant
                 chunks placed first; nDCG = DCG / IDCG.
* SlotFullCov@K  1 if EVERY evidence slot is covered within the top-K. A slot is a list of alternative groups (OR);
                 a group is a list of evidence items (AND); an item is covered when any chunk it resolves to is in the
                 top-K. This is the "complete evidence" criterion for multi-evidence questions: hitting any one
                 chunk is not enough.
* SlotRecall@K   fraction of slots covered within the top-K.
* CompletionRank smallest K (≤ max_k) at which all slots are covered, else None.

NOT provided here on purpose: the legacy "Source Hit@K" of scripts/10_eval_retrieval.compute_source_hit (a top-k chunk
comes from the expected FILE). It is a file-level hit, not a recall; ``source_hit_at_k`` below exists only so reports
can show it side by side with an explicit name.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Set

# A resolved slot: List[group]; group: List[item]; item: Set[chunk_id] (any of them satisfies the item)
Item = Set[str]
Group = List[Item]
Slot = List[Group]


def hit_at_k(top: Sequence[str], relevant: Set[str], k: int) -> int:
    return int(bool(set(top[:k]) & relevant))


def recall_at_k(top: Sequence[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        raise ValueError("recall undefined without relevant chunks")
    return len(set(top[:k]) & relevant) / len(relevant)


def mrr_at_k(top: Sequence[str], relevant: Set[str], k: int = 10) -> float:
    for i, c in enumerate(top[:k], 1):
        if c in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(top: Sequence[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        raise ValueError("nDCG undefined without relevant chunks")
    dcg = sum(1.0 / math.log2(i + 1) for i, c in enumerate(top[:k], 1) if c in relevant)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / idcg


def item_covered(item: Item, got: Set[str]) -> bool:
    return bool(item & got)


def group_covered(group: Group, got: Set[str]) -> bool:
    return bool(group) and all(item_covered(it, got) for it in group)


def slot_covered(slot: Slot, got: Set[str]) -> bool:
    return any(group_covered(g, got) for g in slot)


def slot_fullcov_at_k(top: Sequence[str], slots: List[Slot], k: int) -> int:
    got = set(top[:k])
    return int(all(slot_covered(s, got) for s in slots))


def slot_recall_at_k(top: Sequence[str], slots: List[Slot], k: int) -> float:
    got = set(top[:k])
    return sum(slot_covered(s, got) for s in slots) / len(slots)


def completion_rank(top: Sequence[str], slots: List[Slot], max_k: int = 10) -> Optional[int]:
    for k in range(1, min(len(top), max_k) + 1):
        if slot_fullcov_at_k(top, slots, k):
            return k
    return None


def relevant_chunks(slots: List[Slot]) -> Set[str]:
    return {c for s in slots for g in s for it in g for c in it}


def failure_reason(top: Sequence[str], pool: Iterable[str], slots: List[Slot], k: int) -> Dict[str, object]:
    """Why a question is not fully covered at K.

    covered        all slots covered in the top-K
    truncation     every uncovered slot has a group whose evidence IS in the candidate pool, but ranked below K
    recall_failure every uncovered slot lacks evidence in the candidate pool (the retriever never found it)
    mixed          some uncovered slots are truncation, others recall failures
    """
    got, pool_set = set(top[:k]), set(pool)
    missing = [i for i, s in enumerate(slots) if not slot_covered(s, got)]
    if not missing:
        return {"reason": "covered", "missing_slots": []}
    kinds = ["truncation" if slot_covered(slots[i], pool_set) else "recall_failure" for i in missing]
    reason = kinds[0] if len(set(kinds)) == 1 else "mixed"
    return {"reason": reason, "missing_slots": missing, "slot_kinds": kinds}


def question_metrics(top: Sequence[str], slots: List[Slot], ks: Iterable[int] = (1, 3, 5, 10)) -> Dict[str, object]:
    rel = relevant_chunks(slots)
    m: Dict[str, object] = {}
    for k in ks:
        m[f"hit@{k}"] = hit_at_k(top, rel, k)
        m[f"recall@{k}"] = round(recall_at_k(top, rel, k), 4)
        m[f"ndcg@{k}"] = round(ndcg_at_k(top, rel, k), 4)
        m[f"slot_fullcov@{k}"] = slot_fullcov_at_k(top, slots, k)
        m[f"slot_recall@{k}"] = round(slot_recall_at_k(top, slots, k), 4)
    m["mrr@10"] = round(mrr_at_k(top, rel, 10), 4)
    m["completion_rank"] = completion_rank(top, slots)
    return m


def aggregate(rows: List[Dict[str, object]]) -> Dict[str, float]:
    if not rows:
        return {}
    keys = [k for k, v in rows[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "completion_rank"]
    return {k: round(sum(float(r[k]) for r in rows) / len(rows), 4) for k in keys} | {"n": len(rows)}


def source_hit_at_k(top_sources: Sequence[str], expected_source: str, k: int) -> int:
    """LEGACY file-level metric (scripts/10 compute_source_hit). Shown only under this explicit name; NOT recall."""
    return int(bool(expected_source) and any(expected_source in s for s in top_sources[:k]))
