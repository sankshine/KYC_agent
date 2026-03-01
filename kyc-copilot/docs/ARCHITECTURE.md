# 🏛️ Architecture Documentation
## KYC Copilot + KYC Compliance Agent

---

## System Overview

Two complementary AI systems designed to eliminate KYC document rejection loops:

| System | Who it helps | What it does |
|--------|-------------|--------------|
| **KYC Copilot** (Idea 1) | Applicants | Validates documents BEFORE submission |
| **KYC Compliance Agent** (Idea 3) | Compliance teams | AI-assists review AFTER submission |

Together they attack the problem from both ends: preventing bad submissions AND making review faster.

---

## Idea 1: KYC Copilot — Technical Architecture

### Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND (Next.js 14)                             │
│                                                                             │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────────────────┐   │
│  │  Upload Zone │   │  Live Results │   │  Issue Cards + Suggestions   │   │
│  │ (react-drop- │──▶│  Panel        │   │                              │   │
│  │  zone)       │   │  (WebSocket)  │   │  ⚠️ Blur detected            │   │
│  └──────────────┘   └───────────────┘   │  ⚠️ DOB mismatch            │   │
│                                         │  ✅ Resolution OK            │   │
│                                         └──────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │ HTTPS + WebSocket
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         API LAYER (FastAPI)                                 │
│                                                                             │
│  POST /api/v1/validate/document   ←── File upload (multipart/form-data)    │
│  GET  /api/v1/validate/{id}       ←── Poll for async results               │
│  WS   /ws/validation/{id}         ←── Real-time progress streaming         │
│                                                                             │
│  Middleware: JWT Auth → Rate Limiting → CORS → GZip                       │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │
                ┌───────────▼────────────┐
                │   Task Queue (Redis)    │
                │   Celery workers        │
                └───────────┬────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────────────────┐
│                    LANGGRAPH ORCHESTRATOR                                   │
│                                                                             │
│  START                                                                      │
│    │                                                                        │
│    ▼                                                                        │
│  ┌──────────────────────────────┐                                          │
│  │  Document Quality Agent      │  OpenCV blur, resolution, brightness      │
│  │  + GPT-4o Vision             │  + vision check for obstructions         │
│  └──────────────┬───────────────┘                                          │
│                 │                                                           │
│    ┌────────────┴──────────────┐                                           │
│    │  Critical quality?        │                                           │
│    │  YES → skip to aggregate  │                                           │
│    │  NO → continue            │                                           │
│    └────────────┬──────────────┘                                           │
│                 │                                                           │
│    ┌────────────┴──────────────────────────────┐                          │
│    │          PARALLEL EXECUTION               │                          │
│    │                                           │                          │
│    ▼                                           ▼                          │
│  ┌─────────────────────┐     ┌─────────────────────────┐                 │
│  │  CrossRef Agent     │     │  Form Completeness Agent │                 │
│  │                     │     │                          │                 │
│  │  Textract OCR       │     │  Textract OCR            │                 │
│  │  ↓                  │     │  ↓                       │                 │
│  │  GPT-4o Structure   │     │  Rule-based checks       │                 │
│  │  ↓                  │     │  ↓                       │                 │
│  │  DB Profile Lookup  │     │  W-8BEN: Section 9?      │                 │
│  │  ↓                  │     │  Bank: acct truncated?   │                 │
│  │  Name, DOB, Address │     │  ID: expiry valid?       │                 │
│  │  fuzzy match        │     │                          │                 │
│  └──────────┬──────────┘     └────────────┬─────────────┘                 │
│             │                             │                                │
│             └──────────────┬──────────────┘                               │
│                            │                                               │
│                  ┌─────────▼──────────┐                                   │
│                  │  Aggregate Results  │                                   │
│                  │  Score: 0-100       │                                   │
│                  │  Issues list        │                                   │
│                  │  Suggestions        │                                   │
│                  └─────────┬──────────┘                                   │
│                            │                                               │
│                           END                                              │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
        ┌────────────────────┼──────────────────────────┐
        ▼                    ▼                           ▼
┌──────────────┐   ┌──────────────────┐      ┌───────────────────┐
│  AWS S3      │   │  PostgreSQL       │      │  AWS Textract     │
│  (temp docs, │   │  (user profiles,  │      │  (OCR engine)     │
│  24h TTL,    │   │   audit log,      │      │                   │
│  AES-256)    │   │   validation      │      │  Region:          │
│              │   │   history)        │      │  ca-central-1     │
└──────────────┘   └──────────────────┘      └───────────────────┘
```

### Data Flow (Step by Step)

1. **User uploads document** → Frontend sends `multipart/form-data` to `/api/v1/validate/document`
2. **API Gateway** validates JWT token, checks file size (<10MB), checks content type
3. **File encrypted** with AES-256 and stored in S3 with 24-hour lifecycle rule
4. **Celery task** created for async processing; WebSocket connection opened to client
5. **LangGraph graph** invoked with initial state
6. **Quality Agent** runs: OpenCV checks (blur score, resolution, brightness, boundaries) + GPT-4o vision check
7. If quality is critical failure → skip to step 10 (saves API costs on obviously bad docs)
8. **CrossRef Agent + Form Agent run in parallel**: Textract extracts text → GPT-4o structures it → cross-reference vs. database profile
9. Issues aggregated, score calculated (100 - weighted deductions)
10. **Result returned** via WebSocket (real-time) and REST API (polling fallback)
11. **Audit record** created in PostgreSQL; S3 doc auto-deleted after 24h

### Security Architecture

```
Internet → CloudFront CDN → WAF (rate limit, SQLi, XSS) → ALB → EKS
                                                                    ↓
                                                            App in private subnet
                                                                    ↓
                                                       VPC Endpoint → S3 (no internet)
                                                       VPC Endpoint → Textract
                                                       VPC Endpoint → Secrets Manager
```

- All docs encrypted at rest (S3 SSE-AES256) and in transit (TLS 1.3)
- PII never logged to application logs (structured logging with field masking)
- JWT tokens expire in 1 hour; refresh tokens in 7 days
- Secrets in AWS Secrets Manager (never in env vars or code)

---

## Idea 3: KYC Compliance Agent — Technical Architecture

### Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                  DOCUMENT INTAKE (existing KYC flow)                        │
│                                                                             │
│   User submits via web/mobile → Existing intake API → SQS Queue           │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │ SQS message
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                       KYC COMPLIANCE AGENT API (FastAPI)                   │
│                                                                             │
│   SQS Consumer (background worker) polls queue every 30s                  │
│   → Creates ReviewCase → Triggers AI review pipeline                       │
│                                                                             │
│   Endpoints:                                                               │
│   GET  /api/v1/review/queue          ← Reviewer queue dashboard            │
│   GET  /api/v1/review/cases/{id}     ← Case detail + AI findings          │
│   POST /api/v1/review/cases/{id}/decide ← Human decision recording         │
│   GET  /api/v1/review/metrics        ← Analytics dashboard                 │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                     LANGGRAPH REVIEW PIPELINE                               │
│                                                                             │
│  analyze_document → check_policies → score_confidence → route_decision      │
│                                                              │              │
│                              ┌───────────────┬──────────────┘              │
│                              ▼               ▼              ▼              │
│                         auto_approve    draft_rejection  flag_senior        │
│                              │               │              │              │
│                              └───────────────┴──────────────┘              │
│                                              │                             │
│                                       finalize_review                      │
│                                       (audit log + metrics)                │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
     ┌───────────────────────────────┼──────────────────────────┐
     ▼                               ▼                          ▼
┌─────────────┐             ┌─────────────────┐      ┌──────────────────────┐
│  Document   │             │ Policy Checker  │      │  Rejection Drafter   │
│  Analyzer   │             │                 │      │                      │
│             │             │ • KYC policy    │      │  Claude 3.5 Sonnet   │
│ Textract    │             │   v2.4 rules    │      │                      │
│ OCR         │             │ • FINTRAC reqs  │      │  Input:              │
│ ↓           │             │ • IRS W-8BEN    │      │  - Applicant name    │
│ Claude 3.5  │             │   requirements  │      │  - Doc type          │
│ (structure  │             │ • Address match │      │  - Violations list   │
│  + extract) │             │ • DOB match     │      │                      │
│             │             │ • Expiry check  │      │  Output:             │
│ Output:     │             │                 │      │  - Specific email    │
│ Structured  │             │ Output:         │      │  - Per-issue fixes   │
│ JSON        │             │ PolicyViolation │      │  - Policy refs       │
│             │             │ list with refs  │      │                      │
└─────────────┘             └─────────────────┘      └──────────────────────┘
        │                           │                          │
        └───────────────────────────┴──────────────────────────┘
                                    │
                     ┌──────────────▼────────────────┐
                     │  REVIEWER DASHBOARD (Next.js)  │
                     │                                │
                     │  ┌─────────────────────────┐   │
                     │  │ Queue View              │   │
                     │  │ • Priority sort         │   │
                     │  │ • SLA countdown         │   │
                     │  │ • Case assignment       │   │
                     │  └─────────────────────────┘   │
                     │  ┌─────────────────────────┐   │
                     │  │ Case Detail View        │   │
                     │  │ • Document preview      │   │
                     │  │ • AI findings panel     │   │
                     │  │ • Draft email editor    │   │
                     │  │ • Approve/Reject/Esc    │   │
                     │  └─────────────────────────┘   │
                     │  ┌─────────────────────────┐   │
                     │  │ Analytics View          │   │
                     │  │ • AI accuracy rate      │   │
                     │  │ • Avg review time       │   │
                     │  │ • Auto-approve rate     │   │
                     │  │ • Rejection reason dist │   │
                     │  └─────────────────────────┘   │
                     └────────────────────────────────┘
```

### Human-in-the-Loop Design

The AI **never** takes final action autonomously. The review pipeline produces a recommendation only:

```
AI Recommendation          Human Action Required
─────────────────────────────────────────────────
AUTO_APPROVE (>92% conf)  → Reviewer can 1-click confirm, or override
REJECT (violations found) → Reviewer reviews AI findings + draft email,
                            can edit email, then sends
FLAG_SENIOR (<60% conf)   → Escalated to senior reviewer for manual review
```

This ensures regulatory compliance (FINTRAC requires human oversight) while maximizing efficiency.

### Audit Trail Architecture

Every event is logged to an **append-only** audit table:

```sql
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,  -- ai_review, human_decision, email_sent
    case_id VARCHAR(50) NOT NULL,
    actor_type VARCHAR(20) NOT NULL,  -- ai, human
    actor_id VARCHAR(50),             -- reviewer_id or model_name
    decision VARCHAR(50),             -- approve, reject, escalate
    ai_recommendation VARCHAR(50),    -- what AI recommended
    agreed_with_ai BOOLEAN,           -- did human agree with AI?
    payload JSONB,                    -- full details
    created_at TIMESTAMPTZ DEFAULT NOW(),
    -- Row-level security: no UPDATE or DELETE allowed
    CONSTRAINT no_modifications CHECK (true)
);

-- Revoke UPDATE/DELETE from all roles
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
REVOKE UPDATE, DELETE ON audit_log FROM kyc_admin;
```

Audit logs are also streamed to S3 (Glacier) for 7-year retention per FINTRAC requirements.

---

## Shared Infrastructure

Both systems share:
- EKS cluster (different namespaces)
- RDS PostgreSQL (different schemas)
- Terraform state (separate workspaces)
- CI/CD pipeline (separate deployment stages)
- Datadog organization (separate dashboards)

This reduces shared infrastructure cost by ~$300/month vs. running separately.

---

## Failure Modes & Mitigations

| Failure | Impact | Mitigation |
|---------|--------|-----------|
| OpenAI API down | Quality check degrades | Fallback to OpenCV-only (no vision check) |
| AWS Textract down | OCR fails | Retry with exponential backoff; use GPT-4o Vision as fallback OCR |
| Database down | Can't cross-reference | Return quality-only result with warning |
| Redis down | No async queue | Synchronous processing fallback |
| EKS node failure | Service degraded | HPA min 2 pods; spot + on-demand node mix |
| Model drift | False positives/negatives | Weekly accuracy metrics; alert if drops >5% |
| Adversarial docs | Fraud bypass | Separate fraud detection pipeline (Phase 4) |
