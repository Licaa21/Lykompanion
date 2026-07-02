"""Lexical relevance retrieval for game/session memories (RAG-lite, no embeddings/DB - in
keeping with the flat-file design). User-scope memories are always injected in full; this
module only decides WHICH game/session memories make the prompt once a playthrough has
accumulated more than the cap. Scoring is a small TF-IDF-style term overlap against the
current conversation + game activity, with a share of the slots reserved for the most
recently saved facts (recent progress is what the conversation most likely refers to, even
when the wording doesn't overlap)."""

import math
import re

# How many of the `limit` slots go to the most recently saved memories regardless of their
# relevance score (as a fraction). The rest go to the best-scoring ones.
_RECENCY_SHARE = 0.34


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def select_relevant(memories: list[dict], query: str, limit: int) -> list[dict]:
    """Picks up to `limit` entries from `memories`, preserving their original order in the
    returned list. Reserved-recency slots are filled from the newest saved_at values; the
    remaining slots by lexical relevance to `query`. With a blank query, falls back to the
    most recent `limit` entries."""
    if limit <= 0 or len(memories) <= limit:
        return memories

    by_recency = sorted(memories, key=lambda m: m.get("saved_at") or "", reverse=True)
    if not query.strip():
        keep = {id(m) for m in by_recency[:limit]}
        return [m for m in memories if id(m) in keep]

    recency_slots = max(1, round(limit * _RECENCY_SHARE))
    keep = {id(m) for m in by_recency[:recency_slots]}

    # Document frequency over the candidate set, so common filler words ("the player", the
    # game's own name on every entry) stop dominating the overlap score.
    doc_tokens = {id(m): set(_tokenize(m.get("content") or "")) for m in memories}
    df: dict[str, int] = {}
    for tokens in doc_tokens.values():
        for t in tokens:
            df[t] = df.get(t, 0) + 1
    n_docs = len(memories)

    query_tokens = set(_tokenize(query))
    scored = []
    for m in memories:
        if id(m) in keep:
            continue
        overlap = query_tokens & doc_tokens[id(m)]
        score = sum(math.log(1 + n_docs / df[t]) for t in overlap)
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    for _, m in scored[: limit - len(keep)]:
        keep.add(id(m))
    # Any slots still free (few lexical matches) fall back to recency.
    for m in by_recency:
        if len(keep) >= limit:
            break
        keep.add(id(m))

    return [m for m in memories if id(m) in keep]
