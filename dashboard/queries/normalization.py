from django.core.paginator import Paginator
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.db.models import (
    Case,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)

from core.models import (
    Brand,
    BrandSource,
    Product,
    ProductReview,
    ProductSource,
    ProductSourceSnapshot,
    ProductTerm,
    ResaleSnapshot,
    Source,
)


PLATFORM_CODES = (
    "musinsa",
    "zigzag",
    "kream",
    "ably",
    "musinsa_used",
    "musinsa-used",
)



def _platform_priority(prefix=""):
    field = f"{prefix}code"
    return Case(
        When(**{f"{field}__iexact": "musinsa"}, then=Value(1)),
        When(**{f"{field}__iexact": "zigzag"}, then=Value(2)),
        When(**{f"{field}__iexact": "kream"}, then=Value(3)),
        When(**{f"{field}__iexact": "ably"}, then=Value(4)),
        When(**{f"{field}__iexact": "musinsa_used"}, then=Value(999)),
        When(**{f"{field}__iexact": "musinsa-used"}, then=Value(999)),
        default=Value(99),
        output_field=IntegerField(),
    )


def _source_options():
    """실제 ProductSource가 존재하는 플랫폼만 노출한다."""
    product_source_exists = ProductSource.objects.filter(
        source_id=OuterRef("pk")
    )

    return (
        Source.objects
        .annotate(
            has_product_sources=Exists(product_source_exists),
            _platform_priority_value=_platform_priority(),
        )
        .filter(has_product_sources=True)
        .order_by("_platform_priority_value", "name", "id")
    )


def _brand_queryset():
    """공식 Product가 있는 FEEDIT 브랜드만 노출한다."""
    product_exists = Product.objects.filter(
        brand_id=OuterRef("pk")
    )

    official_count = (
        Product.objects
        .filter(brand_id=OuterRef("pk"))
        .values("brand_id")
        .annotate(cnt=Count("id"))
        .values("cnt")[:1]
    )

    actual_source_count = (
        BrandSource.objects
        .filter(brand_id=OuterRef("pk"))
        .values("brand_id")
        .annotate(cnt=Count("id"))
        .values("cnt")[:1]
    )

    return (
        Brand.objects
        .annotate(
            has_products=Exists(product_exists),
            official_count=Subquery(
                official_count,
                output_field=IntegerField(),
            ),
            dashboard_source_count=Subquery(
                actual_source_count,
                output_field=IntegerField(),
            ),
        )
        .filter(has_products=True)
        .order_by("name", "id")
    )


def _representative_product_source():
    return (
        ProductSource.objects
        .filter(
            product_id=OuterRef("pk"),
            thumbnail_url__isnull=False,
        )
        .exclude(thumbnail_url="")
        .annotate(
            _platform_priority_value=_platform_priority("source__"),
        )
        .order_by(
            "_platform_priority_value",
            "source_id",
            "-last_seen_at",
            "-id",
        )
    )


def _official_products(selected_brand):
    representative_source = _representative_product_source()

    return (
        Product.objects
        .filter(brand=selected_brand)
        .annotate(
            representative_thumbnail=Subquery(
                representative_source.values("thumbnail_url")[:1]
            ),
            representative_source_id=Subquery(
                representative_source.values("id")[:1]
            ),
        )
        .order_by(
            "normalized_name",
            "id",
        )
    )

def _recommended_brands(mode="REAL", q="", limit=24, offset=0):
    """상품 매핑 작업 우선순위용 브랜드 목록."""
    mode = str(mode or "REAL").strip().upper()
    if mode not in {"REAL", "MARKET"}:
        mode = "REAL"

    qs = Brand.objects.all()

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(english_name__icontains=q)
        )

    non_used_source_filter = (
        ~Q(brand_sources__source__code__iexact="musinsa_used")
        & ~Q(brand_sources__source__code__iexact="musinsa-used")
    )
    non_used_product_filter = (
        ~Q(brand_sources__product_sources__source__code__iexact="musinsa_used")
        & ~Q(brand_sources__product_sources__source__code__iexact="musinsa-used")
    )

    qs = qs.annotate(
        musinsa_source_count=Count(
            "brand_sources",
            filter=Q(brand_sources__source__code__iexact="musinsa"),
            distinct=True,
        ),
        zigzag_source_count=Count(
            "brand_sources",
            filter=Q(brand_sources__source__code__iexact="zigzag"),
            distinct=True,
        ),
        non_used_platform_count=Count(
            "brand_sources__source",
            filter=non_used_source_filter,
            distinct=True,
        ),
        observed_product_count=Count(
            "brand_sources__product_sources",
            filter=non_used_product_filter,
            distinct=True,
        ),
        unmapped_product_count=Count(
            "brand_sources__product_sources",
            filter=(
                non_used_product_filter
                & Q(brand_sources__product_sources__product_id__isnull=True)
            ),
            distinct=True,
        ),
        musinsa_product_count=Count(
            "brand_sources__product_sources",
            filter=Q(
                brand_sources__product_sources__source__code__iexact="musinsa"
            ),
            distinct=True,
        ),
        zigzag_product_count=Count(
            "brand_sources__product_sources",
            filter=Q(
                brand_sources__product_sources__source__code__iexact="zigzag"
            ),
            distinct=True,
        ),
        used_product_count=Count(
            "brand_sources__product_sources",
            filter=(
                Q(brand_sources__product_sources__source__code__iexact="musinsa_used")
                | Q(brand_sources__product_sources__source__code__iexact="musinsa-used")
            ),
            distinct=True,
        ),
    )

    if mode == "REAL":
        qs = qs.filter(
            musinsa_source_count__gt=0,
            musinsa_product_count__gt=0,
            unmapped_product_count__gt=0,
        ).order_by(
            "-unmapped_product_count",
            "-non_used_platform_count",
            "-observed_product_count",
            "-musinsa_product_count",
            "name",
            "id",
        )
    else:
        qs = qs.filter(
            musinsa_source_count=0,
            zigzag_source_count__gt=0,
            zigzag_product_count__gt=0,
            unmapped_product_count__gt=0,
        ).order_by(
            "-unmapped_product_count",
            "-non_used_platform_count",
            "-observed_product_count",
            "-zigzag_product_count",
            "name",
            "id",
        )

    offset = max(int(offset or 0), 0)
    return qs[offset:offset + limit]


def _cached_recommended_brands(mode="REAL", q="", limit=24, offset=0):
    # 검색어 없는 기본 추천 목록만 짧게 캐시한다.
    if q:
        return list(_recommended_brands(mode=mode, q=q, limit=limit, offset=offset))

    cache_key = f"dashboard:recommended-brands:{mode}:{limit}:{offset}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows = list(_recommended_brands(mode=mode, q="", limit=limit, offset=offset))
    cache.set(cache_key, rows, 60)
    return rows


def get_product_mapping_context(request):
    brand_id = request.GET.get("brand", "").strip()
    mode = request.GET.get("mode", "REAL").strip().upper()
    brand_q = request.GET.get("brand_q", "").strip()
    market = request.GET.get("market", "").strip().upper()
    mapping = request.GET.get("mapping", "").strip().upper()
    source_code = request.GET.get("source", "").strip()
    q = request.GET.get("q", "").strip()
    page_raw = request.GET.get("page", "1").strip()
    page_number = int(page_raw) if page_raw.isdigit() else 1
    page_number = max(page_number, 1)

    # 대형 브랜드는 초기 매핑 작업에서 제외.
    # ?skip_top=0 으로 전체 우선순위의 처음부터 다시 볼 수 있다.
    skip_top_raw = request.GET.get("skip_top", "20").strip()
    skip_top = int(skip_top_raw) if skip_top_raw.isdigit() else 20
    skip_top = max(skip_top, 0)

    if mode not in {"REAL", "MARKET"}:
        mode = "REAL"

    recommended_brands = _cached_recommended_brands(
        mode=mode, limit=24, offset=skip_top
    )
    # 직접 검색할 때는 대형 브랜드도 찾을 수 있게 offset을 적용하지 않는다.
    brand_search_results = (
        list(_recommended_brands(mode=mode, q=brand_q, limit=50, offset=0))
        if brand_q else []
    )

    selected_brand = None
    if brand_id.isdigit():
        selected_brand = Brand.objects.filter(pk=int(brand_id)).first()

    base_context = {
        "page_title": "상품 매핑 관리",
        "page_description": (
            "기준 플랫폼을 중심으로 FEEDIT Product를 만들고 플랫폼 상품을 연결합니다."
        ),
        "brands": [],
        "selected_brand": selected_brand,
        "selected_mode": mode,
        "brand_search_query": brand_q,
        "recommended_brands": recommended_brands,
        "brand_search_results": brand_search_results,
        "source_options": _source_options(),
        "selected_market": market,
        "selected_mapping": mapping,
        "selected_source": source_code,
        "search_query": q,
        "skip_top": skip_top,
    }

    if selected_brand is None:
        return {
            **base_context,
            "official_products": [],
            "mapping_products": [],
            "grouped": {"RETAIL": [], "RESALE": []},
            "summary": {
                "official": 0,
                "source_total": 0,
                "mapped": 0,
                "candidates": 0,
            },
            "page_obj": None,
            "filtered_count": 0,
        }

    official_qs = _official_products(selected_brand)
    official_products = list(official_qs[:30])
    for product in official_products:
        product.display_thumbnail_url = normalize_image_url(
            product.representative_thumbnail
        )

    # Finder 후보는 초기 페이지에서 선로딩하지 않는다. 서버 검색 endpoint에서 필요할 때만 조회한다.
    mapping_products = []

    source_qs = (
        ProductSource.objects
        .filter(source_brand__brand=selected_brand)
        .select_related(
            "source",
            "source_brand",
            "product",
            "source_category",
            "source_category__category",
            "source_category__category__parent",
            "source_category__category__parent__parent",
        )
    )

    if market in {"RETAIL", "RESALE"}:
        source_qs = source_qs.filter(market_type=market)
    # 기본 화면은 작업 대상인 미매핑 상품을 바로 보여준다.
    # 전체 Source를 먼저 페이지네이션하면 mapped 상품이 앞 페이지를 차지해
    # 미매핑 개수는 존재하지만 카드가 비어 보일 수 있다.
    if mapping == "MAPPED":
        source_qs = source_qs.filter(product_id__isnull=False)
    else:
        source_qs = (
            source_qs
            .filter(product_id__isnull=True)
            .exclude(source__code__iexact="musinsa_used")
            .exclude(source__code__iexact="musinsa-used")
        )
    if source_code:
        source_qs = source_qs.filter(source__code__iexact=source_code)
    if q:
        source_qs = source_qs.filter(
            Q(normalized_name__icontains=q)
            | Q(source_name__icontains=q)
            | Q(source_product_id__icontains=q)
            | Q(style_no__icontains=q)
        )

    source_qs = source_qs.annotate(
        _platform_priority_value=_platform_priority("source__"),
    ).order_by(
        "market_type",
        "_platform_priority_value",
        "source_id",
        "normalized_name",
        "-last_seen_at",
        "-id",
    )

    paginator = Paginator(source_qs, 20)
    page_obj = paginator.get_page(page_number)
    rows = list(page_obj.object_list)

    for product_source in rows:
        product_source.display_thumbnail_url = normalize_image_url(
            product_source.thumbnail_url
        )

    _attach_source_finder_metadata(rows)

    grouped = {"RETAIL": [], "RESALE": []}
    group_map = {}
    for row in rows:
        market_key = row.market_type or "RETAIL"
        code = (row.source.code or "").lower()
        key = (market_key, row.source_id)
        if key not in group_map:
            group = {
                "source": row.source,
                "source_code": code,
                "source_name": row.source.name or row.source.code,
                "mapped": [],
                "candidates": [],
            }
            group_map[key] = group
            grouped.setdefault(market_key, []).append(group)
        if row.product_id:
            group_map[key]["mapped"].append(row)
        else:
            group_map[key]["candidates"].append(row)

    brand_source_base = ProductSource.objects.filter(
        source_brand__brand=selected_brand
    )
    non_used_brand_source_base = brand_source_base.exclude(
        source__code__iexact="musinsa_used",
    ).exclude(
        source__code__iexact="musinsa-used",
    )

    brand_counts = brand_source_base.aggregate(
        total=Count("id"),
        mapped=Count("id", filter=Q(product_id__isnull=False)),
    )

    non_used_brand_counts = non_used_brand_source_base.aggregate(
        candidates=Count(
            "id",
            filter=Q(product_id__isnull=True),
        ),
    )

    return {
        **base_context,
        "official_products": official_products,
        "mapping_products": mapping_products,
        "grouped": grouped,
        "summary": {
            "official": Product.objects.filter(brand=selected_brand).count(),
            "source_total": brand_counts["total"],
            "mapped": brand_counts["mapped"],
            "candidates": non_used_brand_counts["candidates"],
        },
        "page_obj": page_obj,
        "unmapped_rows": rows if mapping != "MAPPED" else [],
        "mapped_rows": rows if mapping == "MAPPED" else [],
        "filtered_count": paginator.count,
    }

def normalize_image_url(url):
    if not url:
        return None

    url = str(url).strip()

    if not url:
        return None

    if url.startswith(("http://", "https://")):
        return url

    if url.startswith("//"):
        return f"https:{url}"

    if url.startswith("/images/"):
        return f"https://image.msscdn.net{url}"

    return None


def _category_root(category):
    """최대 3-depth FEEDIT 상품 카테고리에서 최상위 대분류를 반환한다."""
    current = category
    while current is not None and current.parent_id:
        current = current.parent
    return current


def _attach_source_finder_metadata(rows):
    """현재 페이지 ProductSource에 필요한 Finder 메타데이터만 붙인다."""
    source_ids = [row.id for row in rows]
    source_terms = {}

    if source_ids:
        for product_term in (
            ProductTerm.objects
            .filter(product_source_id__in=source_ids)
            .select_related("term")
            .order_by("term__term_type", "term__canonical_name", "id")
        ):
            source_terms.setdefault(product_term.product_source_id, []).append(product_term)

    for row in rows:
        category = (
            row.source_category.category
            if row.source_category_id and row.source_category and row.source_category.category_id
            else None
        )
        root = _category_root(category) if category else None
        row.mapping_category_id = category.id if category else None
        row.mapping_category_name = category.name if category else None
        row.mapping_root_category_id = root.id if root else None
        row.mapping_root_category_name = root.name if root else None
        row.finder_terms = source_terms.get(row.id, [])
        row.finder_term_ids = ",".join(str(item.term_id) for item in row.finder_terms)


def find_mapping_products(
    brand_id,
    q="",
    limit=30,
    scope="all",
    category_id=None,
    root_category_id=None,
    term_ids=None,
):
    """Product Finder 서버 검색. 후보 Product 전체를 페이지에 선로딩하지 않는다."""
    q = str(q or "").strip()
    scope = str(scope or "all").strip().lower()
    limit = min(max(int(limit or 30), 1), 50)
    term_ids = [int(x) for x in (term_ids or []) if str(x).isdigit()]
    term_ids = list(dict.fromkeys(term_ids))

    qs = (
        _official_products(Brand.objects.get(pk=brand_id))
        .select_related("category", "category__parent", "category__parent__parent")
    )

    if scope == "exact" and category_id:
        qs = qs.filter(category_id=category_id)
    elif scope == "root" and root_category_id:
        qs = qs.filter(
            Q(category_id=root_category_id)
            | Q(category__parent_id=root_category_id)
            | Q(category__parent__parent_id=root_category_id)
        )

    if term_ids:
        matching_product_ids = (
            ProductTerm.objects
            .filter(
                product_source__product__brand_id=brand_id,
                term_id__in=term_ids,
                product_source__product_id__isnull=False,
            )
            .values("product_source__product_id")
            .annotate(matched_term_count=Count("term_id", distinct=True))
            .filter(matched_term_count=len(term_ids))
            .values("product_source__product_id")
        )
        qs = qs.filter(id__in=Subquery(matching_product_ids))

    if q:
        filters = (
            Q(normalized_name__icontains=q)
            | Q(canonical_name__icontains=q)
        )
        if q.isdigit():
            filters |= Q(id=int(q))
        qs = qs.filter(filters)

    products = list(qs[:limit])
    for product in products:
        product.display_thumbnail_url = normalize_image_url(product.representative_thumbnail)
    return products

def get_product_source_detail(product_source_id):
    """
    ProductSource 내부 상세 페이지용 context.

    원본 URL로 보내기보다 FEEDIT에 수집된 정규화 정보,
    DictionaryTerm, 최신 스냅샷, 리뷰를 한 화면에서 확인한다.
    """
    product_source = get_object_or_404(
        ProductSource.objects.select_related(
            "source",
            "source_brand",
            "source_brand__brand",
            "source_category",
            "product",
            "product__brand",
        ),
        pk=product_source_id,
    )
    product_source.display_thumbnail_url = normalize_image_url(
        product_source.thumbnail_url
    )


    terms = list(
        ProductTerm.objects
        .filter(product_source_id=product_source_id)
        .select_related("term")
        .order_by("term__term_type", "term__canonical_name", "id")
    )

    latest_snapshot = None
    latest_resale = None

    if product_source.market_type == ProductSource.MarketType.RESALE:
        latest_resale = (
            ResaleSnapshot.objects
            .filter(product_source_id=product_source_id)
            .order_by("-snapshot_date", "-observed_at", "-id")
            .first()
        )
    else:
        latest_snapshot = (
            ProductSourceSnapshot.objects
            .filter(product_source_id=product_source_id)
            .order_by("-snapshot_date", "-observed_at", "-id")
            .first()
        )

    reviews = list(
        ProductReview.objects
        .filter(product_source_id=product_source_id)
        .order_by("-source_created_at", "-id")[:20]
    )

    mapping_brand = None
    available_products = Product.objects.none()

    if (
        product_source.source_brand_id
        and product_source.source_brand.brand_id
    ):
        mapping_brand = product_source.source_brand.brand
        # 대형 브랜드의 Product 전체를 상세 페이지 진입 시 선로딩하지 않는다.
        # 수동 매핑 후보는 mapping_product_search API에서 검색할 때만 조회한다.
        available_products = Product.objects.none()

    term_groups = []
    grouped_terms = {}
    for product_term in terms:
        term_type = product_term.term.term_type
        grouped_terms.setdefault(term_type, []).append(product_term)

    term_type_order = [
        "ITEM", "DETAIL", "MATERIAL", "COLOR", "STYLE",
        "TPO", "TARGET", "PERSON", "BRAND",
    ]
    for term_type in term_type_order:
        rows = grouped_terms.pop(term_type, None)
        if rows:
            term_groups.append({"term_type": term_type, "items": rows})
    for term_type in sorted(grouped_terms):
        term_groups.append({"term_type": term_type, "items": grouped_terms[term_type]})

    term_type_choices = ["ITEM", "DETAIL", "MATERIAL", "COLOR", "STYLE", "TPO"]

    return {
        "page_title": "플랫폼 상품 상세",
        "page_description": (
            "수집된 ProductSource의 정규화 정보와 연결 데이터, "
            "스냅샷, 리뷰를 확인합니다."
        ),
        "product_source": product_source,
        "terms": terms,
        "term_groups": term_groups,
        "term_type_choices": term_type_choices,
        "latest_snapshot": latest_snapshot,
        "latest_resale": latest_resale,
        "reviews": reviews,
        "mapping_brand": mapping_brand,
        "available_products": available_products,
    }

def get_product_detail_context(product_id):
    product = (
        Product.objects
        .select_related(
            "brand",
            "category",
        )
        .filter(pk=product_id)
        .first()
    )

    if product is None:
        return None

    product_sources = list(
        ProductSource.objects
        .filter(product_id=product.id)
        .select_related(
            "source",
            "source_brand",
            "source_category",
        )
        .annotate(
            term_count=Count(
                "product_terms",
                distinct=True,
            ),
        )
        .order_by(
            "source__code",
            "id",
        )
    )

    source_ids = [
        source.id
        for source in product_sources
    ]

    terms = (
        ProductTerm.objects
        .filter(
            product_source_id__in=source_ids,
        )
        .select_related(
            "term",
            "product_source",
        )
        .order_by(
            "product_source_id",
            "term__term_type",
            "term__canonical_name",
            "id",
        )
    )

    terms_by_source = {}

    for product_term in terms:
        terms_by_source.setdefault(
            product_term.product_source_id,
            [],
        ).append(product_term)

    for source in product_sources:
        source.display_thumbnail_url = (
            normalize_image_url(
                source.thumbnail_url
            )
        )

        source.detail_terms = (
            terms_by_source.get(
                source.id,
                [],
            )
        )
        
    product_image_url = None
    for source in product_sources:
        if source.display_thumbnail_url:
            product_image_url = source.display_thumbnail_url
            break

    return {
        "product": product,
        "product_sources": product_sources,
        "source_count": len(product_sources),
        "product_image_url": product_image_url,
    }