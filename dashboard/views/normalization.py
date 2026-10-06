from __future__ import annotations

import boto3
import json
import logging
import os
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
import time
import traceback
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    BooleanField,
    Case,
    Count,
    Max,
    Min,
    Q,
    Value,
    When,
)
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Brand,
    BrandSource,
    Category,
    CategorySource,
    ContentItem,
    ContentProfile,
    ContentSnapshot,
    CrawlRun,
    CrawlTarget,
    DictionaryTerm,
    Product,
    ProductSource,
    ProductTerm,
    ProductSourceSnapshot,
    RawDocument,
    ResaleSnapshot,
    Source,
    Style,
    TermAlias,
    TermAssocDaily,
    TermCandidate,
    TermMetricDaily,
    TextDocument,
)

from .helpers import (
    attach_latest_content_snapshot,
    attach_latest_product_snapshot,
    find_source,
    ordered_sources,
    qs_without,
    qs_without_page,
    source_label,
)

from dashboard.queries.normalization import (
    find_mapping_products,
    get_product_mapping_context,
    get_product_source_detail,
    get_product_detail_context,
)


def _normalization_summary():
    """RawDocument 정규화 상태 요약.

    상태별로 따로 count()를 돌리면 같은 테이블을 다섯 번 훑는다.
    filter 조건부 집계로 한 번에 처리한다.
    """

    agg = RawDocument.objects.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(normalization_status="SUCCESS")),
        failed=Count("id", filter=Q(normalization_status="FAILED")),
        pending=Count("id", filter=Q(normalization_status="PENDING")),
        processing=Count("id", filter=Q(normalization_status="PROCESSING")),
    )

    return {
        **agg,
        "product_source": ProductSource.objects.count(),
        "product": Product.objects.count(),
        "content_item": ContentItem.objects.count(),
    }


@login_required(login_url="/admin-dashboard/login/")
def normalized_products(request):
    """FEEDIT 브랜드 중심 상품 매핑 관리."""
    return render(
        request,
        "dashboard/normalization/products.html",
        get_product_mapping_context(request),
    )


@login_required(login_url="/admin-dashboard/login/")
@require_GET
def mapping_product_search(request):
    """Product Finder AJAX 검색. 브랜드 전체 상품을 선로딩하지 않고 최대 30개만 반환한다."""
    brand_id = request.GET.get("brand", "").strip()
    q = request.GET.get("q", "").strip()
    scope = request.GET.get("scope", "all").strip().lower()
    category_id = request.GET.get("category_id", "").strip()
    root_category_id = request.GET.get("root_category_id", "").strip()

    if not brand_id.isdigit():
        return JsonResponse({"results": []})

    products = find_mapping_products(
        int(brand_id),
        q=q,
        limit=30,
        scope=scope,
        category_id=int(category_id) if category_id.isdigit() else None,
        root_category_id=(
            int(root_category_id)
            if root_category_id.isdigit()
            else None
        ),
    )

    return JsonResponse({
        "results": [
            {
                "id": product.id,
                "name": (
                    product.normalized_name
                    or product.canonical_name
                    or f"Product {product.id}"
                ),
                "thumbnail_url": product.display_thumbnail_url,
                "category": (
                    product.category.name
                    if getattr(product, "category", None)
                    else None
                ),
            }
            for product in products
        ]
    })


@login_required(login_url="/admin-dashboard/login/")
def product_source_detail(request, product_source_id):
    """ProductSource 내부 수집/정규화/매핑 상세."""
    return render(
        request,
        "dashboard/normalization/product_source_detail.html",
        get_product_source_detail(product_source_id),
    )



@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def update_product_source_normalization(request, product_source_id):
    if request.method != "POST":
        raise Http404

    product_source = (
        ProductSource.objects
        .select_for_update()
        .filter(pk=product_source_id)
        .first()
    )
    if product_source is None:
        raise Http404

    normalized_name = str(request.POST.get("normalized_name") or "").strip()
    style_no = str(request.POST.get("style_no") or "").strip()
    gender_scope = str(request.POST.get("gender_scope") or "").strip()
    season = str(request.POST.get("season") or "").strip()
    season_year_raw = str(request.POST.get("season_year") or "").strip()

    if not normalized_name:
        messages.error(request, "정규화 상품명은 비워둘 수 없습니다.")
        return redirect("dashboard:product_source_detail", product_source_id=product_source.id)

    season_year = None
    if season_year_raw:
        if not season_year_raw.isdigit():
            messages.error(request, "시즌 연도는 숫자로 입력해주세요.")
            return redirect("dashboard:product_source_detail", product_source_id=product_source.id)
        season_year = int(season_year_raw)

    product_source.normalized_name = normalized_name
    product_source.style_no = style_no or None
    product_source.gender_scope = gender_scope or None
    product_source.season = season or None
    product_source.season_year = season_year
    product_source.save(update_fields=[
        "normalized_name",
        "style_no",
        "gender_scope",
        "season",
        "season_year",
        "updated_at",
    ])

    messages.success(request, "ProductSource 정규화 정보를 수정했습니다.")
    return redirect("dashboard:product_source_detail", product_source_id=product_source.id)


TERM_TYPE_DEFAULT_RELATION = {
    "ITEM": ProductTerm.RelationType.HAS_ITEM,
    "MATERIAL": ProductTerm.RelationType.HAS_MATERIAL,
    "COLOR": ProductTerm.RelationType.HAS_COLOR,
    "STYLE": ProductTerm.RelationType.HAS_STYLE,
    "TPO": ProductTerm.RelationType.HAS_TPO,
    "DETAIL": ProductTerm.RelationType.HAS_DETAIL,
}

DETAIL_RELATIONS = {
    ProductTerm.RelationType.HAS_DETAIL,
    ProductTerm.RelationType.HAS_FIT,
    ProductTerm.RelationType.HAS_SILHOUETTE,
    ProductTerm.RelationType.HAS_NECKLINE,
    ProductTerm.RelationType.HAS_SLEEVE,
    ProductTerm.RelationType.HAS_LENGTH,
    ProductTerm.RelationType.HAS_SHAPE,
}

@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def add_product_source_term(
    request,
    product_source_id,
):
    if request.method != "POST":
        raise Http404

    product_source = (
        ProductSource.objects
        .select_for_update()
        .filter(pk=product_source_id)
        .first()
    )

    if product_source is None:
        raise Http404

    term_type = str(
        request.POST.get("term_type")
        or ""
    ).strip().upper()

    term_id = str(
        request.POST.get("term_id")
        or ""
    ).strip()

    term_name = str(
        request.POST.get("term_name")
        or ""
    ).strip()

    relation_type = str(
        request.POST.get("relation_type")
        or ""
    ).strip().upper()

    valid_term_types = {
        value
        for value, _label
        in DictionaryTerm.TermType.choices
    }

    if term_type not in valid_term_types:
        messages.error(
            request,
            "올바른 Term 유형을 선택해주세요.",
        )
        return redirect(
            "dashboard:product_source_detail",
            product_source_id=product_source.id,
        )

    # ========================================================
    # DictionaryTerm 찾기
    #
    # 1순위:
    # 검색 UI에서 선택한 실제 DictionaryTerm ID
    #
    # 2순위:
    # 기존 term_name 입력 방식
    # ========================================================

    term = None

    if term_id.isdigit():
        term = (
            DictionaryTerm.objects
            .filter(
                pk=int(term_id),
                term_type=term_type,
                status=DictionaryTerm.Status.ACTIVE,
            )
            .first()
        )

    elif term_name:
        term = (
            DictionaryTerm.objects
            .filter(
                term_type=term_type,
                status=DictionaryTerm.Status.ACTIVE,
            )
            .filter(
                Q(
                    canonical_name__iexact=term_name
                )
                | Q(
                    normalized_name__iexact=term_name
                )
                | Q(
                    aliases__alias__iexact=term_name
                )
                | Q(
                    aliases__normalized_alias__iexact=term_name
                )
            )
            .distinct()
            .order_by("id")
            .first()
        )

    if term is None:
        if term_name:
            message = (
                f"{term_type} 사전에서 "
                f"'{term_name}' 용어를 찾지 못했습니다."
            )
        else:
            message = (
                "사전 검색 결과에서 "
                "추가할 용어를 선택해주세요."
            )

        messages.error(
            request,
            message,
        )

        return redirect(
            "dashboard:product_source_detail",
            product_source_id=product_source.id,
        )

    # ========================================================
    # Relation Type
    # ========================================================

    if term_type == "DETAIL":
        if relation_type not in DETAIL_RELATIONS:
            relation_type = (
                ProductTerm
                .RelationType
                .HAS_DETAIL
            )

    else:
        relation_type = (
            TERM_TYPE_DEFAULT_RELATION
            .get(term_type)
        )

    if not relation_type:
        messages.error(
            request,
            (
                f"{term_type}은 현재 "
                "상품 Term 추가 대상으로 "
                "지원하지 않습니다."
            ),
        )

        return redirect(
            "dashboard:product_source_detail",
            product_source_id=product_source.id,
        )

    # ========================================================
    # ProductTerm 생성
    # ========================================================

    _row, created = (
        ProductTerm.objects
        .get_or_create(
            product_source=product_source,
            term=term,
            relation_type=relation_type,
        )
    )

    if created:
        messages.success(
            request,
            (
                f"{term.canonical_name} "
                "Term을 추가했습니다."
            ),
        )

    else:
        messages.info(
            request,
            (
                f"{term.canonical_name} "
                "Term이 이미 연결되어 있습니다."
            ),
        )

    return redirect(
        "dashboard:product_source_detail",
        product_source_id=product_source.id,
    )


@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def remove_product_source_term(
    request,
    product_source_id,
    product_term_id,
):
    if request.method != "POST":
        raise Http404

    product_source = (
        ProductSource.objects
        .select_for_update()
        .filter(pk=product_source_id)
        .first()
    )

    if product_source is None:
        raise Http404

    product_term = (
        ProductTerm.objects
        .select_related("term")
        .filter(
            pk=product_term_id,
            product_source_id=product_source.id,
        )
        .first()
    )

    if product_term is None:
        messages.error(
            request,
            "삭제할 ProductTerm을 찾을 수 없습니다.",
        )

        return redirect(
            "dashboard:product_source_detail",
            product_source_id=product_source.id,
        )

    term_name = (
        product_term.term.canonical_name
    )

    product_term.delete()

    messages.success(
        request,
        f"{term_name} Term 연결을 삭제했습니다.",
    )

    return redirect(
        "dashboard:product_source_detail",
        product_source_id=product_source.id,
    )

@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def manual_map_product_source(request, product_source_id):
    if request.method != "POST":
        raise Http404

    product_id = str(
        request.POST.get("product_id") or ""
    ).strip()

    next_url = str(
        request.POST.get("next") or ""
    ).strip()

    # ProductSource 행만 잠근다.
    # nullable FK인 product/source_brand를 select_related로 JOIN하지 않는다.
    product_source = (
        ProductSource.objects
        .select_for_update()
        .filter(pk=product_source_id)
        .first()
    )

    if product_source is None:
        messages.error(
            request,
            "플랫폼 상품을 찾을 수 없습니다.",
        )
        return redirect(
            "dashboard:normalized_products"
        )

    if not product_id.isdigit():
        messages.error(
            request,
            "매핑할 FEEDIT 상품을 선택해주세요.",
        )
        return redirect(
            next_url
            or "dashboard:normalized_products"
        )

    # ProductSource가 속한 FEEDIT Brand를 별도 쿼리로 확인한다.
    source_brand = (
        BrandSource.objects
        .select_related("brand")
        .filter(pk=product_source.source_brand_id)
        .first()
    )

    if (
        source_brand is None
        or source_brand.brand_id is None
    ):
        messages.error(
            request,
            "FEEDIT 브랜드가 연결되지 않은 플랫폼 상품입니다.",
        )
        return redirect(
            next_url
            or "dashboard:normalized_products"
        )

    # 반드시 같은 FEEDIT Brand의 Product만 허용한다.
    product = (
        Product.objects
        .filter(
            pk=int(product_id),
            brand_id=source_brand.brand_id,
        )
        .first()
    )

    if product is None:
        messages.error(
            request,
            "같은 FEEDIT 브랜드의 상품에만 매핑할 수 있습니다.",
        )
        return redirect(
            next_url
            or "dashboard:normalized_products"
        )

    old_product_id = product_source.product_id

    product_source.product_id = product.id
    product_source.mapping_status = (
        ProductSource.MappingStatus.MAPPED
    )

    product_source.save(
        update_fields=[
            "product",
            "mapping_status",
            "updated_at",
        ]
    )

    if old_product_id == product.id:
        messages.info(
            request,
            f"이미 Product #{product.id}에 매핑되어 있습니다.",
        )

    elif old_product_id:
        messages.success(
            request,
            (
                f"ProductSource #{product_source.id} 매핑을 "
                f"Product #{old_product_id}에서 "
                f"Product #{product.id}로 변경했습니다."
            ),
        )

    else:
        messages.success(
            request,
            (
                f"ProductSource #{product_source.id}을 "
                f"Product #{product.id}에 수동 매핑했습니다."
            ),
        )

    return redirect(
        next_url
        or "dashboard:normalized_products"
    )


def _next_product_code_number():
    prefix = "FDT-P-"
    max_number = 0

    existing_codes = (
        Product.objects
        .filter(product_code__startswith=prefix)
        .exclude(product_code__isnull=True)
        .values_list("product_code", flat=True)
    )

    for code in existing_codes:
        code = str(code or "").strip()
        if not code.startswith(prefix):
            continue

        number_text = code[len(prefix):]
        if number_text.isdigit():
            max_number = max(max_number, int(number_text))

    return max_number + 1


def _promote_locked_product_source(
    product_source,
    *,
    product_code,
    product_name=None,
):
    if product_source.product_id:
        return None, "ALREADY_MAPPED"

    source_brand = (
        BrandSource.objects
        .select_related("brand")
        .filter(pk=product_source.source_brand_id)
        .first()
    )

    if source_brand is None or source_brand.brand_id is None:
        return None, "NO_BRAND"

    category = None
    if product_source.source_category_id:
        source_category = (
            CategorySource.objects
            .select_related("category")
            .filter(pk=product_source.source_category_id)
            .first()
        )
        if source_category:
            category = source_category.category

    product_name = str(
        product_name
        or product_source.normalized_name
        or product_source.source_name
        or ""
    ).strip()

    if not product_name:
        return None, "NO_NAME"

    normalized_attributes = (
        (product_source.attributes or {}).get("normalized", {})
    )
    if not isinstance(normalized_attributes, dict):
        normalized_attributes = {}

    product = Product.objects.create(
        product_code=product_code,
        group_key=None,
        brand=source_brand.brand,
        category=category,
        canonical_name=product_name,
        normalized_name=product_name,
        english_name=product_source.source_name_en or None,
        gender_scope=product_source.gender_scope or None,
        attributes=normalized_attributes,
        status=Product.Status.ACTIVE,
    )

    product_source.product = product
    product_source.mapping_status = ProductSource.MappingStatus.MAPPED
    product_source.save(
        update_fields=[
            "product",
            "mapping_status",
            "updated_at",
        ]
    )

    return product, None


@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def promote_product_source(request, product_source_id):
    if request.method != "POST":
        raise Http404

    next_url = str(request.POST.get("next") or "").strip()

    product_source = (
        ProductSource.objects
        .select_for_update()
        .filter(pk=product_source_id)
        .first()
    )

    if product_source is None:
        messages.error(request, "플랫폼 상품을 찾을 수 없습니다.")
        return redirect(next_url or "dashboard:normalized_products")

    if product_source.product_id:
        messages.info(
            request,
            (
                f"ProductSource #{product_source.id}은 "
                f"이미 Product #{product_source.product_id}에 "
                "연결되어 있습니다."
            ),
        )
        return redirect(next_url or "dashboard:normalized_products")

    product_name = str(
        request.POST.get("product_name")
        or product_source.normalized_name
        or product_source.source_name
        or ""
    ).strip()

    next_number = _next_product_code_number()
    product_code = f"FDT-P-{next_number:08d}"

    product, error = _promote_locked_product_source(
        product_source,
        product_code=product_code,
        product_name=product_name,
    )

    if error == "NO_BRAND":
        messages.error(request, "FEEDIT 브랜드가 연결되지 않은 플랫폼 상품입니다.")
    elif error == "NO_NAME":
        messages.error(request, "상품명이 없어 FEEDIT Product로 승격할 수 없습니다.")
    elif error == "ALREADY_MAPPED":
        messages.info(request, "이미 FEEDIT Product에 연결된 상품입니다.")
    else:
        messages.success(
            request,
            (
                f"ProductSource #{product_source.id}을 "
                f"신규 FEEDIT Product #{product.id}로 승격했습니다. "
                f"({product.product_code})"
            ),
        )

    return redirect(next_url or "dashboard:normalized_products")


@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def bulk_promote_product_sources(request):
    if request.method != "POST":
        raise Http404

    next_url = str(request.POST.get("next") or "").strip()
    brand_id = str(request.POST.get("brand_id") or "").strip()

    raw_ids = request.POST.getlist("product_source_ids")
    selected_ids = []
    seen = set()

    for raw_id in raw_ids:
        value = str(raw_id or "").strip()
        if not value.isdigit():
            continue
        product_source_id = int(value)
        if product_source_id in seen:
            continue
        seen.add(product_source_id)
        selected_ids.append(product_source_id)

    if not brand_id.isdigit():
        messages.error(request, "브랜드 정보가 없어 일괄 승격할 수 없습니다.")
        return redirect(next_url or "dashboard:normalized_products")

    if not selected_ids:
        messages.info(request, "승격할 미매핑 상품을 선택해주세요.")
        return redirect(next_url or "dashboard:normalized_products")

    if len(selected_ids) > 500:
        messages.error(request, "한 번에 최대 500개까지 승격할 수 있습니다.")
        return redirect(next_url or "dashboard:normalized_products")

    # 선택한 브랜드의 USED 제외 미매핑 ProductSource만 허용한다.
    eligible_ids = list(
        ProductSource.objects
        .filter(
            pk__in=selected_ids,
            product_id__isnull=True,
            source_brand__brand_id=int(brand_id),
        )
        .exclude(source__code__iexact="musinsa_used")
        .exclude(source__code__iexact="musinsa-used")
        .values_list("id", flat=True)
    )

    locked_sources = list(
        ProductSource.objects
        .select_for_update()
        .filter(pk__in=eligible_ids)
        .order_by("id")
    )

    next_number = _next_product_code_number()
    promoted = 0
    skipped = len(selected_ids) - len(locked_sources)
    failed_no_brand = 0
    failed_no_name = 0

    for product_source in locked_sources:
        product_code = f"FDT-P-{next_number:08d}"
        product, error = _promote_locked_product_source(
            product_source,
            product_code=product_code,
        )

        if product is not None:
            promoted += 1
            next_number += 1
            continue

        skipped += 1
        if error == "NO_BRAND":
            failed_no_brand += 1
        elif error == "NO_NAME":
            failed_no_name += 1

    message = f"선택 상품 일괄 승격 완료: {promoted:,}개"
    details = []
    if skipped:
        details.append(f"제외/스킵 {skipped:,}개")
    if failed_no_brand:
        details.append(f"브랜드 없음 {failed_no_brand:,}개")
    if failed_no_name:
        details.append(f"상품명 없음 {failed_no_name:,}개")
    if details:
        message += " (" + ", ".join(details) + ")"

    if promoted:
        messages.success(request, message)
    else:
        messages.info(request, message)

    return redirect(next_url or "dashboard:normalized_products")


@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def unmap_product_source(request, product_source_id):
    if request.method != "POST":
        raise Http404

    next_url = str(request.POST.get("next") or "").strip()

    product_source = (
        ProductSource.objects
        .select_for_update()
        .filter(pk=product_source_id)
        .first()
    )

    if product_source is None:
        messages.error(request, "플랫폼 상품을 찾을 수 없습니다.")
        return redirect("dashboard:normalized_products")

    old_product_id = product_source.product_id
    product_source.product = None
    product_source.mapping_status = ProductSource.MappingStatus.UNMAPPED
    product_source.save(
        update_fields=["product", "mapping_status", "updated_at"]
    )

    if old_product_id:
        messages.success(
            request,
            f"ProductSource #{product_source.id}의 Product #{old_product_id} 매핑을 해제했습니다.",
        )
    else:
        messages.info(request, "이미 미매핑 상태입니다.")

    return redirect(next_url or "dashboard:normalized_products")


@login_required(login_url="/admin-dashboard/login/")
def normalization_failures(request):
    """정규화 실패 — 실패한 RawDocument와 오류 내용."""

    selected_source = request.GET.get("source", "").strip()
    selected_type = request.GET.get("document_type", "").strip()
    q = request.GET.get("q", "").strip()

    queryset = (
        RawDocument.objects
        .select_related("source", "crawl_run")
        .filter(normalization_status="FAILED")
        .order_by("-collected_at", "-id")
    )

    if selected_source:
        queryset = queryset.filter(
            source_id=selected_source
        )

    if selected_type:
        queryset = queryset.filter(
            document_type=selected_type
        )

    if q:
        queryset = queryset.filter(
            Q(external_id__icontains=q)
            | Q(s3_key__icontains=q)
            | Q(normalization_error__icontains=q)
            | Q(source_url__icontains=q)
        )

    paginator = Paginator(queryset, 30)
    page_obj = paginator.get_page(
        request.GET.get("page")
    )

    rows = list(page_obj.object_list)

    for row in rows:
        row.platform_label = source_label(row.source)

    # 실패 사유 상위 (앞 80자로 묶음)
    reasons = {}
    for text in (
        RawDocument.objects
        .filter(normalization_status="FAILED")
        .values_list("normalization_error", flat=True)[:2000]
    ):
        key = (str(text or "").strip() or "(사유 없음)")[:80]
        reasons[key] = reasons.get(key, 0) + 1

    top_reasons = sorted(
        reasons.items(),
        key=lambda x: -x[1],
    )[:6]

    document_types = (
        RawDocument.objects
        .filter(normalization_status="FAILED")
        .values_list("document_type", flat=True)
        .distinct()
        .order_by("document_type")
    )

    context = {
        "page_title": "정규화 실패",
        "page_description": (
            "정규화에 실패한 원본 문서와 오류 내용을 검토합니다."
        ),
        "summary": _normalization_summary(),
        "top_reasons": top_reasons,
        "sources": ordered_sources(),
        "document_types": document_types,
        "page_obj": page_obj,
        "rows": rows,
        "filtered_count": paginator.count,
        "selected_source": selected_source,
        "selected_type": selected_type,
        "search_query": q,
    }

    return render(
        request,
        "dashboard/normalization/failures.html",
        context,
    )


def _platform_commerce_page(request, source, title, description, reset_url):
    """플랫폼 상품(ProductSource) 기반 정규화 페이지."""

    selected_mapping = request.GET.get("mapping", "").strip()
    selected_brand = request.GET.get("brand", "").strip()
    selected_order = request.GET.get("order", "recent").strip()
    q = request.GET.get("q", "").strip()

    base = ProductSource.objects.filter(source=source)

    queryset = (
        base
        .select_related(
            "source",
            "source_brand",
            "source_brand__brand",
            "source_category",
        )
    )

    if selected_order == "id_asc":
        queryset = queryset.order_by("id")
    elif selected_order == "id_desc":
        queryset = queryset.order_by("-id")
    else:
        queryset = queryset.order_by("-last_seen_at", "-id")

    if selected_mapping:
        queryset = queryset.filter(mapping_status=selected_mapping)

    if selected_brand == "1":
        queryset = queryset.filter(source_brand__brand__isnull=False)
    elif selected_brand == "0":
        queryset = queryset.filter(
            Q(source_brand__isnull=True)
            | Q(source_brand__brand__isnull=True)
        )

    if q:
        queryset = queryset.filter(
            Q(source_name__icontains=q)
            | Q(normalized_name__icontains=q)
            | Q(source_product_id__icontains=q)
            | Q(style_no__icontains=q)
            | Q(source_brand__name__icontains=q)
        )

    paginator = Paginator(queryset, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    rows = list(page_obj.object_list)
    attach_latest_product_snapshot(rows)

    docs = RawDocument.objects.filter(source=source)
    total_rows = base.count()

    cards = [
        {
            "label": "원본 문서",
            "value": docs.count(),
            "caption": "RawDocument",
            "tone": "",
        },
        {
            "label": "정규화 성공",
            "value": docs.filter(normalization_status="SUCCESS").count(),
            "caption": "SUCCESS",
            "tone": "ok",
        },
        {
            "label": "정규화 실패",
            "value": docs.filter(normalization_status="FAILED").count(),
            "caption": "FAILED",
            "tone": "bad",
        },
        {
            "label": "플랫폼 상품",
            "value": total_rows,
            "caption": "ProductSource",
            "tone": "",
        },
        {
            "label": "브랜드 연결",
            "value": base.filter(source_brand__brand__isnull=False).count(),
            "caption": "표준 브랜드 매핑됨",
            "tone": "",
        },
        {
            "label": "표준 상품 연결",
            "value": base.filter(product__isnull=False).count(),
            "caption": "Product 승격",
            "tone": "",
        },
    ]

    context = {
        "page_title": title,
        "page_description": description,
        "mode": "commerce",
        "cards": cards,
        "rows": rows,
        "page_obj": page_obj,
        "filtered_count": paginator.count,
        "total_rows": total_rows,
        "blank_reason": (
            "Source는 등록돼 있지만 이 플랫폼의 상품이 아직 한 건도 "
            "적재되지 않았습니다. 수집과 정규화를 먼저 실행해야 합니다."
        ),
        "mapping_choices": ProductSource.MappingStatus.choices,
        "selected_mapping": selected_mapping,
        "selected_brand": selected_brand,
        "selected_order": selected_order,
        "search_query": q,
        "reset_url": reset_url,
        "qs": qs_without_page(request),
        "qs_sort": qs_without(request, "order"),
    }

    return render(
        request,
        "dashboard/normalization/platform.html",
        context,
    )


def _platform_content_page(request, source, title, description, reset_url):
    """콘텐츠(ContentItem) 기반 정규화 페이지."""

    selected_content_type = request.GET.get("content_type", "").strip()
    selected_order = request.GET.get("order", "recent").strip()
    q = request.GET.get("q", "").strip()

    base = ContentItem.objects.filter(source=source)

    queryset = (
        base
        .select_related("source", "profile")
    )

    if selected_order == "id_asc":
        queryset = queryset.order_by("id")
    elif selected_order == "id_desc":
        queryset = queryset.order_by("-id")
    elif selected_order in ("views_desc", "views_asc"):
        queryset = queryset.annotate(
            v=Max("snapshots__view_count")
        ).order_by(
            "v" if selected_order == "views_asc" else "-v",
            "-id",
        )
    else:
        queryset = queryset.order_by("-published_at", "-id")

    if selected_content_type:
        queryset = queryset.filter(content_type=selected_content_type)

    if q:
        queryset = queryset.filter(
            Q(title__icontains=q)
            | Q(external_content_id__icontains=q)
            | Q(profile__name__icontains=q)
        )

    paginator = Paginator(queryset, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    rows = list(page_obj.object_list)
    attach_latest_content_snapshot(rows)

    docs = RawDocument.objects.filter(source=source)
    total_rows = base.count()

    cards = [
        {
            "label": "원본 문서",
            "value": docs.count(),
            "caption": "RawDocument",
            "tone": "",
        },
        {
            "label": "정규화 성공",
            "value": docs.filter(normalization_status="SUCCESS").count(),
            "caption": "SUCCESS",
            "tone": "ok",
        },
        {
            "label": "콘텐츠",
            "value": total_rows,
            "caption": "ContentItem",
            "tone": "",
        },
        {
            "label": "채널/프로필",
            "value": ContentProfile.objects.filter(source=source).count(),
            "caption": "ContentProfile",
            "tone": "",
        },
        {
            "label": "스냅샷",
            "value": ContentSnapshot.objects.filter(
                content_item__source=source
            ).count(),
            "caption": "ContentSnapshot",
            "tone": "",
        },
        {
            "label": "분석 문서",
            "value": TextDocument.objects.filter(source=source).count(),
            "caption": "TextDocument",
            "tone": "",
        },
    ]

    content_types = (
        base
        .exclude(content_type="")
        .values_list("content_type", flat=True)
        .distinct()
        .order_by("content_type")
    )

    context = {
        "page_title": title,
        "page_description": description,
        "mode": "content",
        "cards": cards,
        "rows": rows,
        "page_obj": page_obj,
        "filtered_count": paginator.count,
        "total_rows": total_rows,
        "blank_reason": (
            "Source는 등록돼 있지만 이 플랫폼의 콘텐츠가 아직 "
            "적재되지 않았습니다."
        ),
        "content_types": content_types,
        "selected_content_type": selected_content_type,
        "selected_order": selected_order,
        "search_query": q,
        "reset_url": reset_url,
        "qs": qs_without_page(request),
        "qs_sort": qs_without(request, "order"),
    }

    return render(
        request,
        "dashboard/normalization/platform.html",
        context,
    )


def _platform_page(request, codes, title, description, url_name, mode="commerce"):
    """플랫폼별 정규화 페이지 공통 진입점."""

    source = find_source(*codes)
    reset_url = reverse("dashboard:" + url_name)

    if source is None:
        return render(
            request,
            "dashboard/normalization/platform.html",
            {
                "page_title": title,
                "page_description": description,
                "mode": "none",
                "blank_reason": (
                    "이 플랫폼의 Source가 아직 등록되지 않았습니다. "
                    "수집 대상을 등록하고 크롤링을 실행하면 여기에 "
                    "정규화 결과가 표시됩니다."
                ),
            },
        )

    if mode == "auto":
        has_content = ContentItem.objects.filter(source=source).exists()
        has_product = ProductSource.objects.filter(source=source).exists()
        mode = "content" if (has_content and not has_product) else "commerce"

    if mode == "content":
        return _platform_content_page(
            request, source, title, description, reset_url
        )

    return _platform_commerce_page(
        request, source, title, description, reset_url
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_musinsa(request):
    return _platform_page(
        request,
        ["musinsa"],
        "무신사",
        "무신사에서 수집·정규화된 상품을 조회합니다.",
        "normalized_musinsa",
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_zigzag(request):
    return _platform_page(
        request,
        ["zigzag"],
        "지그재그",
        "지그재그에서 수집·정규화된 상품을 조회합니다.",
        "normalized_zigzag",
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_ably(request):
    return _platform_page(
        request,
        ["ably"],
        "에이블리",
        "에이블리에서 수집·정규화된 상품을 조회합니다.",
        "normalized_ably",
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_kream(request):
    return _platform_page(
        request,
        ["kream"],
        "크림",
        "크림에서 수집·정규화된 리셀 상품을 조회합니다.",
        "normalized_kream",
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_musinsa_used(request):
    return _platform_page(
        request,
        ["musinsa_used", "musinsa-used"],
        "무신사 USED",
        "무신사 USED에서 수집·정규화된 중고 상품을 조회합니다.",
        "normalized_musinsa_used",
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_youtube(request):
    return _platform_page(
        request,
        ["YOUTUBE", "youtube"],
        "유튜브",
        "유튜브에서 수집·정규화된 콘텐츠를 조회합니다.",
        "normalized_youtube",
        mode="content",
    )


@login_required(login_url="/admin-dashboard/login/")
def normalized_naver(request):
    return _platform_page(
        request,
        ["naver"],
        "네이버",
        "네이버에서 수집·정규화된 데이터를 조회합니다.",
        "normalized_naver",
        mode="auto",
    )

@require_POST
def run_product_mapping(request):
    brand_id = request.POST.get("brand_id")

    if not brand_id:
        messages.error(request, "브랜드가 선택되지 않았습니다.")
        return redirect("dashboard:normalized_products")

    brand_id = int(brand_id)

    # 같은 브랜드 안에서 normalized_name이 유일한 Product만 사용한다.
    unique_names = (
        Product.objects
        .filter(
            brand_id=brand_id,
        )
        .exclude(normalized_name__isnull=True)
        .exclude(normalized_name="")
        .values("normalized_name")
        .annotate(product_count=Count("id"))
        .filter(product_count=1)
        .values_list("normalized_name", flat=True)
    )

    product_map = {
        product.normalized_name: product.id
        for product in Product.objects.filter(
            brand_id=brand_id,
            normalized_name__in=unique_names,
        ).only(
            "id",
            "normalized_name",
        )
    }

    candidates = (
        ProductSource.objects
        .filter(
            product_id__isnull=True,
            source_brand__brand_id=brand_id,
            normalized_name__in=product_map.keys(),
        )
        .exclude(normalized_name__isnull=True)
        .exclude(normalized_name="")
        .only(
            "id",
            "normalized_name",
            "product_id",
            "mapping_status",
        )
    )

    mapped_count = 0

    with transaction.atomic():
        for product_source in candidates.iterator(chunk_size=500):
            product_id = product_map.get(
                product_source.normalized_name
            )

            if not product_id:
                continue

            # 동시에 다른 작업이 매핑했을 가능성까지 방어한다.
            updated = (
                ProductSource.objects
                .filter(
                    pk=product_source.pk,
                    product_id__isnull=True,
                )
                .update(
                    product_id=product_id,
                    mapping_status="MAPPED",
                )
            )

            mapped_count += updated

    messages.success(
        request,
        f"EXACT 상품 매핑 완료: {mapped_count:,}개",
    )

    return redirect(
        f"/admin-dashboard/normalization/products/"
        f"?mode=REAL&brand={brand_id}"
    )
    
    
@require_GET
def search_dictionary_terms(request):
    term_type = (
        request.GET.get("type", "")
        .strip()
        .upper()
    )
    query = request.GET.get("q", "").strip()

    allowed_types = {
        "ITEM",
        "DETAIL",
        "MATERIAL",
        "COLOR",
        "STYLE",
        "TPO",
    }

    if term_type not in allowed_types:
        return JsonResponse(
            {"results": []}
        )

    if len(query) < 1:
        return JsonResponse(
            {"results": []}
        )

    terms = (
        DictionaryTerm.objects
        .filter(
            term_type=term_type,
            status=DictionaryTerm.Status.ACTIVE,
        )
        .filter(
            Q(canonical_name__icontains=query)
            | Q(normalized_name__icontains=query)
            | Q(english_name__icontains=query)
            | Q(aliases__alias__icontains=query)
            | Q(aliases__normalized_alias__icontains=query)
        )
        .distinct()
        .order_by(
            "canonical_name",
            "id",
        )[:20]
    )

    term_ids = [term.id for term in terms]

    aliases = (
        TermAlias.objects
        .filter(
            term_id__in=term_ids,
        )
        .filter(
            Q(alias__icontains=query)
            | Q(normalized_alias__icontains=query)
        )
        .select_related("term")
        .order_by("id")
    )

    alias_map = {}

    for alias in aliases:
        alias_map.setdefault(
            alias.term_id,
            alias.alias,
        )

    results = []

    for term in terms:
        results.append(
            {
                "id": term.id,
                "term_type": term.term_type,
                "canonical_name": term.canonical_name,
                "english_name": term.english_name or "",
                "matched_alias": alias_map.get(
                    term.id,
                    "",
                ),
            }
        )

    return JsonResponse(
        {
            "results": results,
        }
    )
    
@login_required(login_url="/admin-dashboard/login/")
def product_detail(
    request,
    product_id,
):
    context = get_product_detail_context(
        product_id
    )

    if context is None:
        raise Http404

    context["page_title"] = (
        "FEEDIT Product 상세"
    )

    context["categories"] = (
        Category.objects
        .filter(
            status="ACTIVE",
        )
        .order_by(
            "level",
            "sort_order",
            "name",
        )
    )

    return render(
        request,
        "dashboard/normalization/product_detail.html",
        context,
    )


@login_required(login_url="/admin-dashboard/login/")
@transaction.atomic
def update_product(
    request,
    product_id,
):
    if request.method != "POST":
        raise Http404

    product = (
        Product.objects
        .select_for_update()
        .filter(pk=product_id)
        .first()
    )

    if product is None:
        raise Http404

    canonical_name = str(
        request.POST.get("canonical_name")
        or ""
    ).strip()

    normalized_name = str(
        request.POST.get("normalized_name")
        or ""
    ).strip()

    english_name = str(
        request.POST.get("english_name")
        or ""
    ).strip()

    group_key = str(
        request.POST.get("group_key")
        or ""
    ).strip()

    gender_scope = str(
        request.POST.get("gender_scope")
        or ""
    ).strip()

    category_id = str(
        request.POST.get("category_id")
        or ""
    ).strip()

    status = str(
        request.POST.get("status")
        or ""
    ).strip().upper()

    if not canonical_name:
        messages.error(
            request,
            "상품명을 입력해주세요.",
        )
        return redirect(
            "dashboard:product_detail",
            product_id=product.id,
        )

    if not normalized_name:
        messages.error(
            request,
            "검색용 상품명을 입력해주세요.",
        )
        return redirect(
            "dashboard:product_detail",
            product_id=product.id,
        )

    valid_statuses = {
        value
        for value, _label
        in Product.Status.choices
    }

    if status not in valid_statuses:
        messages.error(
            request,
            "올바른 상품 상태를 선택해주세요.",
        )
        return redirect(
            "dashboard:product_detail",
            product_id=product.id,
        )

    category = None

    if category_id:
        if not category_id.isdigit():
            messages.error(
                request,
                "올바른 카테고리를 선택해주세요.",
            )
            return redirect(
                "dashboard:product_detail",
                product_id=product.id,
            )

        category = (
            Category.objects
            .filter(
                pk=int(category_id),
            )
            .first()
        )

        if category is None:
            messages.error(
                request,
                "선택한 카테고리를 찾을 수 없습니다.",
            )
            return redirect(
                "dashboard:product_detail",
                product_id=product.id,
            )

    product.canonical_name = canonical_name
    product.normalized_name = normalized_name

    product.english_name = (
        english_name
        or None
    )

    product.group_key = (
        group_key
        or None
    )

    product.gender_scope = (
        gender_scope
        or None
    )

    product.category = category
    product.status = status

    product.save(
        update_fields=[
            "canonical_name",
            "normalized_name",
            "english_name",
            "group_key",
            "gender_scope",
            "category",
            "status",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "FEEDIT Product 정보를 수정했습니다.",
    )

    return redirect(
        "dashboard:product_detail",
        product_id=product.id,
    )