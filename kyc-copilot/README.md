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
User Upload
    │
    ▼
┌─────────────────────────────────────────────────────┐
│                   FastAPI Backend                    │
│  ┌──────────────┐    ┌──────────────────────────┐   │
│  │ Image Quality│    │   Claude Vision AI        │   │
│  │   Checker    │───▶│  Document Analyzer        │   │
│  │  (OpenCV)    │    │  - Field extraction       │   │
│  └──────────────┘    │  - Profile cross-check    │   │
│                      │  - Completeness check     │   │
│                      └──────────────────────────-┘   │
│                               │                      │
│                      ┌────────▼────────┐             │
│                      │ ValidationResult│             │
│                      │ + User Checklist│             │
│                      └─────────────────┘             │
└─────────────────────────────────────────────────────┘
    │
    ▼
React Frontend (User sees issues BEFORE submitting)
```

## Quick Start

```bash
# 1. Clone
git clone https://github.com/your-org/kyc-copilot
cd kyc-copilot

# 2. Configure
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env

# 3. Run with Docker
docker-compose up

# OR run locally
pip install -r requirements.txt
uvicorn src.api.main:app --reload

# 4. Test it
curl -X POST "http://localhost:8000/validate" \
  -F "document_type=photo_id" \
  -F "file=@your_id.jpg" \
  -F "full_name=Sana Khan" \
  -F "date_of_birth=1990-05-15" \
  -F "address=123 Main St" \
  -F "city=Toronto" \
  -F "province=ON" \
  -F "postal_code=M5V1A1"
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/validate` | POST | Validate document + user profile |
| `/validation/{id}` | GET | Retrieve past validation |
| `/analytics/summary` | GET | Aggregate stats |
| `/document-types` | GET | List supported doc types |
| `/health` | GET | Health check |

## Running Tests

```bash
pip install pytest pytest-asyncio httpx opencv-python-headless
pytest tests/ -v
```

## Infrastructure (AWS)

```
API Gateway → Lambda (or ECS Fargate) → KYC Copilot API
                                       → S3 (document temp storage)
                                       → ElastiCache Redis (results cache)
                                       → CloudWatch (logging/monitoring)
```

## Cost Estimate (AWS, 10K validations/month)

| Service | Monthly Cost |
|---------|-------------|
| ECS Fargate (2 vCPU, 4GB) | ~$29 |
| Claude API (claude-opus-4-5, ~1K tokens/call) | ~$150 |
| S3 (temp storage, auto-delete 24h) | ~$2 |
| ElastiCache Redis (cache.t3.micro) | ~$15 |
| API Gateway | ~$3.50 |
| CloudWatch | ~$5 |
| **Total** | **~$205/month** |

## License
MIT
