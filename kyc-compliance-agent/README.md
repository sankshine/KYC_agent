# 🔍 KYC Review Agent — AI Compliance Review Copilot

> Flipping the perspective: instead of helping the user submit, help the compliance team review at 10x speed.

## What It Does

KYC Review Agent is an internal AI tool for compliance teams. Instead of a human agent manually comparing each document to the applicant's profile and writing rejection emails, the AI does the first-pass review in seconds — with confidence scores, specific evidence, regulatory citations, and pre-drafted rejection emails.

### The Before/After

| | Before (Manual) | After (AI Review Agent) |
|--|--|--|
| Time per review | ~12 minutes | ~8 seconds |
| Reviews per agent/day | ~40 | ~400+ |
| Rejection email | Written from scratch | AI-drafted, agent reviews & sends |
| Audit trail | Manual notes | Auto-generated with confidence scores |
| Edge cases | All handled manually | Escalated to senior agent |

## Decision Framework

```
Submission (Pub/Sub message)
      │
      ▼
Cloud Run — KYC Review Agent
      │
      ▼
Claude Vision Review (5-criteria scoring)
      │
      ├─ Fraud indicator + confidence > 70% ─────▶ FRAUD_FLAG
      │                                            (Pub/Sub alert → legal)
      ├─ Confidence > 95%, zero flags ───────────▶ AUTO_APPROVE
      │
      ├─ Confidence 70–95% ──────────────────────▶ RECOMMEND_APPROVE/REJECT
      │                                            (agent confirms)
      └─ Confidence < 50% ───────────────────────▶ ESCALATE (senior agent)
```

## GCP Architecture

```
Submission intake
      │
      ▼
Cloud Pub/Sub Topic: kyc-review-submissions
      │
      ▼ (push subscription)
Cloud Run — KYC Review Agent (4 vCPU, 8GB, 1–20 instances)
      │
      ├──▶ Cloud Storage (GCS) — document storage (reviewed/, 90-day retention)
      │
      ├──▶ Memorystore Redis (HA, 2GB) — real-time dashboard queue
      │
      ├──▶ Cloud SQL PostgreSQL (REGIONAL HA) — immutable audit trail
      │
      ├──▶ Anthropic Claude API — Vision review
      │
      └──▶ Pub/Sub: kyc-fraud-alerts ──▶ Push → compliance email webhook
                                         
```


## Quick Start

```bash
git clone https://github.com/your-org/kyc-review-agent
cd kyc-review-agent

cp .env.example .env
# Add: ANTHROPIC_API_KEY, GCP_PROJECT_ID, GCS_BUCKET_NAME, etc.

# Run locally
docker-compose up

# Deploy to Cloud Run
gcloud run deploy kyc-review-agent \
  --source . \
  --region northamerica-northeast1 \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --min-instances 1 \
  --cpu 4 --memory 8Gi \
  --no-allow-unauthenticated  # Internal only

# Full GCP infra
cd infrastructure/terraform
terraform init
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/review` | POST | Submit document for AI review |
| `/queue` | GET | Get compliance review queue |
| `/queue/{id}` | GET | Get specific submission detail |
| `/queue/{id}/decision` | POST | Record human agent decision |
| `/analytics` | GET | Productivity metrics |
| `/health` | GET | Health check |

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Cost Estimate (GCP, 50K reviews/month — enterprise)

| Service | Monthly Cost |
|---------|-------------|
| Cloud Run (4 vCPU × 5 instances avg) | ~$290 |
| Claude API (claude-opus-4-5, ~2K tokens/review) | ~$3,750 |
| Cloud SQL PostgreSQL (REGIONAL HA, db-g1-small) | ~$55 |
| Memorystore Redis (Standard HA, 2GB) | ~$116 |
| Cloud Storage (250GB, 90-day retention) | ~$5.75 |
| Pub/Sub (50K messages + fraud alerts) | ~$0.04 |
| Cloud Monitoring + Logging | ~$15 |
| **Total** | **~$4,232/month** |
| **Cost per review** | **~$0.085** |
| **Manual cost** (50K × $4.00) | **$200,000/month** |
| **Annual savings** | **~$2.35M/year** |

## License
MIT
