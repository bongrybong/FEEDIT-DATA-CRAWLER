from .types import MatchEvidence


def calculate_score(evidence: MatchEvidence) -> float:
    if evidence.normalized_name_score is None:
        return 0.0
    return round(float(evidence.normalized_name_score), 6)
