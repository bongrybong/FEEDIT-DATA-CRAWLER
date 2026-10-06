from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


DASHBOARD_PREFIX = "/admin-dashboard/"
CACHE_VERSION = "v1"


def _canonical_query(request) -> str:
    """refresh 파라미터를 제외한 정렬된 query string."""
    pairs = []
    for key in sorted(request.GET.keys()):
        if key == "refresh":
            continue
        for value in request.GET.getlist(key):
            pairs.append((key, value))
    return urlencode(pairs, doseq=True)


def _cache_key(request) -> str:
    user_id = getattr(getattr(request, "user", None), "pk", None) or "anon"
    session_key = getattr(getattr(request, "session", None), "session_key", None) or "nosession"
    query = _canonical_query(request)
    raw = f"{user_id}|{session_key}|{request.path}|{query}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"dashboard:page:{CACHE_VERSION}:{digest}"


def _serialize_response(response: HttpResponse) -> dict:
    headers = {
        key: value
        for key, value in response.items()
        if key.lower() not in {"set-cookie", "content-length"}
    }
    return {
        "status": response.status_code,
        "content": bytes(response.content),
        "headers": headers,
    }


def _restore_response(payload: dict) -> HttpResponse:
    response = HttpResponse(
        content=payload["content"],
        status=payload["status"],
    )
    for key, value in payload.get("headers", {}).items():
        response[key] = value
    response["X-FEEDIT-Cache"] = "HIT"
    return response


class DashboardCacheMiddleware:
    """
    FEEDIT 관리자 대시보드 GET HTML 응답 캐시.

    - /admin-dashboard/ 하위만 대상
    - 사용자 + 세션 + URL + query string 단위 캐시
    - ?refresh=1 이면 cache read를 건너뛰고 최신 응답으로 교체
    - POST/PUT/DELETE 등은 절대 캐시하지 않음
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = int(
            getattr(settings, "DASHBOARD_PAGE_CACHE_SECONDS", 120)
        )

    def __call__(self, request):
        if not self._is_cacheable_request(request):
            return self.get_response(request)

        key = _cache_key(request)
        force_refresh = request.GET.get("refresh") == "1"

        if not force_refresh:
            payload = cache.get(key)
            if payload is not None:
                return _restore_response(payload)

        response = self.get_response(request)

        if self._is_cacheable_response(response):
            cache.set(
                key,
                _serialize_response(response),
                self.timeout,
            )
            response["X-FEEDIT-Cache"] = (
                "REFRESH" if force_refresh else "MISS"
            )

        return response

    @staticmethod
    def _is_cacheable_request(request) -> bool:
        if request.method != "GET":
            return False
        if not request.path.startswith(DASHBOARD_PREFIX):
            return False
        return True

    @staticmethod
    def _is_cacheable_response(response) -> bool:
        if response.status_code != 200:
            return False
        content_type = response.get("Content-Type", "")
        return content_type.startswith("text/html")
