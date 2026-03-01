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
Submission Arrives
      │
      ▼
AI Reviews Document  (claude-opus-4-5 Vision)
      │
      ├─ Overall Confidence > 95% + No flags ──▶ AUTO_APPROVE
      │
      ├─ Confidence 70-95% + No errors ────────▶ RECOMMEND_APPROVE
      │                                          (agent confirms)
      ├─ Any error flags detected ──────────────▶ RECOMMEND_REJECT
      │                                          (agent reviews draft email)
      ├─ Confidence < 50% ──────────────────────▶ ESCALATE
      │                                          (senior agent)
      └─ Fraud indicators detected ─────────────▶ FRAUD_FLAG
                                                  (compliance + legal)
```

## Architecture

```
                         ┌─────────────────────────────────────┐
                         │        Compliance Dashboard           │
                         │  (React + real-time WebSocket queue) │
                         └──────────────┬──────────────────────┘
                                        │
                         ┌──────────────▼──────────────────────┐
                         │       FastAPI Backend (Port 8001)    │
                         │  POST /review                        │
                         │  GET  /queue                         │
                         │  POST /queue/{id}/decision           │
                         │  GET  /analytics                     │
                         └──────────────┬──────────────────────┘
                                        │
                         ┌──────────────▼──────────────────────┐
                         │         KYCReviewAgent               │
                         │  • Claude Vision API                 │
                         │  • Business rule engine              │
                         │  • Decision framework                │
                         │  • Email draft generator             │
                         └──────────────┬──────────────────────┘
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
              PostgreSQL           Redis Cache           S3 Storage
          (submission audit)   (queue/real-time)    (document archive)
```

## Quick Start

```bash
git clone https://github.com/your-org/kyc-review-agent
cd kyc-review-agent

cp .env.example .env
# Add ANTHROPIC_API_KEY

docker-compose up

# Submit a document for review
curl -X POST "http://localhost:8001/review" \
  -F "document_type=photo_id" \
  -F "file=@applicant_id.jpg" \
  -F "full_name=John Doe" \
  -F "date_of_birth=1985-06-15" \
  -F "address=456 Oak Ave" \
  -F "city=Vancouver" \
  -F "province=BC" \
  -F "postal_code=V6B1A1" \
  -F "previous_attempts=2" \
  -F "previous_rejection_reasons=Blurry image,DOB mismatch"
```

## Sample Review Packet Output

```json
{
  "submission_id": "uuid-...",
  "dashboard_entry": {
    "ai_decision": "recommend_reject",
    "confidence": "88%",
    "status_color": "orange",
    "flag_count": 2
  },
  "flags": [
    {
      "issue_id": "F001",
      "category": "data_mismatch",
      "description": "Date of birth on document (1985-06-15) does not match profile (1985-06-20)",
      "confidence": 0.94,
      "evidence": "Section showing DOB field clearly states June 15",
      "regulatory_ref": "FINTRAC PCMLTFR s.64(1)(b)"
    }
  ],
  "draft_rejection_email": "Subject: Action Required...",
  "processing_time_seconds": 7.3
}
```

## Infrastructure (AWS)

```
SQS Queue → Lambda Trigger → ECS Task (Review Agent)
                           → RDS PostgreSQL (audit log)
                           → ElastiCache (real-time dashboard)
                           → S3 (document storage, 90-day retention)
                           → SNS → Email (fraud alerts)
```

## Cost Estimate (AWS, 50K reviews/month — enterprise scale)

| Service | Monthly Cost |
|---------|-------------|
| ECS Fargate (4 vCPU, 8GB) | ~$116 |
| Claude API (claude-opus-4-5, ~2K tokens/review) | ~$750 |
| RDS PostgreSQL (db.t3.medium) | ~$50 |
| ElastiCache Redis | ~$30 |
| S3 (document storage) | ~$12 |
| SQS + SNS | ~$5 |
| CloudWatch + Alerts | ~$10 |
| **Total** | **~$973/month** |
| **Cost per review** | **~$0.019** |
| **Manual cost per review** | **~$4.00 (agent salary)** |
| **Savings** | **99.5% cost reduction** |

## Running Tests

```bash
pip install pytest pytest-asyncio httpx opencv-python-headless
pytest tests/ -v
```

## License
MIT
