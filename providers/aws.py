import asyncio
from datetime import datetime, timezone
from typing import List, Dict

import boto3
from botocore.exceptions import NoCredentialsError, ClientError

AWS_REGIONS = ["us-west-1", "us-west-2"]

_PRODUCT_DESC_MAP = {
    "Linux/UNIX": "Linux",
    "Linux/UNIX (Amazon VPC)": "Linux",
    "Windows": "Windows",
    "Windows (Amazon VPC)": "Windows",
    "Red Hat Enterprise Linux": "RHEL",
    "SUSE Linux": "SUSE",
}


def _region_from_az(az: str) -> str:
    """Strip trailing AZ letter: 'us-west-2a' → 'us-west-2'."""
    if az and az[-1].isalpha() and len(az) > 2 and az[-2].isdigit():
        return az[:-1]
    return az


def _fetch_region(region: str, access_key: str, secret_key: str) -> List[Dict]:
    kwargs = {"region_name": region}
    if access_key:
        kwargs["aws_access_key_id"] = access_key
    if secret_key:
        kwargs["aws_secret_access_key"] = secret_key

    client = boto3.client("ec2", **kwargs)
    paginator = client.get_paginator("describe_spot_price_history")

    now = datetime.now(timezone.utc)
    prices: List[Dict] = []

    page_iterator = paginator.paginate(
        StartTime=now,
        ProductDescriptions=list(_PRODUCT_DESC_MAP.keys()),
        PaginationConfig={"MaxItems": 2000, "PageSize": 1000},
    )

    for page in page_iterator:
        for item in page.get("SpotPriceHistory", []):
            try:
                price_usd = float(item["SpotPrice"])
            except (KeyError, ValueError):
                continue

            if price_usd <= 0:
                continue

            ts = item.get("Timestamp", now)
            timestamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(ts, "strftime") else str(ts)

            product_desc = item.get("ProductDescription", "Linux/UNIX")
            os_type = _PRODUCT_DESC_MAP.get(product_desc, "Linux")

            az = item.get("AvailabilityZone", "")
            prices.append(
                {
                    "provider": "aws",
                    "region": _region_from_az(az) or region,
                    "instance_type": item.get("InstanceType", "unknown"),
                    "os": os_type,
                    "price_usd": price_usd,
                    "timestamp": timestamp,
                }
            )

    return prices


async def fetch_aws(access_key: str = "", secret_key: str = "") -> List[Dict]:
    tasks = [
        asyncio.to_thread(_fetch_region, region, access_key, secret_key)
        for region in AWS_REGIONS
    ]
    results = await asyncio.gather(*tasks)
    return [price for region_prices in results for price in region_prices]
