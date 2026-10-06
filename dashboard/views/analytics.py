from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from dashboard.queries.analytics import (
    get_product_metrics_context,
    get_product_snapshot_context,
    get_product_snapshot_detail,
    get_term_metrics_context,
    get_text_comment_metrics_context,
    get_trend_metrics_context,
)


@login_required(login_url="/admin-dashboard/login/")
def term_metrics(request):
    return render(
        request,
        "dashboard/analytics/term_metrics.html",
        get_term_metrics_context(request),
    )


@login_required(login_url="/admin-dashboard/login/")
def trend_metrics(request):
    return render(
        request,
        "dashboard/trend/trend_signals.html",
        get_trend_metrics_context(request),
    )


@login_required(login_url="/admin-dashboard/login/")
def product_metrics(request):
    return render(
        request,
        "dashboard/analytics/product_metrics.html",
        get_product_metrics_context(request),
    )


@login_required(login_url="/admin-dashboard/login/")
def product_snapshot(request):
    return render(
        request,
        "dashboard/analytics/product_snapshot.html",
        get_product_snapshot_context(request),
    )


@login_required(login_url="/admin-dashboard/login/")
def text_comment_metrics(request):
    return render(
        request,
        "dashboard/analytics/text_comments.html",
        get_text_comment_metrics_context(),
    )


@login_required(login_url="/admin-dashboard/login/")
def product_snapshot_detail(request, snapshot_id: int):
    try:
        snapshot = get_product_snapshot_detail(snapshot_id)
    except Exception as exc:
        from core.models import ProductSourceSnapshot
        if isinstance(exc, ProductSourceSnapshot.DoesNotExist):
            raise Http404("상품 스냅샷을 찾을 수 없습니다.") from exc
        raise

    return render(
        request,
        "dashboard/analytics/product_snapshot_detail.html",
        {
            "page_title": "상품 스냅샷 상세",
            "page_description": "선택한 상품 스냅샷의 상세 데이터를 확인합니다.",
            "snapshot": snapshot,
            "product_source": snapshot.product_source,
        },
    )
