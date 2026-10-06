from __future__ import annotations

from django.db.models import Prefetch

from core.models import Brand, Product, ProductSource

from .matcher import compare_normalized_name
from .scorer import calculate_score
from .types import BrandMatchingResult, ProductCandidate, ProductSourceMatch


class ProductMatchingPipeline:
    """Brand 하나 안에서만 후보를 계산하는 read-only v1 파이프라인."""

    def __init__(self, *, brand_id: int, top_k: int = 5):
        self.brand_id = int(brand_id)
        self.top_k = max(1, int(top_k))

    def _get_brand(self):
        return Brand.objects.get(pk=self.brand_id)

    def _get_products(self):
        linked_sources = (
            ProductSource.objects
            .exclude(
                source__code__iexact="musinsa_used",
            )
            .exclude(
                source__code__iexact="musinsa-used",
            )
            .select_related("source")
            .order_by("id")
        )

        return list(
            Product.objects
            .filter(
                brand_id=self.brand_id,
            )
            .prefetch_related(
                Prefetch(
                    "sources",
                    queryset=linked_sources,
                )
            )
            .order_by("id")
        )
        
    def _get_unmapped_sources(self):
        return list(
            ProductSource.objects
            .filter(
                source_brand__brand_id=self.brand_id,
                product_id__isnull=True,
            )
            .exclude(
                source__code__iexact="musinsa_used",
            )
            .exclude(
                source__code__iexact="musinsa-used",
            )
            .select_related(
                "source",
                "source_brand",
                "source_category",
            )
            .order_by(
                "source_id",
                "id",
            )
        )

    @staticmethod
    def _product_names(product):
        names = [
            getattr(product, "normalized_name", None),
            getattr(product, "canonical_name", None),
        ]
        for source in product.sources.all():
            names.append(getattr(source, "normalized_name", None))
        return names

    def _match_one(self, product_source, products):
        source_name = (
            getattr(product_source, "normalized_name", None)
            or getattr(product_source, "source_name", None)
        )
        candidates = []

        for product in products:
            evidence = compare_normalized_name(
                source_name,
                self._product_names(product),
            )
            score = calculate_score(evidence)

            candidates.append(
                ProductCandidate(
                    product_id=product.id,
                    product_code=getattr(product, "product_code", None),
                    product_name=(
                        getattr(product, "normalized_name", None)
                        or getattr(product, "canonical_name", None)
                        or ""
                    ),
                    score=score,
                    evidence=evidence,
                )
            )

        candidates.sort(key=lambda x: (-x.score, x.product_id))

        return ProductSourceMatch(
            product_source_id=product_source.id,
            source_code=(
                getattr(product_source.source, "code", "")
                if product_source.source_id else ""
            ),
            source_name=getattr(product_source, "source_name", "") or "",
            normalized_name=getattr(product_source, "normalized_name", None),
            candidates=candidates[:self.top_k],
        )

    def run(self, *, limit: int | None = None):
        brand = self._get_brand()
        products = self._get_products()
        unmapped_sources = self._get_unmapped_sources()
        total_unmapped = len(unmapped_sources)

        if limit is not None:
            unmapped_sources = unmapped_sources[:max(0, int(limit))]

        return BrandMatchingResult(
            brand_id=brand.id,
            brand_name=getattr(brand, "name", ""),
            product_count=len(products),
            unmapped_count=total_unmapped,
            matches=[
                self._match_one(ps, products)
                for ps in unmapped_sources
            ],
        )
