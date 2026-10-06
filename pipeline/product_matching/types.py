from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MatchEvidence:
    normalized_name_score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProductCandidate:
    product_id: int
    product_code: str | None
    product_name: str
    score: float
    evidence: MatchEvidence
    conflicts: list[str] = field(default_factory=list)


@dataclass
class ProductSourceMatch:
    product_source_id: int
    source_code: str
    source_name: str
    normalized_name: str | None
    candidates: list[ProductCandidate] = field(default_factory=list)


@dataclass
class BrandMatchingResult:
    brand_id: int
    brand_name: str
    product_count: int
    unmapped_count: int
    matches: list[ProductSourceMatch] = field(default_factory=list)
