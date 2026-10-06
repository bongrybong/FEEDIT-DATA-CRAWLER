from __future__ import annotations

from typing import Any

from .brand_mapping import (
    resolve_step01_brands,
)


def run_step01_ingestion(
    *,
    source_code: str,
    raw_document_id: int,
) -> dict[str, Any] | None:

    code = (
        source_code
        or ""
    ).strip().lower()

    # ========================================================
    # Platform ingestion
    # ========================================================

    if code == "musinsa":
        from pipeline.step01_ingestion.musinsa import (
            MusinsaIngestion,
        )

        result = MusinsaIngestion().run(
            raw_document_id=raw_document_id,
        )

    elif code == "kream":
        from pipeline.step01_ingestion.kream import (
            KreamIngestion,
        )

        result = KreamIngestion().run(
            raw_document_id=raw_document_id,
        )

    elif code == "musinsa_used":
        from pipeline.step01_ingestion.musinsa_used import (
            MusinsaUsedIngestion,
        )

        result = MusinsaUsedIngestion().run(
            raw_document_id=raw_document_id,
        )

    elif code == "zigzag":
        from pipeline.step01_ingestion.zigzag import (
            ZigzagIngestion,
        )

        result = ZigzagIngestion().run(
            raw_document_id=raw_document_id,
        )

    else:
        return None

    if result is None:
        return None

    # ========================================================
    # ProductSource IDs
    # ========================================================

    product_source_ids = list(
        dict.fromkeys(
            int(value)
            for value in (
                result.get(
                    "product_source_ids"
                )
                or []
            )
            if value not in (
                None,
                "",
            )
        )
    )

    result[
        "product_source_ids"
    ] = product_source_ids

    # ========================================================
    # STEP01 Brand Mapping
    #
    # BrandSource 생성 직후
    # FEEDIT Brand를 즉시 resolve한다.
    # ========================================================

    brand_result = resolve_step01_brands(
        product_source_ids
    )

    result["brand_mapping"] = {
        "total":
            brand_result.total,

        "already_mapped":
            brand_result.already_mapped,

        "mapped_by_source_name":
            brand_result.mapped_by_source_name,

        "mapped_by_brand_exact":
            brand_result.mapped_by_brand_exact,

        "unmapped":
            brand_result.unmapped,
    }

    return result