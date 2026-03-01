"""
KYC Review Copilot — AI Compliance Agent
Automates the compliance reviewer side of KYC document processing.
"""

import anthropic
import base64
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional


class ReviewDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    RECOMMEND_APPROVE = "recommend_approve"
    RECOMMEND_REJECT = "recommend_reject"
    ESCALATE = "escalate"
    FRAUD_FLAG = "fraud_flag"


@dataclass
class IssueFlag:
    issue_id: str
    category: str
    description: str
    confidence: float
    evidence: str
    regulatory_ref: str
    draft_rejection_reason: str


@dataclass
class ReviewPacket:
    submission_id: str
    document_type: str
    applicant_name: str
    review_timestamp: str
    decision: ReviewDecision
    overall_confidence: float
    flags: list
    extracted_data: dict
    draft_rejection_email: str
    draft_approval_note: str
    processing_time_seconds: float
    model_version: str
    requires_human_review: bool
    human_review_reason: str


class KYCReviewAgent:
    """AI agent that pre-reviews KYC submissions for compliance agents."""

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-opus-4-5"
        self.auto_approve_threshold = 0.95
        self.escalate_threshold = 0.50

    def _encode_image(self, path: str) -> tuple:
        with open(path, "rb") as f:
            data = f.read()
        ext = path.lower().split(".")[-1]
        media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}
        return base64.standard_b64encode(data).decode("utf-8"), media_map.get(ext, "image/jpeg")

    def _build_prompt(self, document_type: str, user_profile: dict, context: dict) -> str:
        return f"""You are an expert KYC compliance reviewer. Analyze the submitted document.

DOCUMENT TYPE: {document_type}
APPLICANT PROFILE: {json.dumps(user_profile, indent=2)}
ACCOUNT TYPE: {context.get('account_type', 'margin')}
PREVIOUS REJECTIONS: {context.get('previous_rejections', [])}

Check: image quality, data accuracy vs profile, form completeness, document validity, fraud indicators.

FINTRAC/OSC requirements:
- Photo ID: government-issued, clear, not expired, name/DOB/address match profile
- W-8BEN: Part I complete (esp. Section 3 address = profile address, Section 9 country filled), Part III signed
- Bank statement: within 90 days, full account number visible (not truncated), name matches

Return ONLY valid JSON:
{{
  "decision": "auto_approve|recommend_approve|recommend_reject|escalate|fraud_flag",
  "overall_confidence": 0.0,
  "extracted_data": {{}},
  "flags": [{{
    "issue_id": "F001",
    "category": "image_quality|data_mismatch|incomplete_form|fraud_indicator",
    "description": "specific issue",
    "confidence": 0.0,
    "evidence": "what in document triggered this",
    "regulatory_ref": "regulation reference",
    "draft_rejection_reason": "text for applicant"
  }}],
  "requires_human_review": true,
  "human_review_reason": "why human needed"
}}"""

    def _gen_rejection_email(self, name: str, doc_type: str, flags: list) -> str:
        reasons = "\n".join(f"• {f.draft_rejection_reason}" for f in flags if f.confidence > 0.7)
        return f"""Subject: Action Required for Your {doc_type} Submission

Hi {name},

You recently sent us a document for your account. Unfortunately, we weren't able to accept it.

Document: {doc_type}

Reason(s):
{reasons}

Please resubmit via Account > Summary > Sign and Submit Documents > Verify your identity.

New documents reviewed within 2-4 business days. Contact us at 1.888.783.7866 if you need help.

Thanks,
Compliance Team

[AGENT NOTE: AI confidence flags attached to submission record. Review before sending.]"""

    def _determine_decision(self, ai_decision: str, flags: list, confidence: float) -> ReviewDecision:
        if any(f.category == "fraud_indicator" and f.confidence > 0.7 for f in flags):
            return ReviewDecision.FRAUD_FLAG
        decision_map = {
            "auto_approve": ReviewDecision.AUTO_APPROVE,
            "recommend_approve": ReviewDecision.RECOMMEND_APPROVE,
            "recommend_reject": ReviewDecision.RECOMMEND_REJECT,
            "escalate": ReviewDecision.ESCALATE,
            "fraud_flag": ReviewDecision.FRAUD_FLAG,
        }
        decision = decision_map.get(ai_decision, ReviewDecision.ESCALATE)
        if decision == ReviewDecision.AUTO_APPROVE and confidence < self.auto_approve_threshold:
            return ReviewDecision.RECOMMEND_APPROVE
        if confidence < self.escalate_threshold:
            return ReviewDecision.ESCALATE
        return decision

    def review_document(self, document_path: str, document_type: str, user_profile: dict, context: dict, submission_id: str) -> ReviewPacket:
        start = time.time()
        image_data, media_type = self._encode_image(document_path)
        prompt = self._build_prompt(document_type, user_profile, context)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": prompt}
            ]}]
        )

        raw = re.sub(r"```json|```", "", response.content[0].text.strip()).strip()
        ai = json.loads(raw)

        flags = [IssueFlag(**{k: f[k] for k in IssueFlag.__dataclass_fields__}) for f in ai.get("flags", [])]
        confidence = ai.get("overall_confidence", 0.5)
        decision = self._determine_decision(ai.get("decision", "escalate"), flags, confidence)
        name = user_profile.get("full_name", "Valued Client")

        return ReviewPacket(
            submission_id=submission_id,
            document_type=document_type,
            applicant_name=name,
            review_timestamp=datetime.utcnow().isoformat(),
            decision=decision,
            overall_confidence=confidence,
            flags=flags,
            extracted_data=ai.get("extracted_data", {}),
            draft_rejection_email=self._gen_rejection_email(name, document_type, flags) if flags else "",
            draft_approval_note=f"Document verified for {name}. {document_type} accepted." if decision in [ReviewDecision.AUTO_APPROVE, ReviewDecision.RECOMMEND_APPROVE] else "",
            processing_time_seconds=round(time.time() - start, 2),
            model_version=self.model,
            requires_human_review=ai.get("requires_human_review", True),
            human_review_reason=ai.get("human_review_reason", "")
        )

    def to_dashboard_entry(self, packet: ReviewPacket) -> dict:
        colors = {
            ReviewDecision.AUTO_APPROVE: "green",
            ReviewDecision.RECOMMEND_APPROVE: "light_green",
            ReviewDecision.RECOMMEND_REJECT: "orange",
            ReviewDecision.ESCALATE: "red",
            ReviewDecision.FRAUD_FLAG: "dark_red"
        }
        return {
            "submission_id": packet.submission_id,
            "applicant": packet.applicant_name,
            "document_type": packet.document_type,
            "ai_decision": packet.decision.value,
            "confidence": f"{packet.overall_confidence:.0%}",
            "status_color": colors[packet.decision],
            "flag_count": len(packet.flags),
            "requires_action": packet.requires_human_review,
            "processing_seconds": packet.processing_time_seconds,
            "timestamp": packet.review_timestamp,
        }
