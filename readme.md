# KYC AI Solutions — Full Technical Design
**Prepared for: Wealthsimple PM Role Application**  
**Author: Sana Khan**  
**Date: March 2026**

---

## Background: The Real Problem

Between December 2024 and May 2025, I experienced **4 KYC rejections** with a Wealthsimple competitor (Questrade), each requiring 2-4 business days of waiting just to receive a rejection notice:

| Date | Document | Rejection Reason |
|------|----------|-----------------|
| Dec 19, 2024 | W-8BEN | DOB doesn't match profile |
| Jan 1, 2025 | W-8BEN | Too blurry/unclear |
| Apr 29, 2025 | W-8BEN | Section 3 address mismatch + Section 9 country missing |
| May 25, 2025 | W-8BEN | Account number truncated |

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
│  │   💡 KYC COPILOT         │          │  🔍 KYC REVIEW AGENT        │    │
│  │                          │─────────▶│                              │    │
│  │  Pre-validates BEFORE    │  Submit  │  Auto-reviews AFTER          │    │
│  │  submission              │  only    │  submission                  │    │
│  │                          │  valid   │                              │    │
│  └──────────────────────────┘  docs    └──────────────────────────────┘    │
│                                                                             │
│  SHARED INFRASTRUCTURE                                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Claude API (Anthropic) │ GCS │ Cloud SQL │ Memorystore │ Pub/Sub    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## KYC Copilot (User-Facing Validator)

### What It Does
Intercepts the document upload flow and validates documents in real-time before they are submitted to compliance review. The user gets a checklist of issues and specific suggestions — turning a multi-week back-and-forth into a 30-second fix.

### Technical Architecture

```
User (Web/Mobile)
      │ Upload document
      ▼
Cloud Load Balancer + Cloud Armor (WAF)
      │
      ▼
Cloud Run (KYC Copilot API — fully managed, auto-scales to zero)
      │
      ├──▶ Image Quality Checker (OpenCV)
      │      • Blur detection (Laplacian variance)
      │      • Resolution check (≥800×600)
      │      • File size check (>50KB)
      │
      └──▶ Claude Vision Analyzer
             Document Type Handlers:
             • W-8BEN Analyzer
             • W-8BEN Tax Form Analyzer
             • Financial Doc Analyzer
             • Proof of Address

             For each doc type:
             1. OCR field extraction
             2. Profile cross-validation
             3. Completeness check
             4. Regulatory compliance
                    │
                    ▼
             ValidationResult
             • passed: bool
             • issues: [ValidationIssue]
             • confidence_score: float
             • extracted_fields: dict
                    │
                    ▼
             User Checklist Report
             ✅ OR ❌ + specific fixes
             with suggestions per issue
```

### Validation Pipeline (per document type)

#### W-8BEN
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

#### W-8BEN Tax Form
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
| API Framework | FastAPI (Python) on Cloud Run | Serverless, auto-scaling, no cluster management |
| AI/Vision | Claude claude-opus-4-5 Vision API | Best-in-class document understanding |
| Image Processing | OpenCV (headless) | Fast local blur/resolution checks |
| Cache | Memorystore for Redis | Managed Redis — cache repeat validations by file hash |
| Storage | GCS (24h auto-delete lifecycle rule) | Temp storage, encrypted at rest |
| Container | Cloud Run (fully managed) | Serverless scaling; no cluster overhead |
| CDN | Cloud CDN | Fast uploads from anywhere |
| Monitoring | Cloud Monitoring + Cloud Logging | Real-time error alerting |

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
Cloud Load Balancer + Cloud Armor (WAF)
       │
       ▼
Cloud Run (KYC Copilot API)
       │
       ├──▶ GCS: Upload temp file (encrypted, lifecycle TTL=24h, prefix: temp/)
       │
       ├──▶ Memorystore Redis: Check cache by SHA256(file) — return cached result if found
       │
       ├──▶ OpenCV: Local blur/resolution check (< 10ms)
       │
       └──▶ Anthropic API: Claude Vision analysis (2-8 seconds)
                  │
                  ▼
           ValidationResult
                  │
                  ├──▶ Memorystore Redis: Cache result (TTL=1h)
                  │
                  └──▶ Response to client
```

---

## KYC Review Agent (Compliance Copilot)

### What It Does
An internal AI tool for compliance teams. Every submitted document is automatically pre-reviewed by Claude before a human agent sees it. The agent receives a pre-filled packet: AI decision, confidence score, specific flag evidence, regulatory citations, and a draft rejection email. What took 12 minutes manually now takes 8 seconds.

### Technical Architecture

```
Document Submission (from user or intake)
           │
           ▼
    Cloud Pub/Sub Topic (decoupled intake)
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                     KYC Review Agent (Cloud Run)                 │
│                                                                  │
│  ┌────────────────────────────────────────────────────────── ┐   │
│  │                   Claude Vision Review                    │   │
│  │                                                           │   │
│  │  Multi-criteria scoring:                                  │   │
│  │  1. Image quality assessment                              │   │
│  │  2. Data accuracy vs profile (name, DOB, address)         │   │
│  │  3. Form completeness (per doc type)                      │   │
│  │  4. Document validity (expiry, institution, date)         │   │
│  │  5. Fraud indicator detection                             │   │
│  │                                                           │   │
│  │  Output: {decision, confidence, flags[], extracted_data}  │   │
│  └─────────────────────────┬─────────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼────────────────────────────────┐  │
│  │              Business Rule Engine                          │  │
│  │                                                           │  │
│  │  IF fraud_indicator AND confidence > 0.7:                 │  │
│  │      → FRAUD_FLAG (Pub/Sub alert to compliance + legal)   │  │
│  │  ELIF overall_confidence > 0.95 AND no_flags:             │  │
│  │      → AUTO_APPROVE                                       │  │
│  │  ELIF overall_confidence 0.70–0.95:                       │  │
│  │      → RECOMMEND_APPROVE or RECOMMEND_REJECT              │  │
│  │  ELIF overall_confidence < 0.50:                          │  │
│  │      → ESCALATE (senior agent)                            │  │
│  └─────────────────────────┬──────────────────────────────────┘  │
│                             │                                    │
│  ┌──────────────────────────▼────────────────────────────────┐  │
│  │              ReviewPacket Assembly                         │  │
│  │                                                           │  │
│  │  • AI decision + confidence                               │  │
│  │  • Per-flag: description, evidence, regulatory citation   │  │
│  │  • Draft rejection email (agent reviews before sending)   │  │
│  │  • Draft approval note                                    │  │
│  │  • Extracted fields                                       │  │
│  └─────────────────────────┬──────────────────────────────────┘  │
└─────────────────────────────┼────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                   ▼
    Cloud SQL             Memorystore         Compliance
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
            (Pub/Sub alert             │ Confidence score? │
             to legal)                 └──┬──────────────┬─┘
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
│ 001  │ John Smith  │ W-8BEN       │ 🟠 REJ │ 88%        │ 2 errors  │
│ 002  │ Jane Doe    │ W-8BEN       │ 🟢 APP │ 97%        │ 0         │
│ 003  │ Bob Lee     │ W-8BEN       │ 🔴 ESC │ 42%        │ 3 errors  │
│ 004  │ Sara K      │ W-8BEN       │ 🔴 FRD │ 91%        │ 1 fraud   │
├──────┴─────────────┴──────────────┴────────┴────────────┴───────────┤
│                                                                     │
│  [Click row to expand: flags detail + draft email + approve/reject] │
└─────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| AI/Vision | Claude claude-opus-4-5 Vision API | Best document analysis + long context |
| Queue | Google Cloud Pub/Sub | Decoupled async review at scale; built-in DLQ |
| Database | Cloud SQL for PostgreSQL | Managed audit trail; automated backups |
| Cache/Realtime | Memorystore Redis + WebSocket | Live dashboard updates |
| Alerts | Pub/Sub + Cloud Functions | Fraud alerts to legal/compliance |
| Dashboard | React + shadcn/ui on Cloud Run | Internal compliance agent UI |
| Storage | GCS (90-day retention policy) | Document archive per regulation |
| Monitoring | Cloud Monitoring + PagerDuty | SLA alerting |

---

## Infrastructure — GCP Architecture

```
                          ┌──────────────────────────────────────────┐
                          │           Google Cloud Project            │
                          │                                          │
           Users ────────▶│  Cloud Load Balancer + Cloud Armor (WAF)│
                          │       │                                  │
                          │  Cloud Endpoints / API Gateway           │
                          │    ├── /validate ──▶ Cloud Run           │
                          │    │               (KYC Copilot)         │
                          │    └── /review ───▶ Pub/Sub ──▶ Cloud Run│
                          │                            (Review Agent)│
                          │                                          │
                          │  Shared Services:                        │
                          │    ├── GCS (document storage)            │
                          │    ├── Memorystore Redis                 │
                          │    ├── Cloud SQL (PostgreSQL)            │
                          │    ├── Pub/Sub (fraud alerts)            │
                          │    ├── Cloud Monitoring + Logging        │
                          │    └── Secret Manager (API keys)         │
                          │                                          │
                          │  Security:                               │
                          │    ├── VPC + Private Service Connect     │
                          │    ├── CMEK encryption at rest (Cloud KMS│
                          │    ├── TLS 1.3 in transit               │
                          │    └── IAM roles (least privilege)       │
                          └──────────────────────────────────────────┘
```

## Terraform Infrastructure (Key Resources)

```hcl
# Cloud Run service for KYC Copilot
resource "google_cloud_run_v2_service" "kyc_copilot" {
  name     = "kyc-copilot"
  location = "northamerica-northeast1"  # Toronto region

  template {
    containers {
      image = "northamerica-northeast1-docker.pkg.dev/${var.project}/kyc/copilot:latest"
      resources { limits = { cpu = "1", memory = "2Gi" } }
      env { name = "REDIS_HOST" value = google_redis_instance.cache.host }
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic.secret_id
            version = "latest"
          }
        }
      }
    }
    scaling { min_instance_count = 0  max_instance_count = 10 }
  }
}

# GCS with lifecycle policy (auto-delete temp documents after 24h)
resource "google_storage_bucket" "kyc_documents" {
  name     = "${var.project}-kyc-docs"
  location = "northamerica-northeast1"

  lifecycle_rule {
    condition { age = 1  matches_prefix = ["temp/"] }
    action    { type = "Delete" }
  }
  lifecycle_rule {
    condition { age = 90  matches_prefix = ["reviewed/"] }
    action    { type = "Delete" }  # FINTRAC requires 5-year retention in production
  }

  encryption { default_kms_key_name = google_kms_crypto_key.kyc.id }
}

# Memorystore (managed Redis)
resource "google_redis_instance" "cache" {
  name           = "kyc-cache"
  tier           = "STANDARD_HA"
  memory_size_gb = 1
  region         = "northamerica-northeast1"
}
```

---

## Cost Analysis

### KYC Copilot — Monthly GCP Costs

#### Tier 1: Startup (10K validations/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| Cloud Run | 1 vCPU, 2GB, ~10K requests (pay-per-use) | $8.20 |
| Claude API | claude-opus-4-5, ~1.5K tokens/call × 10K | $150.00 |
| GCS (temp storage, 24h TTL) | ~5GB average | $0.10 |
| Memorystore Redis | BASIC tier, 1GB | $29.20 |
| Cloud Load Balancer | Minimal forwarding rules | $18.00 |
| Cloud Monitoring | Basic logs and metrics | $2.50 |
| Network Egress | ~50GB out | $5.00 |
| **Total** | | **~$213/month** |
| **Cost per validation** | | **$0.021** |

#### Tier 2: Scale (100K validations/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| Cloud Run | Auto-scales; ~100K requests | $45.00 |
| Claude API | claude-opus-4-5, ~1.5K tokens/call × 100K | $1,500.00 |
| GCS | ~50GB average | $1.00 |
| Memorystore Redis | STANDARD_HA, 2GB | $87.60 |
| Cloud Load Balancer | Production config | $18.00 |
| Cloud Monitoring + Logging | Enhanced observability | $40.00 |
| Network Egress | ~500GB out | $50.00 |
| **Total** | | **~$1,742/month** |
| **Cost per validation** | | **$0.017** |

**Cost optimization:** Use claude-haiku-4-5 for image quality pre-screening ($0.25/M tokens vs $15/M), only escalate to claude-opus-4-5 if image passes basic checks. Estimated **40% cost reduction** at scale.

---

### KYC Review Agent — Monthly GCP Costs

#### Tier 1: Small Team (5K reviews/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| Cloud Run | 2 vCPU, 4GB, ~5K requests | $14.00 |
| Claude API | claude-opus-4-5, ~2K tokens/review × 5K | $375.00 |
| Cloud SQL (PostgreSQL) | db-f1-micro (audit trail) | $9.37 |
| Memorystore Redis | BASIC, 1GB | $29.20 |
| Pub/Sub | 5K messages | $0.01 |
| Cloud Functions (alerts) | 5K invocations | $0.01 |
| GCS (90-day doc archive) | ~25GB | $0.50 |
| Cloud Monitoring | Basic | $5.00 |
| **Total** | | **~$433/month** |
| **Cost per review** | | **$0.087** |
| **vs Manual** (~$4/review, 12 min @ $20/hr) | | **97.8% cheaper** |

#### Tier 2: Enterprise (50K reviews/month)

| Service | Config | Monthly Cost |
|---------|--------|-------------|
| Cloud Run | 4 vCPU, 8GB, ~50K requests (auto-scale) | $180.00 |
| Claude API | claude-opus-4-5, ~2K tokens × 50K | $3,750.00 |
| Cloud SQL (PostgreSQL) | db-n1-standard-2 (HA, Multi-zone) | $150.00 |
| Memorystore Redis | STANDARD_HA, 4GB | $175.00 |
| Pub/Sub + DLQ | 50K messages | $0.03 |
| Cloud Functions | Fraud alert fan-out | $0.10 |
| GCS | ~250GB | $5.00 |
| Cloud Monitoring + PagerDuty | Production monitoring | $60.00 |
| **Total** | | **~$4,320/month** |
| **Cost per review** | | **$0.086** |
| **Manual cost** (50K × $4) | | **$200,000/month** |
| **Annual savings** | | **$2.35M/year** |

---

## Human-in-the-Loop Design

Both systems are designed with clear human oversight boundaries — critical for regulated financial services:

### KYC Copilot (User-facing)
- **AI decides:** Whether issues exist and what they are
- **Human decides:** Whether to resubmit or proceed
- **Hard rule:** Never blocks submission — user always has final say
- **Escalation:** Low-confidence detections show "advisory" warnings, not hard blocks

### KYC Review Agent (Compliance-facing)
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
| API latency spikes | Async Pub/Sub queue buffers load; Memorystore cache reduces repeat calls |
| False positives (rejecting valid docs) | Confidence thresholds tuned; human override always available |
| Data privacy (PII in documents) | No PII stored beyond 24h; all data encrypted with Cloud KMS; audit logs |

---

## Regulatory & Compliance Considerations

- **FINTRAC PCMLTFR:** KYC documents must be verified for Canadian AML compliance
- **OSC regulations:** Margin account verification requirements
- **PIPEDA/Bill C-27:** Canadian privacy law — documents stored encrypted, deleted per GCS lifecycle policy
- **FATCA/IRS W-8BEN:** Cross-border tax compliance for non-US persons
- **Audit trail:** All AI decisions logged immutably in Cloud SQL with model version, timestamp, confidence
- **Right to human review:** Regulatory obligation ensures all auto-decisions are reviewable

---

## Implementation Timeline

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| Phase 1: MVP | Weeks 1–6 | Working demo of both systems with real test cases |
| Phase 2: Production | Weeks 7–14 | GCP deployment, auth integration, compliance review |
| Phase 3: Scale | Weeks 15–22 | Performance optimization, feedback loops, mobile |
| Phase 4: Enterprise | Weeks 23–30 | Multi-jurisdiction, full regulatory sign-off |

**MVP focus for application demo:** Weeks 1–6 deliverables only — this is sufficient to demonstrate the full concept with real validation against the actual rejection emails.


