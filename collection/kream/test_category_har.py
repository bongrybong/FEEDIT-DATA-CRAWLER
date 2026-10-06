"""오프라인 HAR fixture 검증: python -m collection.kream.test_category_har /path/to/kream.co.kr.har"""
import json
import sys
from .discovery import KreamDiscoveryCollector


def main(path):
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        if "/api/screens/categories/50/" not in url:
            continue
        body = entry["response"]["content"].get("text")
        if not body:
            continue
        payload = json.loads(body)
        products = KreamDiscoveryCollector.extract_products(payload)
        pagination = KreamDiscoveryCollector.extract_pagination(payload)
        print("URL:", url.split("?")[0])
        print("PRODUCTS:", len(products))
        print("FIRST:", products[0] if products else None)
        print("PAGINATION:", pagination)
        assert products, "상품을 추출하지 못했습니다"


if __name__ == "__main__":
    main(sys.argv[1])
