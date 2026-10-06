from __future__ import annotations

import re
from difflib import SequenceMatcher

from .types import MatchEvidence


_SPACE_RE = re.compile(r"\\s+")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-z가-힣]+")


def normalize_for_match(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).strip().lower()
    value = _NON_WORD_RE.sub(" ", value)
    value = _SPACE_RE.sub(" ", value)
    return value.strip()


def token_set(value: str | None) -> set[str]:
    value = normalize_for_match(value)
    return {x for x in value.split(" ") if x} if value else set()


def jaccard_similarity(left: str | None, right: str | None) -> float:
    a, b = token_set(left), token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def sequence_similarity(left: str | None, right: str | None) -> float:
    a, b = normalize_for_match(left), normalize_for_match(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def compare_normalized_name(
    source_name: str | None,
    candidate_names: list[str | None],
) -> MatchEvidence:
    source_value = normalize_for_match(source_name)
    if not source_value:
        return MatchEvidence(
            normalized_name_score=None,
            details={"reason": "source normalized_name 없음"},
        )

    best_score = 0.0
    best_name = None
    best_method = None

    for candidate_name in candidate_names:
        candidate_value = normalize_for_match(candidate_name)
        if not candidate_value:
            continue

        if source_value == candidate_value:
            score, method = 1.0, "EXACT"
        else:
            jaccard = jaccard_similarity(source_name, candidate_name)
            sequence = sequence_similarity(source_name, candidate_name)
            score = max(jaccard, sequence)
            method = "TOKEN_JACCARD" if jaccard >= sequence else "SEQUENCE"

        if score > best_score:
            best_score = score
            best_name = candidate_name
            best_method = method

    return MatchEvidence(
        normalized_name_score=best_score,
        details={"matched_name": best_name, "method": best_method},
    )
