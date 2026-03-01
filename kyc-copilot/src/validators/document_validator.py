"""
KYC Copilot - Document Validator
Pre-submission AI validation pipeline for KYC documents.
"""

import anthropic
import base64
import cv2
import numpy as np
from PIL import Image
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import io
import re


class DocumentType(str, Enum):
    PHOTO_ID = "photo_id"
    W8BEN = "w8ben"
    FINANCIAL_DOCUMENT = "financial_document"
    PROOF_OF_ADDRESS = "proof_of_address"


@dataclass
class ValidationIssue:
    code: str
    severity: str  # "error" | "warning"
    field: str
    description: str
    suggestion: str


@dataclass
class ValidationResult:
    document_type: DocumentType
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    extracted_fields: dict = field(default_factory=dict)
    confidence_score: float = 0.0
    raw_ai_response: str = ""


class ImageQualityChecker:
    """Check image quality before sending to AI."""

    def check_blur(self, image_path: str) -> tuple[bool, float]:
        """Returns (is_acceptable, blur_score). Higher = clearer."""
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False, 0.0
        variance = cv2.Laplacian(img, cv2.CV_64F).var()
        # Threshold: below 100 is considered blurry for documents
        return variance > 100, round(variance, 2)

    def check_resolution(self, image_path: str) -> tuple[bool, tuple]:
        """Check if image meets minimum resolution requirements."""
        img = Image.open(image_path)
        width, height = img.size
        # Minimum 800x600 for document readability
        is_acceptable = width >= 800 and height >= 600
        return is_acceptable, (width, height)

    def check_file_size(self, image_path: str) -> tuple[bool, float]:
        """Check file size (too small = likely low quality)."""
        import os
        size_kb = os.path.getsize(image_path) / 1024
        return size_kb > 50, round(size_kb, 2)


class AIDocumentAnalyzer:
    """Claude Vision-powered document field extractor and cross-validator."""

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-opus-4-5"

    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """Base64 encode image for Claude Vision."""
        with open(image_path, "rb") as f:
            data = f.read()
        ext = image_path.lower().split(".")[-1]
        media_type_map = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "pdf": "application/pdf"
        }
        return base64.standard_b64encode(data).decode("utf-8"), media_type_map.get(ext, "image/jpeg")

    def analyze_photo_id(self, image_path: str, user_profile: dict) -> dict:
        """Extract and validate Photo ID fields against user profile."""
        image_data, media_type = self._encode_image(image_path)

        prompt = f"""You are a KYC compliance document analyzer. Analyze this photo ID document.

Extract the following fields if present:
- full_name
- date_of_birth (format: YYYY-MM-DD)
- address
- id_number
- expiry_date
- issuing_country

Compare against the user profile:
{user_profile}

Return a JSON object with:
{{
  "extracted_fields": {{}},
  "mismatches": [
    {{"field": "...", "document_value": "...", "profile_value": "...", "severity": "error|warning"}}
  ],
  "is_document_readable": true/false,
  "readability_issues": [],
  "confidence": 0.0-1.0
}}

Only return valid JSON, no other text."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        import json
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)

    def analyze_w8ben(self, image_path: str, user_profile: dict) -> dict:
        """Validate W-8BEN form completeness and consistency."""
        image_data, media_type = self._encode_image(image_path)

        prompt = f"""You are a W-8BEN tax form compliance analyst. Analyze this W-8BEN form.

Check for these specific fields:
- Part I Section 1: Name of individual
- Part I Section 2: Country of citizenship  
- Part I Section 3: Permanent residence address (NOT a P.O. box)
- Part I Section 6a/6b: Foreign tax identifying number OR checkbox
- Part I Section 8: Date of birth
- Part II Section 9: Country of residence for treaty purposes
- Part III: Signature and date

Compare address in Section 3 against user's registered address: {user_profile.get('address', 'Unknown')}

Return JSON:
{{
  "extracted_fields": {{}},
  "missing_required_fields": [],
  "mismatches": [
    {{"field": "...", "issue": "...", "severity": "error|warning"}}
  ],
  "is_signed": true/false,
  "confidence": 0.0-1.0
}}

Only return valid JSON."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        import json
        text = response.content[0].text.strip()
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)

    def analyze_financial_document(self, image_path: str, user_profile: dict) -> dict:
        """Validate bank statement or financial document visibility."""
        image_data, media_type = self._encode_image(image_path)

        prompt = f"""You are a financial document compliance analyst for KYC verification. Analyze this financial document.

Check for:
1. Account holder name - visible and complete?
2. Bank account number - visible and NOT truncated (e.g., not showing only last 4 digits)?
3. Bank name/institution - visible?
4. Date - recent (within last 3 months)?
5. Address - matches profile address: {user_profile.get('address', 'Unknown')}?
6. Any information that appears cut off or illegible?

Return JSON:
{{
  "extracted_fields": {{
    "account_holder": "...",
    "account_number_visible": true/false,
    "account_number_truncated": true/false,
    "bank_name": "...",
    "document_date": "...",
    "address": "..."
  }},
  "issues": [
    {{"field": "...", "issue": "...", "severity": "error|warning"}}
  ],
  "is_complete_and_readable": true/false,
  "confidence": 0.0-1.0
}}

Only return valid JSON."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        import json
        text = response.content[0].text.strip()
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)


class KYCValidator:
    """Main orchestrator - runs all checks and produces a ValidationResult."""

    def __init__(self):
        self.image_checker = ImageQualityChecker()
        self.ai_analyzer = AIDocumentAnalyzer()

    def validate(
        self,
        document_path: str,
        document_type: DocumentType,
        user_profile: dict
    ) -> ValidationResult:
        result = ValidationResult(document_type=document_type, passed=True)
        issues = []

        # --- Step 1: Image Quality Checks (fast, local) ---
        if document_path.lower().endswith((".jpg", ".jpeg", ".png")):
            blur_ok, blur_score = self.image_checker.check_blur(document_path)
            if not blur_ok:
                issues.append(ValidationIssue(
                    code="IMG_BLUR",
                    severity="error",
                    field="image_quality",
                    description=f"Image is too blurry (sharpness score: {blur_score}). Minimum required: 100.",
                    suggestion="Retake the photo in good lighting, hold steady, and ensure the document is flat."
                ))

            res_ok, (w, h) = self.image_checker.check_resolution(document_path)
            if not res_ok:
                issues.append(ValidationIssue(
                    code="IMG_RESOLUTION",
                    severity="error",
                    field="image_quality",
                    description=f"Image resolution too low ({w}x{h}). Minimum: 800x600.",
                    suggestion="Use your phone camera in high-quality mode or scan the document."
                ))

        # --- Step 2: AI Field Extraction & Cross-Validation ---
        try:
            if document_type == DocumentType.PHOTO_ID:
                ai_result = self.ai_analyzer.analyze_photo_id(document_path, user_profile)
                result.extracted_fields = ai_result.get("extracted_fields", {})
                result.confidence_score = ai_result.get("confidence", 0.0)

                if not ai_result.get("is_document_readable", True):
                    for readability_issue in ai_result.get("readability_issues", []):
                        issues.append(ValidationIssue(
                            code="DOC_NOT_READABLE",
                            severity="error",
                            field="document",
                            description=readability_issue,
                            suggestion="Ensure the entire document is visible, unobstructed, and well-lit."
                        ))

                for mismatch in ai_result.get("mismatches", []):
                    issues.append(ValidationIssue(
                        code=f"MISMATCH_{mismatch['field'].upper()}",
                        severity=mismatch.get("severity", "error"),
                        field=mismatch["field"],
                        description=f"Document shows '{mismatch['document_value']}' but your profile has '{mismatch['profile_value']}'.",
                        suggestion=f"Update your Questrade profile or use an ID that matches your registered {mismatch['field']}."
                    ))

            elif document_type == DocumentType.W8BEN:
                ai_result = self.ai_analyzer.analyze_w8ben(document_path, user_profile)
                result.extracted_fields = ai_result.get("extracted_fields", {})
                result.confidence_score = ai_result.get("confidence", 0.0)

                for missing_field in ai_result.get("missing_required_fields", []):
                    issues.append(ValidationIssue(
                        code="W8BEN_MISSING_FIELD",
                        severity="error",
                        field=missing_field,
                        description=f"Required field '{missing_field}' is missing or incomplete.",
                        suggestion=f"Fill in the '{missing_field}' field before submitting."
                    ))

                for mismatch in ai_result.get("mismatches", []):
                    issues.append(ValidationIssue(
                        code=f"W8BEN_{mismatch['field'].upper()}_MISMATCH",
                        severity=mismatch.get("severity", "error"),
                        field=mismatch["field"],
                        description=mismatch["issue"],
                        suggestion="Ensure this field matches your Questrade account profile exactly."
                    ))

                if not ai_result.get("is_signed", True):
                    issues.append(ValidationIssue(
                        code="W8BEN_UNSIGNED",
                        severity="error",
                        field="signature",
                        description="W-8BEN form is not signed.",
                        suggestion="Sign and date Part III of the W-8BEN form before submitting."
                    ))

            elif document_type == DocumentType.FINANCIAL_DOCUMENT:
                ai_result = self.ai_analyzer.analyze_financial_document(document_path, user_profile)
                result.extracted_fields = ai_result.get("extracted_fields", {})
                result.confidence_score = ai_result.get("confidence", 0.0)

                if ai_result.get("extracted_fields", {}).get("account_number_truncated"):
                    issues.append(ValidationIssue(
                        code="FIN_TRUNCATED_ACCOUNT",
                        severity="error",
                        field="account_number",
                        description="Bank account number appears to be truncated or partially hidden.",
                        suggestion="Submit a statement that shows your full account number. Avoid screenshots that cut off the number."
                    ))

                for issue in ai_result.get("issues", []):
                    issues.append(ValidationIssue(
                        code=f"FIN_{issue['field'].upper()}",
                        severity=issue.get("severity", "warning"),
                        field=issue["field"],
                        description=issue["issue"],
                        suggestion="Ensure all document information is fully visible and legible."
                    ))

        except Exception as e:
            issues.append(ValidationIssue(
                code="AI_ANALYSIS_FAILED",
                severity="warning",
                field="system",
                description=f"AI analysis could not complete: {str(e)}",
                suggestion="Please review your document manually before submitting."
            ))

        # --- Step 3: Final verdict ---
        error_issues = [i for i in issues if i.severity == "error"]
        result.passed = len(error_issues) == 0
        result.issues = issues
        return result

    def format_user_report(self, result: ValidationResult) -> str:
        """Human-readable checklist for the user."""
        lines = [f"\n{'='*60}", f"📋 KYC Document Validation Report", f"Document Type: {result.document_type.value}", f"{'='*60}"]

        if result.passed:
            lines.append("✅ Your document looks good! Ready to submit.\n")
        else:
            lines.append("❌ Issues found. Please fix before submitting:\n")

        errors = [i for i in result.issues if i.severity == "error"]
        warnings = [i for i in result.issues if i.severity == "warning"]

        if errors:
            lines.append("🚨 REQUIRED FIXES:")
            for i, issue in enumerate(errors, 1):
                lines.append(f"  {i}. [{issue.code}] {issue.description}")
                lines.append(f"     → {issue.suggestion}")

        if warnings:
            lines.append("\n⚠️  WARNINGS (recommended to fix):")
            for i, issue in enumerate(warnings, 1):
                lines.append(f"  {i}. {issue.description}")
                lines.append(f"     → {issue.suggestion}")

        lines.append(f"\n📊 AI Confidence Score: {result.confidence_score:.0%}")
        lines.append("=" * 60)
        return "\n".join(lines)
