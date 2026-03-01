# 💰 Cost Analysis — KYC Copilot + KYC Compliance Agent

---

## Assumptions

| Variable | Value |
|----------|-------|
| Monthly document submissions | 10,000 |
| Monthly compliance reviews | 10,000 |
| Avg document size | 2 MB (image) or 200KB (PDF) |
| AWS region | ca-central-1 (Canada) |
| GPT-4o pricing | $0.005/1K input tokens, $0.015/1K output tokens |
| Claude 3.5 Sonnet pricing | $0.003/1K input tokens, $0.015/1K output tokens |
| AWS Textract | $0.015 per page (Analyze Document API) |
| Compliance officer salary | $75,000/year = $6,250/month |
| Hours saved per review | 10 min → 2 min = 8 min/review |

---

## Idea 1: KYC Copilot (Pre-Submission Validator)

### AI API Costs (10,000 validations/month)

| Service | Usage | Unit Cost | Monthly Cost |
|---------|-------|-----------|--------------|
| **GPT-4o Vision** (quality check) | 10,000 calls × ~800 tokens | $0.005/1K input | $40 |
| **GPT-4o** (structuring OCR output) | 10,000 calls × ~1,200 tokens | $0.005/1K input | $60 |
| **GPT-4o** (output generation) | 10,000 calls × ~400 tokens output | $0.015/1K output | $60 |
| **AWS Textract** (OCR) | 10,000 docs × 1 page avg | $0.015/page | $150 |
| **Subtotal AI/ML** | | | **$310** |

### Infrastructure (AWS, ca-central-1)

| Resource | Spec | Monthly Cost |
|----------|------|--------------|
| **EKS Cluster** | 3× t3.xlarge nodes | $290 |
| **RDS PostgreSQL** | db.t3.medium, Multi-AZ | $180 |
| **ElastiCache Redis** | cache.t3.medium | $80 |
| **S3** | ~500 GB (temp docs, 24h TTL) | $12 |
| **NAT Gateway** | 2× (HA setup) | $65 |
| **Load Balancer** | Application LB | $25 |
| **Data Transfer** | ~100 GB outbound | $9 |
| **Subtotal Infra** | | **$661** |

### Other

| Item | Monthly Cost |
|------|--------------|
| Datadog (monitoring) | $100 |
| Sentry (error tracking) | $26 |
| GitHub Actions (CI/CD) | $0 (public) |
| **Subtotal Other** | **$126** |

### **Total: ~$1,097/month for 10,000 validations**
### **Per-validation cost: ~$0.11**

### Scaling

| Volume | Monthly Cost | Cost/Validation |
|--------|-------------|-----------------|
| 1,000/month | ~$780 | ~$0.78 |
| 10,000/month | ~$1,097 | ~$0.11 |
| 100,000/month | ~$4,200 | ~$0.042 |
| 1,000,000/month | ~$28,000 | ~$0.028 |

### ROI Calculation

If 60% of 10,000 applicants previously needed to resubmit (6,000 resubmissions):
- **Compliance review cost per resubmission:** ~$6 (12 min × $30/hr)
- **Total cost of resubmissions avoided:** 6,000 × $6 = **$36,000/month**
- **System cost:** $1,097/month
- **Net monthly ROI: $34,903**
- **ROI ratio: 32x**

---

## Idea 3: KYC Compliance Agent (Internal Review Copilot)

### AI API Costs (10,000 reviews/month)

| Service | Usage | Unit Cost | Monthly Cost |
|---------|-------|-----------|--------------|
| **Claude 3.5 Sonnet** (document analysis) | 10,000 × ~2,000 input tokens | $0.003/1K | $60 |
| **Claude 3.5 Sonnet** (policy checking) | 10,000 × ~1,500 input tokens | $0.003/1K | $45 |
| **Claude 3.5 Sonnet** (rejection drafting) | 5,000 × ~1,000 output tokens | $0.015/1K | $75 |
| **AWS Textract** (OCR) | 10,000 docs × 1 page avg | $0.015/page | $150 |
| **Subtotal AI/ML** | | | **$330** |

### Infrastructure

| Resource | Spec | Monthly Cost |
|----------|------|--------------|
| **EKS Cluster** | 2× t3.large nodes | $145 |
| **RDS PostgreSQL** | db.t3.medium, Multi-AZ | $180 |
| **ElastiCache Redis** | cache.t3.small | $40 |
| **SQS** (document queue) | ~10K messages | $0.50 |
| **SES** (email sending) | ~5,000 emails | $0.50 |
| **S3** (audit log storage) | ~50 GB | $1.15 |
| **NAT Gateway** | 1× | $33 |
| **ALB** | 1× | $25 |
| **Subtotal Infra** | | **$425** |

### Other

| Item | Monthly Cost |
|------|--------------|
| Datadog | $100 |
| Okta SSO (auth) | $150 (est. 10 users) |
| Sentry | $26 |
| **Subtotal Other** | **$276** |

### **Total: ~$1,031/month for 10,000 AI-assisted reviews**
### **Per-review cost: ~$0.10**

### ROI Calculation

**Without AI:**
- 1 compliance officer processes 1,600 cases/month (80/day)
- Cost: $6,250/month per officer
- For 10,000 cases: 6.25 officers needed = $39,062/month

**With AI (human-in-the-loop):**
- AI handles initial analysis + drafting (10 min → 2 min per case)
- 1 officer now handles 4,000 cases/month
- For 10,000 cases: 2.5 officers needed = $15,625/month
- Plus AI system cost: $1,031/month
- **Total: $16,656/month**

**Savings: $22,406/month ($268,872/year)**
**ROI ratio: 2.3x (accounting for human reviewers retained)**

Additionally:
- Auto-approve rate (~30% of clean submissions): reduces human review to 7,000 cases
- This brings officer need down to 1.75 officers = $10,937 + $1,031 = $11,968/month
- **Enhanced savings: $27,094/month ($325,128/year)**

---

## Combined System (Both Deployed)

| System | Monthly Cost |
|--------|-------------|
| KYC Copilot | $1,097 |
| KYC Compliance Agent | $1,031 |
| Shared infra savings | -$300 |
| **Total** | **$1,828/month** |

**Combined monthly savings: $57,000+**
**Combined annual ROI: >$650,000**

---

## Cost Optimization Opportunities

1. **Switch from GPT-4o to GPT-4o-mini** for structuring tasks (5x cheaper): saves ~$70/month
2. **Batch Textract calls** using async API (50% discount): saves ~$75/month
3. **Self-host small vision models** (e.g., Idefics on GPU) for blur/quality checks: eliminates ~$100/month
4. **Spot instances** for worker nodes: saves ~30% on EKS compute
5. **Reserved instances** (1-year) for RDS and ElastiCache: saves ~40%

**Optimized combined monthly cost: ~$1,200/month** (vs $1,828 standard)
