# analysis/metrics/common.py
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import exp, log
from typing import Iterable

from django.db import connection
from core.models import DictionaryTerm, Source, TermAlias, TermMetricDaily

METRIC_VERSION = "feedit-unified-text-v2"

SOURCE_WEIGHTS = {
    "musinsa": 0.18,
    "zigzag": 0.18,
    "ably": 0.16,
    "musinsa_used": 0.08,
    "kream": 0.08,
    "YOUTUBE": 0.24,
    "youtube": 0.24,
    "naver": 0.08,
}

def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))

def safe_rate(numerator, denominator):
    denominator = float(denominator or 0)
    if denominator <= 0:
        return None
    return round(float(numerator or 0) / denominator * 100.0, 4)

def log_compress(value):
    return log(1.0 + max(0.0, float(value or 0)))

def momentum_score(ma7, ma28):
    ma7, ma28 = float(ma7 or 0), float(ma28 or 0)
    if ma28 <= 0:
        return 50.0 if ma7 <= 0 else 100.0
    return clamp(100.0 / (1.0 + exp(-6.0 * (ma7 / ma28 - 1.0))))

def trend_temperature_score(level, momentum):
    return round(clamp(0.6 * float(level or 0) + 0.4 * float(momentum or 0)), 4)

def moving_average(values: Iterable, window: int) -> float:
    rows = [float(v or 0) for v in values]
    tail = rows[-window:]
    return sum(tail) / len(tail) if tail else 0.0

def dictfetchall(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def execute_query(sql: str, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return dictfetchall(cursor)

def normalize_term_key(value):
    return " ".join(str(value or "").strip().casefold().split())

def build_term_lookup():
    lookup = {}
    for term in DictionaryTerm.objects.filter(status="ACTIVE"):
        for value in (term.canonical_name, term.normalized_name):
            key = normalize_term_key(value)
            if key:
                lookup[key] = term
    for alias in TermAlias.objects.select_related("term").filter(term__status="ACTIVE"):
        for value in (alias.alias, alias.normalized_alias):
            key = normalize_term_key(value)
            if key:
                lookup.setdefault(key, alias.term)
    return lookup

def source_lookup():
    return {source.id: source for source in Source.objects.all()}

def ensure_metric(*, term, source, metric_date: date, metric_version: str = METRIC_VERSION):
    metric = TermMetricDaily.objects.filter(
        term=term, source=source, metric_date=metric_date, metric_version=metric_version
    ).first()
    if metric is not None:
        return metric
    return TermMetricDaily.objects.create(
        term=term, source=source, metric_date=metric_date, metric_version=metric_version
    )

def merge_json(base, patch):
    result = dict(base or {})
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_json(result[key], value)
        else:
            result[key] = value
    return result

def percentile_rank(values_by_id):
    if not values_by_id:
        return {}
    grouped = defaultdict(list)
    for obj_id, value in values_by_id.items():
        grouped[float(value)].append(obj_id)
    ordered_values = sorted(grouped)
    n = len(values_by_id)
    result, seen = {}, 0
    for value in ordered_values:
        ids = grouped[value]
        group_size = len(ids)
        avg_rank = seen + (group_size + 1) / 2
        pct = 100.0 if n == 1 else (avg_rank - 1) / (n - 1) * 100.0
        for obj_id in ids:
            result[obj_id] = round(clamp(pct), 4)
        seen += group_size
    return result
