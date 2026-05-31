import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Optional
from urllib.parse import quote as urlquote

import httpx

AZURE_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AZURE_REGIONS = ["westus", "westus2", "westus3"]
MAX_PAGES_PER_REGION = 10
_ODATA_SAFE = "'(),"  # chars the Azure OData parser requires unencoded


def _parse_os(product_name: str) -> str:
    return "Windows" if "Windows" in product_name else "Linux"


def _normalize_timestamp(ts: Optional[str]) -> str:
    if not ts:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Trim sub-second precision and ensure Z suffix
    ts = ts.split(".")[0]
    if not ts.endswith("Z"):
        ts = ts.rstrip("+00:00") + "Z"
    if "T" not in ts:
        ts += "T00:00:00Z"
    return ts


async def _fetch_region(client: httpx.AsyncClient, region: str) -> List[Dict]:
    # priceType is not a real filterable field — spot VMs are identified by skuName containing 'Spot'.
    # Single quotes must NOT be percent-encoded (%27); the API rejects that encoding.
    filter_str = (
        f"serviceName eq 'Virtual Machines' "
        f"and armRegionName eq '{region}' "
        f"and contains(skuName, 'Spot')"
    )
    url: Optional[str] = f"{AZURE_PRICES_URL}?$filter={urlquote(filter_str, safe=_ODATA_SAFE)}"
    prices: List[Dict] = []
    pages = 0

    while url and pages < MAX_PAGES_PER_REGION:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("Items", []):
            retail_price = item.get("retailPrice", 0.0)
            if not retail_price or retail_price <= 0:
                continue
            # Skip dev/test discounted rows — only keep standard Consumption prices
            if item.get("type") != "Consumption":
                continue

            instance_type = item.get("armSkuName") or item.get("skuName", "unknown")

            prices.append(
                {
                    "provider": "azure",
                    "region": region,
                    "instance_type": instance_type,
                    "os": _parse_os(item.get("productName", "")),
                    "price_usd": retail_price,
                    "timestamp": _normalize_timestamp(item.get("effectiveStartDate")),
                }
            )

        url = data.get("NextPageLink")  # already fully-formed, use as-is
        pages += 1

    return prices


async def fetch_azure() -> List[Dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [_fetch_region(client, region) for region in AZURE_REGIONS]
        results = await asyncio.gather(*tasks)

    return [price for region_prices in results for price in region_prices]
