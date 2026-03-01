# KYC AI Solutions — Full Technical Design
**Prepared for: Wealthsimple PM Role Application**  
**Author: Sana Khan**  
**Date: March 2026**

---

## Background: The Real Problem

Between December 2024 and May 2025, I experienced **4 KYC rejections** with a Wealthsimple competitor (Questrade), each requiring 2-4 business days of waiting just to receive a rejection notice:

| Date | Document | Rejection Reason |
|------|----------|-----------------|
| Dec 19, 2024 | Photo ID | DOB doesn't match profile |
| Jan 1, 2025 | Photo ID | Too blurry/unclear |
| Apr 29, 2025 | W-8BEN | Section 3 address mismatch + Section 9 country missing |
| May 25, 2025 | Bank Statement | Account number truncated |

**Total wasted time:** ~20 business days in waiting + multiple upload attempts.  
**Root cause:** No pre-validation. No real-time feedback. No cross-validation with the user's profile.  
**Solution:** Two complementary AI systems that fix this from both sides.

---

## Solution Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         WEALTHSIMPLE KYC PLATFORM                           │
│                                                                             │
│  USER SIDE                              COMPLIANCE SIDE                     │
│  ┌──────────────────────────┐          ┌──────────────────────────────┐    │
│  │   💡 KYC COPILOT         │          │  🔍 KYC REVIEW AGENT          │    │
│  │   (Idea 1)               │─────────▶│  (Idea 3)                    │    │
│  │                          │  Submit  │                              │    │
│  │  Pre-validates BEFORE     │  only   │  Auto-reviews AFTER           │    │
│  │  submission              │  valid  │  submission                  │    │
│  │                          │  docs   │                              │    │
│  └──────────────────────────┘          └──────────────────────────────┘    │
│                                                                             │
│  SHARED INFRASTRUCTURE                                                      │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Claude API (Anthropic) │ AWS S3 │ PostgreSQL │ Redis │ SQS/SNS      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Idea 1: KYC Copilot (User-Facing Validator)

### What It Does
Intercepts the document upload flow and validates documents in real-time before they are submitted to compliance review. The user gets a checklist of issues and specific suggestions — turning a multi-week back-and-forth into a 30-second fix.

### Technical Architecture

```
User (Web/Mobile)
      │ Upload document
      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    KYC Copilot Service                           │
│                                                                 │
│  ┌───────────────────┐     ┌──────────────────────────────┐    │
│  │ Image Quality     │     │    Claude Vision Analyzer     │    │
│  │ Checker (OpenCV)  │     │                              │    │
│  │                   │     │  Document Type Handlers:     │    │
│  │ • Blur detection  │────▶│  • Photo ID Analyzer         │    │
│  │   (Laplacian      │     │  • W-8BEN Analyzer           │    │
│  │   variance)       │     │  • Financial Doc Analyzer    │    │
│  │ • Resolution      │     │  • Proof of Address          │    │
│  │   check (≥800x600)│     │                              │    │
│  │ • File size check │     │  For each doc type:          │    │
│  │   (>50KB)         │     │  1. OCR field extraction     │    │
│  └───────────────────┘     │  2. Profile cross-validation │    │
│                            │  3. Completeness check       │    │
│                            │  4. Regulatory compliance    │    │
│                            └──────────────┬───────────────┘    │
│                                           │                    │
│                            ┌──────────────▼───────────────┐    │
│                            │     ValidationResult          │    │
│                            │                               │    │
│                            │  • passed: bool              │    │
│                            │  • issues: [ValidationIssue] │    │
│                            │  • confidence_score: float   │    │
│                            │  • extracted_fields: dict    │    │
│                            └──────────────┬───────────────┘    │
└───────────────────────────────────────────┼─────────────────────┘
                                            │
                            ┌──────────────▼───────────────┐
                            │  User Checklist Report        │
                            │                               │
                            │  ✅ OR ❌ + specific fixes    │
                            │  with suggestions per issue   │
                            └───────────────────────────────┘
```

### Validation Pipeline (per document type)

#### Photo ID
```
Step 1: OpenCV blur check (Laplacian variance > 100)
Step 2: Resolution check (≥ 800×600 pixels)
Step 3: Claude Vision → extract {name, DOB, address, expiry}
Step 4: Cross-validate against user profile:
        - Name match (fuzzy, handles "Khan, Sana" vs "Sana Khan")
        - DOB exact match
        - Address match (normalized)
Step 5: Expiry date check (must be valid)
Step 6: Return ValidationResult with specific flags
```

#### W-8BEN
```
Step 1: Claude Vision → extract all Part I fields
Step 2: Required field checklist:
        - Section 1: Name ✓
        - Section 2: Country of citizenship ✓
        - Section 3: Permanent residence (NOT P.O. box) ✓
        - Section 3: Address matches profile address ✓
        - Section 6a/6b: FTIN or checkbox ✓
        - Section 9 (Part II): Country of residence ✓
        - Part III: Signature + date ✓
Step 3: Flag all missing/mismatched fields
Step 4: Return specific field-level errors with suggestions
```

#### Bank Statement
```
Step 1: Claude Vision → extract {account_holder, account_number, bank, date, address}
Step 2: Truncation check — is account number complete?
Step 3: Date recency check — within 90 days?
Step 4: Name and address match vs profile
Step 5: Return flags with specific fix suggestions
```

### Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| API Framework | FastAPI (Python) | Async, fast, great DX |
| AI/Vision | Claude claude-opus-4-5 Vision API | Best-in-class document understanding |
| Image Processing | OpenCV (headless) | Fast local blur/resolution checks |
| Cache | Redis (ElastiCache) | Cache repeat validations by file hash |
| Storage | AWS S3 (24h auto-delete) | Temp storage, encrypted at rest |
| Container | Docker + ECS Fargate | Serverless scaling |
| CDN | CloudFront | Fast uploads from anywhere |
| Monitoring | CloudWatch + Datadog | Real-time error alerting |

### API Endpoints

```
POST /validate
  - multipart/form-data: file + document_type + user profile fields
  - Returns: ValidationResult JSON + user_report string

GET  /validation/{id}
  - Returns: stored validation result

GET  /analytics/summary
  - Returns: most common pre-submission errors (product insights)

GET  /document-types
  - Returns: requirements per document type (for UI help text)
```

### Data Flow Diagram

```
Browser/Mobile App
       │
       │ HTTPS POST multipart
       ▼
AWS API Gateway
       │
       ▼
ECS Fargate Task (KYC Copilot API)
       │
       ├──▶ S3: Upload temp file (encrypted, TTL=24h)
       │
       ├──▶ Redis: Check cache by SHA256(file) — return cached result if found
       │
       ├──▶ OpenCV: Local blur/resolution check (< 10ms)
       │
       └──▶ Anthropic API: Claude Vision analysis (2-8 seconds)
                  │
                  ▼
           ValidationResult
                  │
                  ├──▶ Redis: Cache result (TTL=1h)
                  │
                  └──▶ Response to client
```

---

## Idea 3: KYC Review Agent (Compliance Copilot)

### What It Does
An internal AI tool for compliance teams. Every submitted document is automatically pre-reviewed by Claude before a human agent sees it. The agent receives a pre-filled packet: AI decision, confidence score, specific flag evidence, regulatory citations, and a draft rejection email. What took 12 minutes manually now takes 8 seconds.

### Technical Architecture

```
Document Submission (from user or intake)
           │
           ▼
    AWS SQS Queue (decoupled intake)
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                     KYC Review Agent Service                      │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   Claude Vision Review                    │   │
│  │                                                          │   │
│  │  Multi-criteria scoring:                                 │   │
│  │  1. Image quality assessment                             │   │
│  │  2. Data accuracy vs profile (name, DOB, address)        │   │
│  │  3. Form completeness (per doc type)                     │   │
│  │  4. Document validity (expiry, institution, date)        │   │
│  │  5. Fraud indicator detection                            │   │
│  │                                                          │   │
│  │  Output: {decision, confidence, flags[], extracted_data} │   │
│  └─────────────────────────┬────────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼────────────────────────────────┐  │
│  │              Business Rule Engine                          │  │
│  │                                                           │  │
│  │  IF fraud_indicator AND confidence > 0.7:                 │  │
│  │      → FRAUD_FLAG (SNS alert to compliance + legal)       │  │
│  │  ELIF overall_confidence > 0.95 AND no_flags:             │  │
│  │      → AUTO_APPROVE                                       │  │
│  │  ELIF overall_confidence 0.70–0.95:                       │  │
│  │      → RECOMMEND_APPROVE or RECOMMEND_REJECT              │  │
│  │  ELIF overall_confidence < 0.50:                          │  │
│  │      → ESCALATE (senior agent)                            │  │
│  └─────────────────────────┬────────────────────────────────-┘  │
│                             │                                    │
│  ┌──────────────────────────▼────────────────────────────────┐  │
│  │              ReviewPacket Assembly                         │  │
│  │                                                           │  │
│  │  • AI decision + confidence                               │  │
│  │  • Per-flag: description, evidence, regulatory citation   │  │
│  │  • Draft rejection email (agent reviews before sending)   │  │
│  │  • Draft approval note                                    │  │
│  │  • Extracted fields                                       │  │
│  └─────────────────────────┬────────────────────────────────-┘  │
└───────────────────────────-─┼────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                   ▼
    PostgreSQL            ElastiCache         Compliance
    (audit trail)         WebSocket           Dashboard
                          (real-time          (React)
                           updates)
```

### Decision Framework Detail

```
                     ┌──────────────────────────────┐
                     │     AI Review Result          │
                     └──────────────┬───────────────┘
                                    │
                    ┌───────────────▼──────────────────┐
                    │  Any fraud_indicator flag         │
                    │  with confidence > 70%?           │
                    └──┬────────────────────────────────┘
                       │ YES                   │ NO
                       ▼                       ▼
               FRAUD_FLAG              ┌───────────────────┐
            (alert legal)              │ Confidence score? │
                                       └──┬──────────────┬─┘
                                          │              │
                              < 50%       │              │ 50-70%
                                ▼         │              ▼
                           ESCALATE       │       RECOMMEND_REJECT
                         (senior review)  │       (if error flags)
                                          │
                                        70-95%    > 95%
                                          │         │
                                          ▼         ▼
                                RECOMMEND_APPROVE  AUTO_APPROVE
                                (agent confirms)   (no human needed)
```

### Compliance Agent Dashboard

```
┌─────────────────────────────────────────────────────────────────────┐
│  KYC Review Queue                               [Filter ▾] [Export] │
├──────┬─────────────┬──────────────┬────────┬────────────┬───────────┤
│  ID  │ Applicant   │ Document     │ AI Dec │ Confidence │ Flags     │
├──────┼─────────────┼──────────────┼────────┼────────────┼───────────┤
│ 001  │ John Smith  │ Photo ID     │ 🟠 REJ │ 88%        │ 2 errors  │
│ 002  │ Jane Doe    │ W-8BEN       │ 🟢 APP │ 97%        │ 0         │
│ 003  │ Bob Lee     │ Bank Stmt    │ 🔴 ESC │ 42%        │ 3 errors  │
│ 004  │ Sara K      │ Photo ID     │ 🔴 FRD │ 91%        │ 1 fraud   │
├──────┴─────────────┴──────────────┴────────┴────────────┴───────────┤
│                                                                     │
│  [Click row to expand: flags detail + draft email + approve/reject] │
└─────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| AI/Vision | Claude claude-opus-4-5 Vision API | Best document analysis + long context |
| Queue | AWS SQS | Decoupled async review at scale |
| Database | PostgreSQL (RDS) | Audit trail, regulatory compliance |
| Cache/Realtime | ElastiCache Redis + WebSocket | Live dashboard updates |
| Alerts | AWS SNS | Fraud alerts to legal/compliance |
| Dashboard | React + shadcn/ui | Internal compliance agent UI |
| Storage | AWS S3 (90-day retention) | Document archive per regulation |
| Monitoring | CloudWatch + PagerDuty | SLA alerting |

---

## Infrastructure — AWS Architecture

```
                          ┌──────────────────────────────────────────┐
                          │              AWS Account                  │
                          │                                          │
           Users ────────▶│  CloudFront + WAF                       │
                          │       │                                  │
                          │  API Gateway                             │
                          │    ├── /validate ──▶ ECS Fargate         │
                          │    │                (KYC Copilot)        │
                          │    └── /review ───▶ SQS ──▶ ECS Fargate │
                          │                            (Review Agent)│
                          │                                          │
                          │  Shared Services:                        │
                          │    ├── S3 (document storage)             │
                          │    ├── ElastiCache Redis                 │
                          │    ├── RDS PostgreSQL                    │
                          │    ├── SNS (fraud alerts)                │
                          │    ├── CloudWatch (monitoring)           │
                          │    └── Secrets Manager (API keys)        │
                          │                                          │
                          │  Security:                               │
                          │    ├── VPC (private subnets)             │
                          │    ├── KMS encryption at rest            │
                          │    ├── TLS 1.3 in transit               │
                          │    └── IAM roles (least privilege)       │
                          └──────────────────────────────────────────┘
```

## Terraform Infrastructure (Key Resources)

```hcl
# ECS Cluster for both services
resource "aws_ecs_cluster" "kyc_ai" {
  name = "kyc-ai-cluster"
}

# KYC Copilot Task Definition
resource "aws_ecs_task_definition" "kyc_copilot" {
  family                   = "kyc-copilot"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"  # 1 vCPU
  memory                   = "2048"  # 2GB RAM
  container_definitions    = jsonencode([{
    name  = "kyc-copilot"
    image = "${aws_ecr_repository.kyc_copilot.repository_url}:latest"
    portMappings = [{ containerPort = 8000 }]
    environment = [
      { name = "REDIS_URL", value = aws_elasticache_cluster.cache.cache_nodes[0].address }
    ]
    secrets = [
      { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.anthropic.arn }
    ]
  }])
}

# S3 with lifecycle policy (auto-delete temp documents after 24h)
resource "aws_s3_bucket_lifecycle_configuration" "kyc_docs" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    id     = "delete-temp-documents"
    status = "Enabled"
    filter { prefix = "temp/" }
    expiration { days = 1 }
  }
  rule {
    id     = "archive-reviewed-documents"
    status = "Enabled"
    filter { prefix = "reviewed/" }
    expiration { days = 90 }  # FINTRAC requires 5-year retention in production
  }
}
```

---

## Cost Analysis

### Idea 1: KYC Copilot — Monthly AWS Costs

#### Tier 1: Startup (10K validations/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| ECS Fargate | 1 vCPU, 2GB × 1 task | $14.58 |
| Claude API | claude-opus-4-5, ~1.5K tokens/call × 10K | $150.00 |
| S3 (temp storage, 24h TTL) | ~5GB average | $0.12 |
| ElastiCache Redis | cache.t3.micro | $14.62 |
| API Gateway | 10K calls | $0.04 |
| CloudWatch | Basic logs | $2.50 |
| Data Transfer | ~50GB out | $4.50 |
| **Total** | | **~$186/month** |
| **Cost per validation** | | **$0.019** |

#### Tier 2: Scale (100K validations/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| ECS Fargate | 2 vCPU, 4GB × 3 tasks (auto-scale) | $87.48 |
| Claude API | claude-opus-4-5, ~1.5K tokens/call × 100K | $1,500.00 |
| S3 | ~50GB average | $1.15 |
| ElastiCache Redis | cache.t3.small (cluster mode) | $29.20 |
| API Gateway | 100K calls | $0.35 |
| CloudWatch + Datadog | Enhanced monitoring | $50.00 |
| Data Transfer | ~500GB out | $45.00 |
| **Total** | | **~$1,713/month** |
| **Cost per validation** | | **$0.017** |

**Cost optimization:** Use claude-haiku-4-5 for image quality pre-screening ($0.25/M tokens vs $15/M), only escalate to claude-opus-4-5 if image passes basic checks. Estimated **40% cost reduction** at scale.

---

### Idea 3: KYC Review Agent — Monthly AWS Costs

#### Tier 1: Small Team (5K reviews/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| ECS Fargate | 2 vCPU, 4GB × 1 task | $29.16 |
| Claude API | claude-opus-4-5, ~2K tokens/review × 5K | $375.00 |
| RDS PostgreSQL | db.t3.micro (audit trail) | $14.44 |
| ElastiCache Redis | cache.t3.micro | $14.62 |
| SQS | 5K messages | $0.01 |
| SNS | 5K notifications | $0.03 |
| S3 (90-day doc archive) | ~25GB | $0.58 |
| CloudWatch | Basic | $5.00 |
| **Total** | | **~$439/month** |
| **Cost per review** | | **$0.088** |
| **vs Manual** (~$4/review, 12 min @ $20/hr) | | **97.8% cheaper** |

#### Tier 2: Enterprise (50K reviews/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| ECS Fargate | 4 vCPU, 8GB × 5 tasks | $365.40 |
| Claude API | claude-opus-4-5, ~2K tokens × 50K | $3,750.00 |
| RDS PostgreSQL | db.t3.medium (Multi-AZ) | $96.00 |
| ElastiCache Redis | cache.t3.medium cluster | $87.60 |
| SQS + DLQ | 50K messages | $0.03 |
| SNS | Fraud alerts | $0.10 |
| S3 | ~250GB | $5.75 |
| CloudWatch + alerting | Production monitoring | $50.00 |
| **Total** | | **~$4,355/month** |
| **Cost per review** | | **$0.087** |
| **Manual cost** (50K × $4) | | **$200,000/month** |
| **Annual savings** | | **$2.34M/year** |

---

## Human-in-the-Loop Design

Both systems are designed with clear human oversight boundaries — critical for regulated financial services:

### Idea 1 (User-facing)
- **AI decides:** Whether issues exist and what they are
- **Human decides:** Whether to resubmit or proceed
- **Hard rule:** Never blocks submission — user always has final say
- **Escalation:** Low-confidence detections show "advisory" warnings, not hard blocks

### Idea 3 (Compliance-facing)
- **AI decides:** Pre-assessment, confidence score, flag evidence
- **Human decides:** Final approval/rejection (except AUTO_APPROVE < 5% of cases)
- **Hard rules:**
  - Fraud flags always require human + legal review
  - Auto-approve only at >95% confidence with zero flags
  - All AI decisions are logged with model version, confidence, and evidence
  - Human can override any AI decision
  - Appeals always go to human senior agent

### What Could Break at Scale

| Risk | Mitigation |
|------|-----------|
| Model drift (new ID formats) | Monthly evals against rejection ground truth |
| Adversarial document fraud | Separate fraud detection layer + human escalation |
| Regulatory changes (FINTRAC/OSC) | Compliance team reviews rules quarterly, prompts updated |
| API latency spikes | Async SQS queue buffers load; Redis cache reduces repeat calls |
| False positives (rejecting valid docs) | Confidence thresholds tuned; human override always available |
| Data privacy (PII in documents) | No PII stored beyond 24h; all data encrypted KMS; audit logs |

---

## Regulatory & Compliance Considerations

- **FINTRAC PCMLTFR:** KYC documents must be verified for Canadian AML compliance
- **OSC regulations:** Margin account verification requirements
- **PIPEDA/Bill C-27:** Canadian privacy law — documents stored encrypted, deleted per policy
- **FATCA/IRS W-8BEN:** Cross-border tax compliance for non-US persons
- **Audit trail:** All AI decisions logged immutably with model version, timestamp, confidence
- **Right to human review:** Regulatory obligation ensures all auto-decisions are reviewable

---

## Implementation Timeline

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| Phase 1: MVP | Weeks 1–6 | Working demo of both systems with real test cases |
| Phase 2: Production | Weeks 7–14 | AWS deployment, auth integration, compliance review |
| Phase 3: Scale | Weeks 15–22 | Performance optimization, feedback loops, mobile |
| Phase 4: Enterprise | Weeks 23–30 | Multi-jurisdiction, full regulatory sign-off |

**MVP focus for application demo:** Weeks 1–6 deliverables only — this is sufficient to demonstrate the full concept with real validation against the actual rejection emails.

---

*Prepared by Sana Khan | khan17sana@gmail.com | +1 437-833-9757*
