from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.utils import timezone

from core.models import (
    ContentItem,
    CrawlRun,
    CrawlTarget,
    DictionaryTerm,
    ProductSource,
    RawDocument,
    Source,
    TermCandidate,
    TermMetricDaily,
    TextDocument,
)


DASHBOARD_CACHE_KEY = "dashboard:home:v1"
DASHBOARD_CACHE_SECONDS = 60

SOURCE_LABELS = {
    "musinsa": "무신사",
    "musinsa_used": "무신사 USED",
    "zigzag": "지그재그",
    "ably": "에이블리",
    "kream": "크림",
    "YOUTUBE": "유튜브",
    "youtube": "유튜브",
    "naver": "네이버",
}

SOURCE_ORDER = [
    "musinsa",
    "zigzag",
    "ably",
    "musinsa_used",
    "kream",
    "YOUTUBE",
    "naver",
]


def _source_label(source: Source) -> str:
    return SOURCE_LABELS.get(source.code, source.name or source.code)


def _percent(part: int, whole: int):
    if not whole:
        return None
    return round(part / whole * 100, 1)


def _build_dashboard_context() -> dict:
    now = timezone.now()
    today = timezone.localtime(now).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    week_ago = now - timedelta(days=7)
    stale_before = now - timedelta(hours=1)

    # 1. 큰 테이블의 상태별 count는 각각 한 번의 aggregate로 끝낸다.
    run_agg = CrawlRun.objects.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(status="SUCCESS")),
        failed=Count("id", filter=Q(status="FAILED")),
        running=Count("id", filter=Q(status="RUNNING")),
        today=Count("id", filter=Q(started_at__gte=today)),
        recent_total=Count("id", filter=Q(started_at__gte=week_ago)),
        recent_success=Count(
            "id",
            filter=Q(started_at__gte=week_ago, status="SUCCESS"),
        ),
        stale=Count(
            "id",
            filter=Q(status="RUNNING", started_at__lt=stale_before),
        ),
    )

    doc_agg = RawDocument.objects.aggregate(
        total=Count("id"),
        today=Count("id", filter=Q(collected_at__gte=today)),
        pending=Count("id", filter=Q(normalization_status="PENDING")),
        failed=Count("id", filter=Q(normalization_status="FAILED")),
    )

    # 2. 서로 다른 테이블의 전체 count는 단순 COUNT만 수행한다.
    term_candidates = TermCandidate.objects.count()
    dictionary_terms = DictionaryTerm.objects.count()
    product_sources = ProductSource.objects.count()
    content_items = ContentItem.objects.count()
    text_documents = TextDocument.objects.count()
    metrics = TermMetricDaily.objects.count()

    total_runs = run_agg["total"]
    success_runs = run_agg["success"]
    failed_runs = run_agg["failed"]

    stats = {
        "collected_today": doc_agg["today"],
        "success_rate": _percent(
            run_agg["recent_success"],
            run_agg["recent_total"],
        ),
        "success_rate_all": _percent(success_runs, total_runs),
        "normalize_pending": doc_agg["pending"],
        "normalize_failed": doc_agg["failed"],
        "term_candidates": term_candidates,
        "dictionary_terms": dictionary_terms,
        "raw_total": doc_agg["total"],
        "runs_today": run_agg["today"],
        "failed_runs": failed_runs,
        "running": run_agg["running"],
    }

    pipeline = [
        {
            "name": "수집",
            "table": "CrawlRun",
            "count": total_runs,
            "status": "success" if total_runs else "idle",
            "note": f"성공 {success_runs:,} · 실패 {failed_runs:,}",
        },
        {
            "name": "원본 적재",
            "table": "RawDocument / S3",
            "count": doc_agg["total"],
            "status": "success" if doc_agg["total"] else "idle",
            "note": "S3 원본 + 포인터",
        },
        {
            "name": "정규화",
            "table": "ProductSource / ContentItem",
            "count": product_sources + content_items,
            "status": "success" if (product_sources or content_items) else "idle",
            "note": f"상품 {product_sources:,} · 콘텐츠 {content_items:,}",
        },
        {
            "name": "텍스트 분석",
            "table": "TextDocument",
            "count": text_documents,
            "status": "success" if text_documents else "idle",
            "note": "댓글·리뷰 적재",
        },
        {
            "name": "트렌드 집계",
            "table": "TermMetricDaily",
            "count": metrics,
            "status": "success" if metrics else "idle",
            "note": "일자별 용어 지표",
        },
    ]

    pipeline_max = max((step["count"] for step in pipeline), default=0)
    for step in pipeline:
        step["pct"] = (
            round(step["count"] / pipeline_max * 100, 1)
            if pipeline_max
            else 0
        )

    # 3. 플랫폼별 통계도 GROUP BY 2회로 끝낸다.
    #    핵심: CrawlRun 전체를 Python for문으로 순회하지 않는다.
    run_by_source = {
        row["source_id"]: row
        for row in (
            CrawlRun.objects
            .values("source_id")
            .annotate(
                runs=Count("id"),
                failed=Count("id", filter=Q(status="FAILED")),
                last_run=Max("started_at"),
            )
        )
    }

    doc_by_source = {
        row["source_id"]: row["documents"]
        for row in (
            RawDocument.objects
            .values("source_id")
            .annotate(documents=Count("id"))
        )
    }

    source_rows = list(Source.objects.all().only("id", "code", "name"))
    source_map = {source.code: source for source in source_rows}

    ordered_sources = [
        source_map.pop(code)
        for code in SOURCE_ORDER
        if code in source_map
    ]
    ordered_sources.extend(
        source_map[code]
        for code in sorted(source_map)
    )

    sources = []
    for source in ordered_sources:
        run_stat = run_by_source.get(source.id, {})
        total = run_stat.get("runs", 0)
        failed = run_stat.get("failed", 0)

        if total == 0:
            status = "idle"
        elif failed / total >= 0.4:
            status = "danger"
        elif failed:
            status = "warning"
        else:
            status = "healthy"

        sources.append(
            {
                "name": _source_label(source),
                "code": source.code,
                "status": status,
                "count": doc_by_source.get(source.id, 0),
                "runs": total,
                "failed": failed,
                "success_rate": _percent(total - failed, total),
                "last_run": run_stat.get("last_run"),
            }
        )

    # 4. 화면에 필요한 실패 실행 8개만 읽는다.
    recent_errors = []
    failed_run_rows = (
        CrawlRun.objects
        .filter(status="FAILED")
        .select_related("source", "crawl_target")
        .only(
            "id",
            "started_at",
            "error_code",
            "error_message",
            "target",
            "source__code",
            "source__name",
            "crawl_target__name",
        )
        .order_by("-started_at")[:8]
    )

    for run in failed_run_rows:
        recent_errors.append(
            {
                "run_id": run.id,
                "time": (
                    timezone.localtime(run.started_at).strftime("%m-%d %H:%M")
                    if run.started_at
                    else "-"
                ),
                "source": _source_label(run.source) if run.source else "-",
                "target": (
                    run.crawl_target.name
                    if run.crawl_target
                    else (run.target or "-")
                ),
                "code": run.error_code or "UNKNOWN",
                "message": (run.error_message or "오류 메시지 없음")[:160],
            }
        )

    alerts = []
    if failed_runs and total_runs:
        failure_rate = round(failed_runs / total_runs * 100, 1)
        if failure_rate >= 20:
            alerts.append(
                {
                    "level": "danger",
                    "title": f"수집 실패율 {failure_rate}%",
                    "detail": (
                        f"전체 {total_runs:,}건 중 "
                        f"{failed_runs:,}건이 실패했습니다."
                    ),
                }
            )

    if run_agg["stale"]:
        alerts.append(
            {
                "level": "warning",
                "title": f"멈춘 실행 {run_agg['stale']}건",
                "detail": "1시간 넘게 RUNNING 상태입니다. 워커가 중단됐을 수 있습니다.",
            }
        )

    if not metrics:
        alerts.append(
            {
                "level": "warning",
                "title": "트렌드 지표 미집계",
                "detail": "term_metric_daily가 비어 있어 분석 화면이 채워지지 않습니다.",
            }
        )

    if not text_documents:
        alerts.append(
            {
                "level": "info",
                "title": "텍스트 분석 대기",
                "detail": "수집한 댓글이 아직 TextDocument로 적재되지 않았습니다.",
            }
        )

    target_agg = CrawlTarget.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )

    return {
        "stats": stats,
        "pipeline": pipeline,
        "sources": sources,
        "recent_errors": recent_errors,
        "alerts": alerts,
        "targets": target_agg,
        "generated_at": now,
    }


def get_dashboard_context(*, use_cache: bool = True) -> dict:
    """Return the home dashboard context.

    The page contains global counts over growing tables, so a short cache gives
    a large speed-up without making the admin dashboard noticeably stale.
    """
    if not use_cache:
        return _build_dashboard_context()

    cached = cache.get(DASHBOARD_CACHE_KEY)
    if cached is not None:
        return cached

    context = _build_dashboard_context()
    cache.set(
        DASHBOARD_CACHE_KEY,
        context,
        DASHBOARD_CACHE_SECONDS,
    )
    return context
