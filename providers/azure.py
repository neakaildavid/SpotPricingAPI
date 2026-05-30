import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Optional

import httpx

AZURE_PRICES_URL = "https://prices.azure.com/api/retail/prices"
AZURE_REGIONS = ["westus", "westus2", "westus3"]
MAX_PAGES_PER_REGION = 10


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
    filter_str = (
        f"serviceName eq 'Virtual Machines' "
        f"and priceType eq 'Spot' "
        f"and armRegionName eq '{region}'"
    )
    params: Optional[Dict] = {"$filter": filter_str}
    url: Optional[str] = AZURE_PRICES_URL
    prices: List[Dict] = []
    pages = 0

    while url and pages < MAX_PAGES_PER_REGION:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("Items", []):
            retail_price = item.get("retailPrice", 0.0)
            if not retail_price or retail_price <= 0:
                continue

            # armSkuName is the canonical instance type (e.g. Standard_D2s_v3)
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

        # NextPageLink already includes all query params
        url = data.get("NextPageLink")
        params = None
        pages += 1

    return prices


async def fetch_azure() -> List[Dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [_fetch_region(client, region) for region in AZURE_REGIONS]
        results = await asyncio.gather(*tasks)

    return [price for region_prices in results for price in region_prices]
