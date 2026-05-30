# SpotPricingAPI

A FastAPI service that fetches real-time spot/preemptible instance pricing from AWS, Azure, and GCP, filtered to North America West regions.

## Regions covered

| Provider | Regions |
|----------|---------|
| AWS      | us-west-1, us-west-2 |
| Azure    | westus, westus2, westus3 |
| GCP      | us-west1, us-west2, us-west3, us-west4 |

## Prerequisites

- Python 3.10+
- AWS credentials with `ec2:DescribeSpotPriceHistory` permission (required for AWS pricing)

## Setup

**1. Clone and install dependencies**

```bash
git clone <repo-url>
cd SpotPricingAPI
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**2. Configure credentials**

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
# Required for AWS spot pricing
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-west-2

# Optional — public GCP catalog works without it, but may be rate-limited
GCP_API_KEY=
```

Azure uses the public Retail Prices API — no credentials needed.  
GCP uses the public Cloud Billing Catalog API — an API key is optional but recommended to avoid rate limiting.

**3. Run the server**

```bash
uvicorn main:app --reload --port 8000
```

Interactive API docs: http://localhost:8000/docs

---

## Endpoints

### `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "version": "1.0.0"}
```

---

### `GET /prices` — all providers, all regions

```bash
curl http://localhost:8000/prices
```

```json
{
  "count": 847,
  "prices": [
    {
      "provider": "gcp",
      "region": "us-west1",
      "instance_type": "e2-standard-2",
      "os": "Linux",
      "price_usd": 0.014,
      "timestamp": "2026-05-30T12:00:00Z"
    },
    ...
  ]
}
```

---

### `GET /prices?provider=aws` — single provider

```bash
curl "http://localhost:8000/prices?provider=aws"
curl "http://localhost:8000/prices?provider=azure"
curl "http://localhost:8000/prices?provider=gcp"
```

---

### `GET /prices?provider=aws&region=us-west-2` — provider + region

```bash
curl "http://localhost:8000/prices?provider=aws&region=us-west-2"
curl "http://localhost:8000/prices?provider=azure&region=westus2"
curl "http://localhost:8000/prices?provider=gcp&region=us-west1"
```

---

## Response schema

```json
{
  "count": 42,
  "prices": [
    {
      "provider": "aws",
      "region": "us-west-2",
      "instance_type": "m5.xlarge",
      "os": "Linux",
      "price_usd": 0.0412,
      "timestamp": "2026-05-30T12:00:00Z"
    }
  ],
  "errors": {
    "aws": "NoCredentialsError: Unable to locate credentials"
  }
}
```

- `prices` is sorted by `price_usd` ascending.
- `errors` is omitted when all providers succeed; otherwise it lists which providers failed and why. Results from healthy providers are still returned.

---

## Implementation notes

| Provider | Data source | Auth |
|----------|-------------|------|
| AWS | `boto3` `describe_spot_price_history` | AWS credentials in `.env` |
| Azure | [Retail Prices API](https://prices.azure.com/api/retail/prices) | None |
| GCP | [Cloud Billing Catalog API](https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus) | Optional API key |

**GCP pricing note:** The billing catalog expresses compute pricing as per-vCPU-hour and per-GB-RAM-hour rates. This service combines those with known machine-type specs (N1, N2, N2D, E2, C2, C2D) to derive per-instance hourly prices. GCP SKUs are cached in memory for 5 minutes since the catalog contains thousands of entries.

**Parallelism:** All three providers are fetched concurrently via `asyncio.gather`. boto3 calls run in a thread pool via `asyncio.to_thread` to avoid blocking the event loop.
