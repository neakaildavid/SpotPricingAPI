import asyncio
import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

from providers.aws import fetch_aws
from providers.azure import fetch_azure
from providers.gcp import fetch_gcp

app = FastAPI(
    title="Spot Pricing API",
    description="Real-time spot/preemptible instance pricing for AWS, Azure, and GCP (North America West).",
    version="1.0.0",
)

VALID_PROVIDERS = {"aws", "azure", "gcp"}


class SpotPriceRecord(BaseModel):
    provider: str
    region: str
    instance_type: str
    os: str
    price_usd: float
    timestamp: str


class PricesResponse(BaseModel):
    count: int
    prices: List[SpotPriceRecord]
    errors: Optional[Dict[str, str]] = None


class HealthResponse(BaseModel):
    status: str
    version: str


@app.get("/", include_in_schema=False)
async def frontend():
    return FileResponse("static/index.html")


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/prices", response_model=PricesResponse, tags=["pricing"])
async def get_prices(
    provider: Optional[str] = Query(
        None,
        description="Filter by cloud provider. One of: aws, azure, gcp.",
    ),
    region: Optional[str] = Query(
        None,
        description="Filter by region (e.g. us-west-2). Combine with provider for best results.",
    ),
):
    if provider and provider not in VALID_PROVIDERS:
        return PricesResponse(
            count=0,
            prices=[],
            errors={"request": f"Invalid provider '{provider}'. Valid values: {', '.join(sorted(VALID_PROVIDERS))}"},
        )

    aws_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    gcp_api_key = os.getenv("GCP_API_KEY", "")

    # Build list of (name, coroutine) for the requested providers
    provider_tasks: List[tuple] = []
    if provider is None or provider == "aws":
        provider_tasks.append(("aws", fetch_aws(aws_key, aws_secret)))
    if provider is None or provider == "azure":
        provider_tasks.append(("azure", fetch_azure()))
    if provider is None or provider == "gcp":
        provider_tasks.append(("gcp", fetch_gcp(gcp_api_key)))

    names = [p[0] for p in provider_tasks]
    coros = [p[1] for p in provider_tasks]

    results = await asyncio.gather(*coros, return_exceptions=True)

    all_prices: List[Dict] = []
    errors: Dict[str, str] = {}

    for name, result in zip(names, results):
        if isinstance(result, Exception):
            errors[name] = f"{type(result).__name__}: {result}"
        else:
            all_prices.extend(result)

    if region:
        all_prices = [p for p in all_prices if p.get("region") == region]

    all_prices.sort(key=lambda p: p.get("price_usd", 0.0))

    return PricesResponse(
        count=len(all_prices),
        prices=[SpotPriceRecord(**p) for p in all_prices],
        errors=errors or None,
    )
