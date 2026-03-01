# 🤖 KYC Copilot — AI Pre-Submission Document Validator

> Inspired by 4 real KYC rejections at Questrade over 5 months. Built to make sure that never happens again.

## What It Does

KYC Copilot is an AI-powered validation system that checks documents **before** submission, catching every issue that would cause a compliance rejection — instantly, instead of waiting 2-4 business days.

### Problems It Solves (from real rejection emails)

| Rejection | Root Cause | KYC Copilot Prevention |
|-----------|-----------|------------------------|
| Photo ID — too blurry | Image quality check skipped | OpenCV blur detection pre-upload |
| Photo ID — DOB mismatch | No cross-validation with profile | Claude Vision OCR + profile comparison |
| W-8BEN — Section 3 address mismatch | User filled wrong address | AI field extraction + address matching |
| W-8BEN — Section 9 country missing | Incomplete form | Required field checklist validation |
| Bank statement — truncated account | Screenshot cut off number | Full document visibility check |

## Architecture

```
User Upload (Web / Mobile)
      │
      ▼
Cloud Load Balancer + Cloud Armor (WAF)
      │
      ▼
Cloud Run — KYC Copilot API (auto-scales 1→10 instances)
      │
      ├──▶ GCS (temp document storage, auto-delete 24h, CMEK encrypted)
      │
      ├──▶ Memorystore Redis (cache validations by file hash, TTL=1h)
      │
      └──▶ Anthropic Claude API (Vision + OCR analysis)
                 │
                 ▼
          ValidationResult → User Checklist (issues + suggestions)
```

## GCP Services Used

| AWS Equivalent | GCP Service | Purpose |
|---------------|-------------|---------|
| ECS Fargate | Cloud Run | Serverless container hosting |
| S3 | Cloud Storage (GCS) | Temp document storage |
| ElastiCache | Memorystore (Redis) | Validation result cache |
| Secrets Manager | Secret Manager | Anthropic API key |
| ECR | Artifact Registry | Docker image registry |
| WAF | Cloud Armor | Rate limiting, SQLi protection |
| CloudWatch | Cloud Monitoring | Alerting and dashboards |
| CloudFront | Cloud CDN | Fast uploads globally |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/your-org/kyc-copilot
cd kyc-copilot

# 2. Configure
cp .env.example .env
# Add: ANTHROPIC_API_KEY, GCP_PROJECT_ID, GCS_BUCKET_NAME

# 3. Run locally (Docker)
docker-compose up

# 4. Deploy to Cloud Run
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud run deploy kyc-copilot \
  --source . \
  --region northamerica-northeast1 \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --set-env-vars GCS_BUCKET_NAME=kyc-copilot-temp-docs \
  --allow-unauthenticated

# 5. Provision full infra with Terraform
cd infrastructure/terraform
terraform init
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/validate` | POST | Validate document + user profile |
| `/validation/{id}` | GET | Retrieve past validation |
| `/analytics/summary` | GET | Aggregate stats |
| `/document-types` | GET | Supported doc types + requirements |
| `/health` | GET | Health check |

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Cost Estimate (GCP, 10K validations/month)

| Service | Monthly Cost |
|---------|-------------|
| Cloud Run (auto-scale, ~5% utilization) | ~$8 |
| Claude API (claude-opus-4-5, ~1.5K tokens/call) | ~$150 |
| Cloud Storage (temp, 24h TTL) | ~$0.10 |
| Memorystore Redis (Basic, 1GB) | ~$35 |
| Cloud Armor | ~$5 |
| Cloud Monitoring | ~$0 (free tier) |
| **Total** | **~$198/month** |
| **Cost per validation** | **~$0.020** |

**Tip:** Use `claude-haiku-4-5` for initial image quality pre-screening — ~40% cost reduction at scale.

## License
MIT
