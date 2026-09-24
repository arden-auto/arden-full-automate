"""READ-ONLY: prints every SKU currently listed on this OnBuy account,
one per line between BEGIN/END markers (paged GET /v2/listings). Built
2026-09-14 to verify bulk deletions: intersect the output with a delete
list and whatever remains still has a live listing. GET quota only."""
import os

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import raise_for_status, with_retry

MAX_PAGES = int(os.getenv("PROBE_MAX_PAGES") or "200")


def main():
    use_sandbox = os.getenv("ONBUY_USE_SANDBOX", "false").strip().lower() == "true"
    onbuy = OnBuyClient(use_sandbox=use_sandbox)
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    skus, offset = [], 0
    for _ in range(MAX_PAGES):
        def _do(off=offset):
            resp = onbuy._send("GET", f"{BASE_URL}/listings", what=f"listings page {off}",
                               params={"site_id": onbuy.site_id, "limit": 100, "offset": off},
                               timeout=60)
            raise_for_status(resp, what=f"listings page {off}")
            return resp
        body = with_retry(_do, what=f"listings page {offset}", max_attempts=3).json()
        items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(items, list) or not items:
            break
        for item in items:
            sku = str((item or {}).get("sku") or "").strip()
            if sku:
                skus.append(sku)
        if len(items) < 100:
            break
        offset += 100
    print(f"Live listings: {len(skus)}")
    print("BEGIN-SKUS")
    for s in skus:
        print(s)
    print("END-SKUS")


if __name__ == "__main__":
    main()
