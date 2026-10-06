from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone

from collection.common.runner import run_target
from core.models import CrawlRun, CrawlTarget


logger = logging.getLogger(__name__)


# 처음에는 작게 시작한다.
# 안정화 후 5, 10 등으로 올릴 수 있다.
DISPATCH_BATCH_SIZE = 2


@shared_task(
    name="core.celery_smoke_test",
)
def celery_smoke_test(
    message: str = "FEEDIT DATA CRAWLER",
) -> dict[str, Any]:
    return {
        "ok": True,
        "message": message,
    }


@shared_task(
    bind=True,
    name="core.run_live_target",
)
def run_live_target(
    self,
    target_id: int,
) -> dict[str, Any]:
    """
    실제 CrawlTarget 1개를 실행한다.

    수집/RAW/STEP01에 대한 책임은 전부 runner에 있고,
    Celery task는 runner 호출만 담당한다.
    """

    close_old_connections()

    logger.info(
        "Celery crawl start. target_id=%s task_id=%s",
        target_id,
        self.request.id,
    )
    try:
        from collection.common.runner import run_target

        result = run_target(
            target_id=target_id,
        )

        step01 = (
            result.get("step01_ingestion")
            or {}
        )

        product_source_ids = list(dict.fromkeys(
            int(value)
            for value in (
                step01.get("product_source_ids")
                or []
            )
            if value not in (None, "")
        ))

        if product_source_ids:
            run_product_agent_after_collection.delay(
                product_source_ids
            )

            logger.info(
                "Product Agent queued. "
                "target_id=%s count=%s",
                target_id,
                len(product_source_ids),
            )

        logger.info(
            "Celery crawl success. "
            "target_id=%s task_id=%s",
            target_id,
            self.request.id,
        )

        return result

    finally:
        close_old_connections()


@shared_task(
    name="core.dispatch_due_targets",
)
def dispatch_due_targets(
    batch_size: int = DISPATCH_BATCH_SIZE,
) -> dict[str, Any]:
    """
    실행 시각이 도래한 CrawlTarget을 찾아 Celery queue에 넣는다.

    실제 크롤링은 하지 않는다.
    """

    close_old_connections()

    now = timezone.now()

    # --------------------------------------------------------
    # 1. 실행 가능한 due target 후보
    # --------------------------------------------------------

    due_targets = list(
        CrawlTarget.objects
        .filter(
            is_active=True,
            next_crawl_at__lte=now,
        )
        .select_related("source")
        .order_by(
            "-priority",
            "next_crawl_at",
            "id",
        )
        [: max(batch_size * 5, 20)]
    )

    queued: list[dict[str, Any]] = []
    skipped_running: list[dict[str, Any]] = []

    # --------------------------------------------------------
    # 2. RUNNING 여부 검사 후 queue
    # --------------------------------------------------------

    for target in due_targets:

        if len(queued) >= batch_size:
            break

        is_running = (
            CrawlRun.objects
            .filter(
                crawl_target=target,
                status="RUNNING",
            )
            .exists()
        )

        if is_running:
            skipped_running.append(
                {
                    "target_id": target.id,
                    "name": target.name,
                }
            )
            continue

        task = run_live_target.delay(
            target.id
        )

        queued.append(
            {
                "target_id": target.id,
                "name": target.name,
                "source": target.source.code,
                "celery_task_id": task.id,
            }
        )

        logger.info(
            (
                "Crawl queued. "
                "target_id=%s source=%s "
                "task_id=%s"
            ),
            target.id,
            target.source.code,
            task.id,
        )

    result = {
        "checked_at": now.isoformat(),
        "due_count_checked": len(due_targets),
        "queued_count": len(queued),
        "skipped_running_count": len(
            skipped_running
        ),
        "queued": queued,
        "skipped_running": skipped_running,
    }

    logger.info(
        (
            "Dispatcher finished. "
            "due_checked=%s queued=%s "
            "skipped_running=%s"
        ),
        result["due_count_checked"],
        result["queued_count"],
        result["skipped_running_count"],
    )

    close_old_connections()

    return result

@shared_task(
    bind=True,
    name="core.run_product_agent_after_collection",
    max_retries=2,
    soft_time_limit=60 * 20,
    time_limit=60 * 25,
)
def run_product_agent_after_collection(
    self,
    product_source_ids: list[int],
) -> dict[str, Any]:
    """
    Collection → STEP01 → STEP02 완료 후
    이번 수집에서 처리된 ProductSource만 상품 매핑/승격한다.

    전체 DB batch를 돌리지 않는다.
    """

    close_old_connections()

    ids = list(dict.fromkeys(
        int(value)
        for value in (product_source_ids or [])
        if value not in (None, "")
    ))

    if not ids:
        return {
            "status": "SKIPPED",
            "reason": "NO_PRODUCT_SOURCE",
            "target_count": 0,
        }

    from pipeline.product_agents import (
        run_product_agent_fast,
    )

    success = 0
    failed = 0
    results = []
    errors = []

    try:
        for index, product_source_id in enumerate(
            ids,
            start=1,
        ):
            logger.info(
                "Product Agent [%s/%s] ps=%s",
                index,
                len(ids),
                product_source_id,
            )

            try:
                result = run_product_agent_fast(
                    product_source_id=product_source_id,
                    apply=True,
                    verbose=False,
                )

                results.append(result)
                success += 1

            except Exception as exc:
                failed += 1

                errors.append({
                    "product_source_id":
                        product_source_id,
                    "error_type":
                        type(exc).__name__,
                    "error_message":
                        str(exc),
                })

                logger.exception(
                    "Product Agent failed. ps=%s",
                    product_source_id,
                )

            finally:
                close_old_connections()

        return {
            "status":
                "SUCCESS"
                if failed == 0
                else "PARTIAL",
            "target_count": len(ids),
            "success": success,
            "failed": failed,
            "results": results,
            "errors": errors,
        }

    finally:
        close_old_connections()