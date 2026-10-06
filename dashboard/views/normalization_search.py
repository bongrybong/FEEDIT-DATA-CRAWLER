from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from dashboard.queries.normalization import (
    find_mapping_products,
    get_product_mapping_context,
)


@login_required(login_url="/admin-dashboard/login/")
def normalized_products(request):
    return render(
        request,
        "dashboard/normalization/products.html",
        get_product_mapping_context(request),
    )


@login_required(login_url="/admin-dashboard/login/")
def mapping_product_search(request):
    brand_id = request.GET.get("brand", "").strip()
    q = request.GET.get("q", "").strip()
    scope = request.GET.get("scope", "all").strip().lower()
    category_id = request.GET.get("category_id", "").strip()
    root_category_id = request.GET.get("root_category_id", "").strip()
    term_ids = [value for value in request.GET.getlist("term") if value.isdigit()]

    if not brand_id.isdigit():
        return JsonResponse({"results": []})

    products = find_mapping_products(
        int(brand_id),
        q=q,
        limit=30,
        scope=scope,
        category_id=int(category_id) if category_id.isdigit() else None,
        root_category_id=int(root_category_id) if root_category_id.isdigit() else None,
        term_ids=term_ids,
    )
    return JsonResponse({
        "results": [
            {
                "id": product.id,
                "name": product.normalized_name or product.canonical_name or f"Product {product.id}",
                "thumbnail_url": product.display_thumbnail_url,
                "category": product.category.name if getattr(product, "category", None) else None,
            }
            for product in products
        ]
    })
