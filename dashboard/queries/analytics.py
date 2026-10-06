from __future__ import annotations

from collections import Counter
from datetime import date

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate

from core.models import (
    ContentItem,
    ContentSnapshot,
    DictionaryTerm,
    ProductSource,
    ProductSourceSnapshot,
    ResaleSnapshot,
    Source,
    TermAlias,
    TermAssocDaily,
    TermMetricDaily,
    TextDocument,
)
from dashboard.views._common import _qs_without_page
from dashboard.views.helpers import (
    _get_comment_summary,
    _get_intent_stats,
    _get_polarity_stats,
    _get_slot_combinations,
    _get_slot_stats,
    _get_tag_count_stats,
    _get_term_pairs,
    ordered_sources,
)


PRODUCT_METRIC_ORDER_CHOICES = [
    ("recent", "최근 확인순"),
    ("rank", "랭킹순"),
    ("discount", "할인율 높은순"),
    ("review", "리뷰 많은순"),
    ("price_desc", "판매가 높은순"),
    ("price_asc", "판매가 낮은순"),
]
METRIC_VERSION = None

SUMMARY_CACHE_SECONDS = 60


def _float_or_none(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value, digits=1):
    value = _float_or_none(value)
    return None if value is None else round(value, digits)


def _rate_to_percent(value):
    value = _float_or_none(value)
    if value is None:
        return None
    if abs(value) <= 1:
        value *= 100
    return round(value, 1)


def _dict_get(data, *keys, default=None):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _signal_score(metrics, signal_name):
    return _round_or_none(
        _dict_get(metrics, "signals", signal_name, "score")
    )


def _search_value(metrics, platform, field):
    return _dict_get(
        metrics,
        "search",
        "platforms",
        platform,
        field,
    )


def _avg(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _lifecycle_stage(*, level, momentum):
    level = _float_or_none(level) or 0.0
    momentum = _float_or_none(momentum)
    momentum = 50.0 if momentum is None else momentum

    if momentum < 40:
        return "COOLING"
    if level >= 70 and momentum < 60:
        return "MATURE"
    if level >= 60 and momentum >= 60:
        return "GROWING"
    if momentum >= 60:
        return "EMERGING"
    return "DISCOVERY"


def _term_detail_label(term):
    relation_map = {
        "ITEM": "item",
        "STYLE": "style",
        "DETAIL": "detail",
        "MATERIAL": "material",
        "COLOR": "color",
        "TPO": "tpo",
    }
    relation_name = relation_map.get(
        str(getattr(term, "term_type", "") or "").upper()
    )
    if not relation_name:
        return "-"

    try:
        obj = getattr(term, relation_name, None)
    except Exception:
        return "-"

    if obj is None:
        return "-"

    for field in (
        "detail_type",
        "item_type",
        "style_type",
        "material_type",
        "color_type",
        "tpo_type",
        "subtype",
        "sub_type",
        "category",
        "group",
        "type",
    ):
        value = getattr(obj, field, None)
        if value in (None, ""):
            continue

        display = getattr(obj, f"get_{field}_display", None)
        if callable(display):
            try:
                displayed = display()
                if displayed:
                    return str(displayed)
            except Exception:
                pass

        return str(value)

    return "-"


def _metric_dates():
    return list(
        TermMetricDaily.objects
        .filter(metric_version=METRIC_VERSION)
        .values_list("metric_date", flat=True)
        .distinct()
        .order_by("-metric_date")
    )


def _term_types():
    return list(
        DictionaryTerm.objects
        .exclude(term_type__isnull=True)
        .exclude(term_type="")
        .values_list("term_type", flat=True)
        .distinct()
        .order_by("term_type")
    )


def get_term_metrics_context(request):
    search_query = request.GET.get("q", "").strip()
    selected_type = request.GET.get("term_type", "").strip()
    selected_date = request.GET.get("metric_date", "").strip()

    dates = _metric_dates()

    term_qs = (
        DictionaryTerm.objects
        .select_related(
            "item",
            "style",
            "detail",
            "material",
            "color",
            "tpo",
        )
        .order_by("canonical_name")
    )

    if selected_type:
        term_qs = term_qs.filter(term_type=selected_type)

    if search_query:
        term_qs = term_qs.filter(
            Q(canonical_name__icontains=search_query)
            | Q(normalized_name__icontains=search_query)
        )

    metric_qs = (
        TermMetricDaily.objects
        .filter(metric_version=METRIC_VERSION)
        .select_related("term", "source")
    )

    parsed_date = None
    if selected_date:
        try:
            parsed_date = date.fromisoformat(selected_date)
            metric_qs = metric_qs.filter(metric_date=parsed_date)
        except ValueError:
            pass

    metrics_by_term = {}
    for metric in metric_qs.order_by(
        "term_id",
        "-metric_date",
        "source_id",
    ):
        current = metrics_by_term.get(metric.term_id)
        if current is None:
            metrics_by_term[metric.term_id] = metric
            continue

        if (
            metric.metric_date == current.metric_date
            and metric.source_id is None
            and current.source_id is not None
        ):
            metrics_by_term[metric.term_id] = metric

    # N+1 제거:
    # 기존에는 term마다 distinct source COUNT 쿼리를 실행했다.
    metric_dates_by_term = {
        metric.term_id: metric.metric_date
        for metric in metrics_by_term.values()
    }

    source_counts = {}
    if metric_dates_by_term:
        count_qs = (
            TermMetricDaily.objects
            .filter(
                metric_version=METRIC_VERSION,
                term_id__in=metric_dates_by_term.keys(),
                source__isnull=False,
            )
            .values("term_id", "metric_date")
            .annotate(source_count=Count("source_id", distinct=True))
        )
        source_counts = {
            (row["term_id"], row["metric_date"]): row["source_count"]
            for row in count_qs
        }

    rows = []
    for term in term_qs:
        metric = metrics_by_term.get(term.id)

        rows.append(
            {
                "id": term.id,
                "term_text": term.canonical_name,
                "term_type": term.term_type,
                "term_detail": _term_detail_label(term),
                "metric_date": metric.metric_date if metric else None,
                "mention_count": metric.mention_count if metric else 0,
                "document_count": metric.document_count if metric else 0,
                "source_count": (
                    source_counts.get(
                        (term.id, metric.metric_date),
                        0,
                    )
                    if metric
                    else 0
                ),
                "growth_rate": (
                    _round_or_none(metric.momentum, 2)
                    if metric
                    else None
                ),
                "trend_score": (
                    _round_or_none(metric.trend_temperature, 2)
                    if metric
                    else None
                ),
            }
        )

    summary_key = f"dashboard:analytics:term-summary:{METRIC_VERSION}"
    summary = cache.get(summary_key)

    if summary is None:
        metric_base = TermMetricDaily.objects.filter(
            metric_version=METRIC_VERSION
        )
        metric_summary = metric_base.aggregate(
            metric_rows=Count("id"),
            measured_terms=Count("term_id", distinct=True),
        )
        summary = {
            **metric_summary,
            "terms": DictionaryTerm.objects.count(),
            "aliases": TermAlias.objects.count(),
            "dates": len(dates),
        }
        cache.set(summary_key, summary, SUMMARY_CACHE_SECONDS)

    return {
        "page_title": "용어별 지표",
        "page_description": (
            "DictionaryTerm 기준으로 수집·분석된 용어별 지표를 확인합니다."
        ),
        "summary": summary,
        "rows": rows,
        "filtered_count": len(rows),
        "term_types": _term_types(),
        "dates": dates,
        "selected_type": selected_type,
        "selected_date": selected_date,
        "search_query": search_query,
    }


def get_trend_metrics_context(request):
    """
    트렌드 지표 대시보드.

    전체 TermMetricDaily를 Python으로 materialize하지 않는다.
    화면에 필요한 집계와 TOP N만 DB에서 계산한다.
    """
    latest_metric_date = (
        TermMetricDaily.objects
        .filter(metric_version=METRIC_VERSION)
        .aggregate(latest=Max("metric_date"))
        ["latest"]
    )

    metric_qs = TermMetricDaily.objects.filter(
        metric_version=METRIC_VERSION,
    )
    assoc_qs = TermAssocDaily.objects.filter(
        metric_version=METRIC_VERSION,
    )

    if latest_metric_date:
        metric_qs = metric_qs.filter(metric_date=latest_metric_date)
        assoc_qs = assoc_qs.filter(metric_date=latest_metric_date)

    # source=NULL은 전체 플랫폼 통합 metric.
    all_metric_qs = metric_qs.filter(source__isnull=True)

    # 최신일에 ALL row가 없을 때만 전체 source row로 fallback.
    summary_qs = (
        all_metric_qs
        if all_metric_qs.exists()
        else metric_qs
    )

    cache_key = (
        f"dashboard:analytics:trend-overview:"
        f"{METRIC_VERSION}:{latest_metric_date or 'none'}:v2"
    )
    cached = cache.get(cache_key)

    if cached is None:
        top_trend = list(
            summary_qs
            .select_related("term")
            .exclude(trend_temperature__isnull=True)
            .order_by(
                "-trend_temperature",
                "-level",
                "term__canonical_name",
            )[:12]
        )

        max_trend = max(
            (
                float(row.trend_temperature)
                for row in top_trend
                if row.trend_temperature is not None
            ),
            default=0.0,
        )

        for row in top_trend:
            score = float(row.trend_temperature or 0)
            row.trend_score = score
            row.pct = (
                round(score / max_trend * 100, 1)
                if max_trend
                else 0
            )

        top_mention = list(
            summary_qs
            .select_related("term")
            .order_by(
                "-mention_count",
                "term__canonical_name",
            )[:12]
        )

        max_mention = max(
            (int(row.mention_count or 0) for row in top_mention),
            default=0,
        )

        for row in top_mention:
            row.pct = (
                round(
                    int(row.mention_count or 0)
                    / max_mention
                    * 100,
                    1,
                )
                if max_mention
                else 0
            )

        by_type = list(
            summary_qs
            .values("term__term_type")
            .annotate(n=Count("term_id", distinct=True))
            .order_by("-n", "term__term_type")
        )

        top_assoc = list(
            assoc_qs
            .select_related("source_term", "target_term")
            .order_by(
                "-cooccurrence_count",
                "-association_percentile",
                "-pmi",
                "source_term__canonical_name",
            )[:20]
        )

        # 기존 template 필드명과 호환.
        # association_score = PMI
        # confidence = association percentile
        for row in top_assoc:
            row.association_score = row.pmi
            row.confidence = row.association_percentile

        cached = {
            "summary": {
                "metric_rows": metric_qs.count(),
                "assoc_rows": assoc_qs.count(),
                "terms": DictionaryTerm.objects.count(),
                "documents": TextDocument.objects.count(),
                "content_items": ContentItem.objects.count(),
            },
            "top_trend": top_trend,
            "top_mention": top_mention,
            "by_type": by_type,
            "top_assoc": top_assoc,
        }

        cache.set(
            cache_key,
            cached,
            SUMMARY_CACHE_SECONDS,
        )

    return {
        "page_title": "트렌드 지표",
        "page_description": (
            "최신 집계 기준으로 용어별 트렌드 강도와 "
            "언급량·연관 용어를 확인합니다."
        ),
        "summary": cached["summary"],
        "latest_date": latest_metric_date,
        "top_trend": cached["top_trend"],
        "top_mention": cached["top_mention"],
        "by_type": cached["by_type"],
        "top_assoc": cached["top_assoc"],

        # 기존 view / 공통 template 호환.
        # 전체 metric queryset을 다시 넘기지 않는다.
        "rows": [],
        "filtered_count": 0,
    }


def _latest_snapshot_subquery(field):
    return Subquery(
        ProductSourceSnapshot.objects
        .filter(product_source_id=OuterRef("pk"))
        .order_by("-observed_at", "-id")
        .values(field)[:1]
    )


def get_product_metrics_context(request):
    selected_source = request.GET.get("source", "").strip()
    selected_order = request.GET.get("order", "recent").strip()
    q = request.GET.get("q", "").strip()

    queryset = (
        ProductSource.objects
        .select_related("source", "source_brand")
        .annotate(
            latest_snapshot_id=_latest_snapshot_subquery("id"),
            latest_rank=_latest_snapshot_subquery("rank_position"),
            latest_discount=_latest_snapshot_subquery("discount_rate"),
            latest_review_count=_latest_snapshot_subquery("review_count"),
            latest_sale_price=_latest_snapshot_subquery("sale_price"),
            latest_snapshot_observed_at=_latest_snapshot_subquery(
                "observed_at"
            ),
        )
    )

    if selected_source:
        queryset = queryset.filter(source_id=selected_source)

    if q:
        queryset = queryset.filter(
            Q(source_name__icontains=q)
            | Q(normalized_name__icontains=q)
            | Q(source_product_id__icontains=q)
            | Q(source_brand__name__icontains=q)
        )

    order_map = {
        "rank": ("latest_rank",),
        "discount": ("-latest_discount",),
        "review": ("-latest_review_count",),
        "price_desc": ("-latest_sale_price",),
        "price_asc": ("latest_sale_price",),
    }

    if selected_order in order_map:
        queryset = (
            queryset
            .exclude(latest_snapshot_id__isnull=True)
            .order_by(*order_map[selected_order], "-id")
        )
    else:
        queryset = queryset.order_by("-last_seen_at", "-id")

    paginator = Paginator(queryset, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = list(page_obj.object_list)

    # 페이지의 30개 상품에 대해서만 최신 snapshot을 한 번에 가져온다.
    snapshot_ids = [
        row.latest_snapshot_id
        for row in rows
        if row.latest_snapshot_id
    ]
    snapshots_by_id = {
        snapshot.id: snapshot
        for snapshot in ProductSourceSnapshot.objects.filter(
            id__in=snapshot_ids
        )
    }

    for row in rows:
        latest = snapshots_by_id.get(row.latest_snapshot_id)
        # 기존 template/helper 호환을 위해 두 이름 모두 제공.
        row.latest_snapshot = latest
        row.latest_product_snapshot = latest

    summary_key = "dashboard:analytics:product-metrics-summary:v1"
    summary = cache.get(summary_key)
    if summary is None:
        snapshot_summary = ProductSourceSnapshot.objects.aggregate(
            snapshot_rows=Count("id"),
            with_snapshot=Count("product_source_id", distinct=True),
            latest_observed=Max("observed_at"),
        )
        summary = {
            "product_source": ProductSource.objects.count(),
            **snapshot_summary,
            "resale_rows": ResaleSnapshot.objects.count(),
        }
        cache.set(summary_key, summary, SUMMARY_CACHE_SECONDS)

    return {
        "page_title": "상품별 지표",
        "page_description": (
            "플랫폼 상품의 최신 가격·할인·평점·순위를 조회합니다."
        ),
        "summary": summary,
        "sources": ordered_sources(),
        "order_choices": PRODUCT_METRIC_ORDER_CHOICES,
        "rows": rows,
        "page_obj": page_obj,
        "filtered_count": paginator.count,
        "selected_source": selected_source,
        "selected_order": selected_order,
        "search_query": q,
        "qs": _qs_without_page(request),
    }


def get_product_snapshot_context(request):
    selected_source = request.GET.get("source", "").strip()
    q = request.GET.get("q", "").strip()

    queryset = (
        ProductSourceSnapshot.objects
        .select_related(
            "product_source",
            "product_source__source",
        )
        .order_by("-observed_at", "-id")
    )

    if selected_source:
        queryset = queryset.filter(
            product_source__source_id=selected_source
        )

    if q:
        queryset = queryset.filter(
            Q(product_source__source_name__icontains=q)
            | Q(product_source__source_product_id__icontains=q)
        )

    paginator = Paginator(queryset, 40)
    page_obj = paginator.get_page(request.GET.get("page"))

    cache_key = "dashboard:analytics:snapshot-summary:v1"
    cached = cache.get(cache_key)

    if cached is None:
        snapshot_summary = ProductSourceSnapshot.objects.aggregate(
            snapshot_rows=Count("id"),
            observed_days=Count(
                TruncDate("observed_at"),
                distinct=True,
            ),
            latest_observed=Max("observed_at"),
        )

        daily_counts = list(
            ProductSourceSnapshot.objects
            .annotate(day=TruncDate("observed_at"))
            .values("day")
            .annotate(n=Count("id"))
            .order_by("-day")[:14]
        )

        cached = {
            "summary": {
                **snapshot_summary,
                "resale_rows": ResaleSnapshot.objects.count(),
                "content_rows": ContentSnapshot.objects.count(),
            },
            "daily_counts": daily_counts,
        }
        cache.set(cache_key, cached, SUMMARY_CACHE_SECONDS)

    return {
        "page_title": "상품 스냅샷",
        "page_description": (
            "관측 시점별로 쌓인 상품 지표 원본을 조회합니다."
        ),
        "summary": cached["summary"],
        "daily_counts": cached["daily_counts"],
        "sources": ordered_sources(),
        "rows": page_obj.object_list,
        "page_obj": page_obj,
        "filtered_count": paginator.count,
        "selected_source": selected_source,
        "search_query": q,
        "qs": _qs_without_page(request),
    }


def get_text_comment_metrics_context():
    summary = _get_comment_summary()
    intent_stats = _get_intent_stats()
    slot_stats = _get_slot_stats()
    polarity_stats = _get_polarity_stats()
    tag_count_stats = _get_tag_count_stats()
    slot_combinations = _get_slot_combinations(limit=20)
    term_pairs = _get_term_pairs(limit=50)

    import json

    return {
        "page_title": "댓글 데이터 분석",
        "page_description": (
            "댓글의 의도·패션 속성·극성·복합 태그와 "
            "동시 등장 용어를 분석합니다."
        ),
        "summary": summary,
        "intent_stats": intent_stats,
        "slot_stats": slot_stats,
        "polarity_stats": polarity_stats,
        "tag_count_stats": tag_count_stats,
        "slot_combinations": slot_combinations,
        "term_pairs": term_pairs,
        "intent_stats_json": json.dumps(
            intent_stats, ensure_ascii=False, default=str
        ),
        "slot_stats_json": json.dumps(
            slot_stats, ensure_ascii=False, default=str
        ),
        "polarity_stats_json": json.dumps(
            polarity_stats, ensure_ascii=False, default=str
        ),
        "tag_count_stats_json": json.dumps(
            tag_count_stats, ensure_ascii=False, default=str
        ),
    }


def get_product_snapshot_detail(snapshot_id):
    return (
        ProductSourceSnapshot.objects
        .select_related(
            "product_source",
            "product_source__source",
        )
        .get(pk=snapshot_id)
    )
