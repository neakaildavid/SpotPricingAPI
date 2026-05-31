"""
GCP spot (preemptible) pricing via the public Cloud Billing Catalog API.

Pricing in the catalog is expressed as per-vCPU-hour and per-GB-RAM-hour.
We combine those rates with known machine-type specs to produce per-instance prices.
Results are cached in memory for 5 minutes to avoid re-fetching thousands of SKUs.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import httpx

GCP_SERVICE_ID = "6F81-5844-456A"  # Compute Engine
GCP_SKUS_URL = f"https://cloudbilling.googleapis.com/v1/services/{GCP_SERVICE_ID}/skus"
GCP_REGIONS = ["us-west1", "us-west2", "us-west3", "us-west4"]

# (family_key, vcpus, ram_gb)
_MACHINE_SPECS: Dict[str, List[Tuple[str, int, float]]] = {
    "N1": [
        ("n1-standard-1", 1, 3.75),
        ("n1-standard-2", 2, 7.5),
        ("n1-standard-4", 4, 15.0),
        ("n1-standard-8", 8, 30.0),
        ("n1-standard-16", 16, 60.0),
        ("n1-standard-32", 32, 120.0),
    ],
    "N2": [
        ("n2-standard-2", 2, 8.0),
        ("n2-standard-4", 4, 16.0),
        ("n2-standard-8", 8, 32.0),
        ("n2-standard-16", 16, 64.0),
        ("n2-standard-32", 32, 128.0),
        ("n2-standard-48", 48, 192.0),
    ],
    "N2D": [
        ("n2d-standard-2", 2, 8.0),
        ("n2d-standard-4", 4, 16.0),
        ("n2d-standard-8", 8, 32.0),
        ("n2d-standard-16", 16, 64.0),
        ("n2d-standard-32", 32, 128.0),
    ],
    "E2": [
        ("e2-standard-2", 2, 8.0),
        ("e2-standard-4", 4, 16.0),
        ("e2-standard-8", 8, 32.0),
        ("e2-standard-16", 16, 64.0),
        ("e2-standard-32", 32, 128.0),
    ],
    "C2": [
        ("c2-standard-4", 4, 16.0),
        ("c2-standard-8", 8, 32.0),
        ("c2-standard-16", 16, 64.0),
        ("c2-standard-30", 30, 120.0),
        ("c2-standard-60", 60, 240.0),
    ],
    "C2D": [
        ("c2d-standard-2", 2, 8.0),
        ("c2d-standard-4", 4, 16.0),
        ("c2d-standard-8", 8, 32.0),
        ("c2d-standard-16", 16, 64.0),
        ("c2d-standard-32", 32, 128.0),
    ],
}

# Simple in-memory cache
_sku_cache: Optional[List[Dict]] = None
_sku_cache_at: Optional[datetime] = None
_CACHE_TTL = timedelta(minutes=5)


def _price_per_hour(pricing_info: List[Dict]) -> float:
    """Extract the starting-tier hourly unit price (units + nanos)."""
    for pi in pricing_info:
        for rate in pi.get("pricingExpression", {}).get("tieredRates", []):
            if rate.get("startUsageAmount", 1) == 0:
                up = rate.get("unitPrice", {})
                units = float(up.get("units") or 0)
                nanos = float(up.get("nanos") or 0)
                return units + nanos / 1e9
    return 0.0


def _machine_family(description: str) -> Optional[str]:
    d = description.upper()
    # Order matters: check longer/more-specific names first
    if "N2D" in d:
        return "N2D"
    if "C2D" in d:
        return "C2D"
    if "N2 " in d or "N2\t" in d or d.endswith("N2"):
        return "N2"
    if "C2 " in d or "C2\t" in d or d.endswith("C2"):
        return "C2"
    if "N1 PREDEFINED" in d or "N1 STANDARD" in d or "N1 CUSTOM" in d:
        return "N1"
    if "E2 " in d or "E2\t" in d or d.endswith("E2"):
        return "E2"
    return None


def _applicable_regions(service_regions: List[str]) -> List[str]:
    """Return the subset of our target GCP regions this SKU covers."""
    if "americas" in service_regions:
        return GCP_REGIONS
    return [r for r in service_regions if r in GCP_REGIONS]


async def _fetch_preemptible_skus(api_key: str) -> List[Dict]:
    global _sku_cache, _sku_cache_at

    if not api_key:
        raise ValueError(
            "GCP_API_KEY is not set. "
            "Get a free key at console.cloud.google.com → APIs & Services → Credentials → Create API Key, "
            "then enable the Cloud Billing API for your project."
        )

    now = datetime.now(timezone.utc)
    if _sku_cache is not None and _sku_cache_at and (now - _sku_cache_at) < _CACHE_TTL:
        return _sku_cache

    params: Dict = {"currencyCode": "USD", "pageSize": 5000, "key": api_key}

    collected: List[Dict] = []
    max_pages = 5

    async with httpx.AsyncClient(timeout=60.0) as client:
        for _ in range(max_pages):
            resp = await client.get(GCP_SKUS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            for sku in data.get("skus", []):
                cat = sku.get("category", {})
                if (
                    cat.get("resourceFamily") == "Compute"
                    and cat.get("usageType") in ("Preemptible", "Spot")
                    and _applicable_regions(sku.get("serviceRegions", []))
                ):
                    collected.append(sku)

            token = data.get("nextPageToken")
            if not token:
                break
            params = {"currencyCode": "USD", "pageSize": 5000, "key": api_key, "pageToken": token}

    _sku_cache = collected
    _sku_cache_at = now
    return collected


async def fetch_gcp(api_key: str = "") -> List[Dict]:
    skus = await _fetch_preemptible_skus(api_key)

    # {region: {family: {cpu: float, ram: float}}}
    rates: Dict[str, Dict[str, Dict[str, float]]] = {}

    for sku in skus:
        desc = sku.get("description", "")
        family = _machine_family(desc)
        if family is None:
            continue

        desc_up = desc.upper()
        is_cpu = "CORE" in desc_up or " CPU" in desc_up
        is_ram = "RAM" in desc_up or "MEMORY" in desc_up
        if not (is_cpu or is_ram):
            continue

        price = _price_per_hour(sku.get("pricingInfo", []))
        if price <= 0:
            continue

        for region in _applicable_regions(sku.get("serviceRegions", [])):
            rates.setdefault(region, {}).setdefault(family, {"cpu": 0.0, "ram": 0.0})
            entry = rates[region][family]
            if is_cpu and entry["cpu"] == 0.0:
                entry["cpu"] = price
            elif is_ram and entry["ram"] == 0.0:
                entry["ram"] = price

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results: List[Dict] = []

    for region, families in rates.items():
        for family, rate in families.items():
            cpu_rate = rate["cpu"]
            ram_rate = rate["ram"]
            if cpu_rate == 0.0 and ram_rate == 0.0:
                continue

            for instance_type, vcpus, ram_gb in _MACHINE_SPECS.get(family, []):
                total = round(cpu_rate * vcpus + ram_rate * ram_gb, 6)
                results.append(
                    {
                        "provider": "gcp",
                        "region": region,
                        "instance_type": instance_type,
                        "os": "Linux",
                        "price_usd": total,
                        "timestamp": timestamp,
                    }
                )

    return results
