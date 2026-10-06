from django.urls import path

from . import views


app_name = "dashboard"


urlpatterns = [
    # ============================================================
    # 인증 / 메인
    # ============================================================

    path(
        "login/",
        views.dashboard_login,
        name="login",
    ),
    path(
        "logout/",
        views.dashboard_logout,
        name="logout",
    ),
    path(
        "",
        views.dashboard,
        name="dashboard",
    ),

    # ============================================================
    # 수집
    # ============================================================

    path(
        "collection/targets/",
        views.collection_targets,
        name="collection_targets",
    ),
    path(
        "collection/targets/create/",
        views.collection_targets_create,
        name="collection_targets_create",
    ),
    path(
        "collection/targets/update/<int:target_id>/",
        views.collection_targets_update,
        name="collection_targets_update",
    ),
    path(
        "collection/targets/delete/",
        views.collection_targets_delete,
        name="collection_targets_delete",
    ),
    path(
        "collection/runs/",
        views.collection_runs,
        name="collection_runs",
    ),
    path(
        "collection/raw-documents/",
        views.raw_documents,
        name="raw_documents",
    ),
    path(
        "collection/raw-documents/<int:pk>/preview/",
        views.raw_document_preview,
        name="raw_document_preview",
    ),
    path(
        "collection/raw-documents/<int:pk>/json/",
        views.raw_document_json,
        name="raw_document_json",
    ),
    path(
        "collection/raw-documents/<int:pk>/download/",
        views.raw_document_download,
        name="raw_document_download",
    ),

    # ============================================================
    # 상품 정규화 / 상품 매핑
    # ============================================================

    path(
        "normalization/products/",
        views.normalized_products,
        name="normalized_products",
    ),

    # Product Finder AJAX 검색
    path(
        "normalization/products/search/",
        views.mapping_product_search,
        name="mapping_product_search",
    ),

    path(
        "normalization/products/<int:product_id>/",
        views.product_detail,
        name="product_detail",
    ),
    path(
        "normalization/products/<int:product_id>/edit/",
        views.update_product,
        name="update_product",
    ),

    # normalized_name EXACT 자동 매핑
    path(
        "normalization/products/run-mapping/",
        views.run_product_mapping,
        name="run_product_mapping",
    ),

    # ProductSource 상세
    path(
        "normalization/products/source/<int:product_source_id>/",
        views.product_source_detail,
        name="product_source_detail",
    ),

    # ProductSource 정규화 정보 수정
    path(
        "normalization/products/source/<int:product_source_id>/edit/",
        views.update_product_source_normalization,
        name="update_product_source_normalization",
    ),

    # ProductTerm 추가
    path(
        "normalization/products/source/<int:product_source_id>/terms/add/",
        views.add_product_source_term,
        name="add_product_source_term",
    ),

    # ProductTerm 삭제
    path(
        "normalization/products/source/<int:product_source_id>/terms/<int:product_term_id>/remove/",
        views.remove_product_source_term,
        name="remove_product_source_term",
    ),

    # DictionaryTerm / TermAlias 검색
    path(
        "normalization/dictionary-terms/search/",
        views.search_dictionary_terms,
        name="search_dictionary_terms",
    ),

    # 기존 Product에 수동 매핑
    path(
        "normalization/products/source/<int:product_source_id>/map/",
        views.manual_map_product_source,
        name="manual_map_product_source",
    ),

    path(
        "normalization/products/bulk-promote/",
        views.bulk_promote_product_sources,
        name="bulk_promote_product_sources",
    ),

    # 신규 Product 승격
    path(
        "normalization/products/source/<int:product_source_id>/promote/",
        views.promote_product_source,
        name="promote_product_source",
    ),

    # Product 매핑 해제
    path(
        "normalization/products/source/<int:product_source_id>/unmap/",
        views.unmap_product_source,
        name="unmap_product_source",
    ),

    path(
        "normalization/failures/",
        views.normalization_failures,
        name="normalization_failures",
    ),

    # ============================================================
    # Dictionary
    # ============================================================

    path(
        "dictionary/terms/",
        views.dictionary_terms,
        name="dictionary_terms",
    ),
    path(
        "dictionary/candidates/",
        views.dictionary_candidates,
        name="dictionary_candidates",
    ),
    path(
        "dictionary/candidates/<int:pk>/",
        views.dictionary_candidate_detail,
        name="dictionary_candidate_detail",
    ),

    # BrandSource -> FEEDIT Brand 매핑
    path(
        "dictionary/brands/",
        views.brand_sources,
        name="brand_sources",
    ),
    path(
        "dictionary/brands/<int:source_id>/map/",
        views.map_brand_source,
        name="map_brand_source",
    ),
    path(
        "dictionary/brands/<int:source_id>/create/",
        views.create_brand_from_source,
        name="create_brand_from_source",
    ),
    path(
        "dictionary/brands/<int:source_id>/unmap/",
        views.unmap_brand_source,
        name="unmap_brand_source",
    ),
    path(
        "dictionary/brands/<int:source_id>/exclude/",
        views.exclude_brand_source,
        name="exclude_brand_source",
    ),

    # FEEDIT Brand -> 연결된 BrandSource 조회
    path(
        "dictionary/brands/overview/",
        views.brand_connections,
        name="brand_connections",
    ),

    # ============================================================
    # Trend
    # ============================================================

    path(
        "trend/metrics/",
        views.trend_metrics,
        name="trend_metrics",
    ),

    # ============================================================
    # 데이터
    # ============================================================

    path(
        "data/products/",
        views.products,
        name="products",
    ),
    path(
        "data/brands/",
        views.brands,
        name="brands",
    ),
    path(
        "data/categories/",
        views.categories,
        name="categories",
    ),

    # ============================================================
    # Jobs / System
    # ============================================================

    path(
        "jobs/",
        views.jobs,
        name="jobs",
    ),
    path(
        "system/",
        views.system_status,
        name="system_status",
    ),

    # ============================================================
    # 수집 관리
    # ============================================================

    path(
        "collection/platform-status/",
        views.platform_status,
        name="platform_status",
    ),
    path(
        "collection/run/",
        views.run_crawl,
        name="run_crawl",
    ),
    path(
        "collection/rules/",
        views.crawl_rules,
        name="crawl_rules",
    ),
    path(
        "collection/robots/",
        views.robots_check,
        name="robots_check",
    ),

    # ============================================================
    # 카테고리 매핑
    # ============================================================

    path(
        "collection/category-mapping/",
        views.category_mapping,
        name="category_mapping",
    ),
    path(
        "collection/category-mapping/search/",
        views.category_mapping_search,
        name="category_mapping_search",
    ),
    path(
        "collection/category-mapping/<int:source_id>/save/",
        views.category_mapping_save,
        name="category_mapping_save",
    ),

    # ============================================================
    # 플랫폼별 정규화 데이터
    # ============================================================

    path(
        "normalized/musinsa/",
        views.normalized_musinsa,
        name="normalized_musinsa",
    ),
    path(
        "normalized/zigzag/",
        views.normalized_zigzag,
        name="normalized_zigzag",
    ),
    path(
        "normalized/ably/",
        views.normalized_ably,
        name="normalized_ably",
    ),
    path(
        "normalized/kream/",
        views.normalized_kream,
        name="normalized_kream",
    ),
    path(
        "normalized/musinsa-used/",
        views.normalized_musinsa_used,
        name="normalized_musinsa_used",
    ),
    path(
        "normalized/youtube/",
        views.normalized_youtube,
        name="normalized_youtube",
    ),
    path(
        "normalized/naver/",
        views.normalized_naver,
        name="normalized_naver",
    ),

    # ============================================================
    # 데이터 분석
    # ============================================================

    path(
        "analytics/term-metrics/",
        views.term_metrics,
        name="term_metrics",
    ),
    path(
        "analytics/product-metrics/",
        views.product_metrics,
        name="product_metrics",
    ),
    path(
        "analytics/product-snapshot/",
        views.product_snapshot,
        name="product_snapshot",
    ),
    path(
        "analytics/product-snapshots/<int:snapshot_id>/",
        views.product_snapshot_detail,
        name="product_snapshot_detail",
    ),
    path(
        "analytics/text-comments/",
        views.analytics.text_comment_metrics,
        name="text_comment_metrics",
    ),

    # ============================================================
    # 시스템 상세
    # ============================================================

    path(
        "system/api/",
        views.system_api,
        name="system_api",
    ),
    path(
        "system/aws/",
        views.system_aws,
        name="system_aws",
    ),
    path(
        "system/celery/",
        views.system_celery,
        name="system_celery",
    ),
]