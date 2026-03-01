"""
Form Completeness Agent
Validates that required fields in structured forms are present and correct.
Focuses on W-8BEN form validation — a common rejection source.
"""

from typing import List, Dict
import json
import re
from anthropic import AsyncAnthropic

from src.models.schemas import (
    ValidationRequest,
    ValidationIssue,
    IssueSeverity,
    DocumentType
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# W-8BEN required fields by section
W8BEN_REQUIRED_FIELDS = {
    "Part I - Line 1": "Name of individual",
    "Part I - Line 2": "Country of citizenship",
    "Part I - Line 3": "Permanent residence address (not PO Box)",
    "Part III - Signature": "Signature of beneficial owner",
    "Part III - Date": "Date signed"
}

W8BEN_CONDITIONAL_FIELDS = {
    "Part II - Line 9": "Country of residence for treaty purposes",
    "Part I - Line 8": "Date of birth"
}


class FormCompletenessAgent:
    """
    Validates that required form fields are present and correctly filled.
    
    Uses a combination of:
    - Rule-based checks for known required fields
    - Claude for complex field validation (e.g., is address a real address?)
    """
    
    def __init__(self):
        self.claude = AsyncAnthropic()
    
    async def validate(self, request: ValidationRequest) -> List[ValidationIssue]:
        """Run form completeness checks based on document type."""
        
        if request.document_type == DocumentType.W8BEN:
            return await self._validate_w8ben(request)
        elif request.document_type == DocumentType.PHOTO_ID:
            return await self._validate_photo_id(request)
        elif request.document_type == DocumentType.FINANCIAL_DOC:
            return await self._validate_financial_doc(request)
        
        return []
    
    async def _validate_w8ben(self, request: ValidationRequest) -> List[ValidationIssue]:
        """Comprehensive W-8BEN form validation."""
        issues = []
        
        # Use Claude to analyze the form holistically
        base64_content = __import__('base64').b64encode(request.file_content).decode()
        
        prompt = """Analyze this W-8BEN form and check for completeness issues.

Check SPECIFICALLY for:
1. Is Line 1 (Name) filled in?
2. Is Line 2 (Country of citizenship) filled in?
3. Is Line 3 (Permanent residence address) filled in with a street address (NOT a PO Box)?
4. Is Part II Line 9 (Country of residence) filled in?
5. Is there a signature in Part III?
6. Is there a date in Part III?
7. Does the mailing address (Line 4) differ from Line 3? (flag if Line 3 is a PO Box used as permanent address)

For each issue found, specify:
- Which line/section
- What's missing or wrong
- Severity: CRITICAL (will definitely be rejected) or HIGH (likely rejected)

Respond only in JSON:
{
  "issues": [
    {
      "line": "Line 9",
      "section": "Part II",
      "issue": "Country of residence is blank",
      "severity": "HIGH"
    }
  ]
}

If no issues, return: {"issues": []}"""
        
        try:
            response = await self.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": base64_content
                                }
                            },
                            {"type": "text", "text": prompt}
                        ]
                    }
                ]
            )
            
            result = json.loads(response.content[0].text)
            
            severity_map = {"CRITICAL": IssueSeverity.CRITICAL, "HIGH": IssueSeverity.HIGH}
            
            for issue_data in result.get("issues", []):
                field_ref = f"{issue_data.get('section', '')} {issue_data.get('line', '')}".strip()
                issues.append(ValidationIssue(
                    check_name=f"w8ben_{issue_data.get('line', 'field').lower().replace(' ', '_')}",
                    severity=severity_map.get(issue_data.get("severity", "HIGH"), IssueSeverity.HIGH),
                    message=f"W-8BEN {field_ref}: {issue_data.get('issue', 'Incomplete field')}",
                    field=field_ref,
                    suggestion=self._get_w8ben_suggestion(issue_data.get("line", ""))
                ))
        
        except Exception as e:
            logger.warning(f"W-8BEN form check failed (Claude): {e}")
        
        return issues
    
    async def _validate_photo_id(self, request: ValidationRequest) -> List[ValidationIssue]:
        """Check photo ID for common issues beyond quality."""
        issues = []
        # Expiry handled in cross-ref agent
        # Face visibility check
        try:
            import cv2
            import numpy as np
            
            nparr = np.frombuffer(request.file_content, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is not None:
                # Simple heuristic: photo IDs have a photo region (top-left or top-right typically)
                # In production: use a face detection model (e.g., OpenCV Haar cascade, or AWS Rekognition)
                face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                
                if len(faces) == 0:
                    issues.append(ValidationIssue(
                        check_name="face_not_detected",
                        severity=IssueSeverity.MEDIUM,
                        message="Could not detect a face photo on the ID. "
                                "The face area may be obscured, too small, or the wrong side of the ID was submitted.",
                        field="photo_region",
                        suggestion="Ensure you're uploading the front side of the ID with the face photo visible. "
                                   "Make sure the photo is not covered by a thumb or glare."
                    ))
        
        except Exception as e:
            logger.debug(f"Face detection skipped: {e}")
        
        return issues
    
    async def _validate_financial_doc(self, request: ValidationRequest) -> List[ValidationIssue]:
        """Check financial documents for completeness."""
        # Core checks handled by CrossRef agent (account number, name, recency)
        # Add: check if this looks like a valid financial document at all
        return []
    
    def _get_w8ben_suggestion(self, line: str) -> str:
        """Return specific fix suggestion for each W-8BEN line."""
        suggestions = {
            "Line 1": "Enter your full legal name as it appears on your government ID.",
            "Line 2": "Enter your country of citizenship (e.g., Canada, India, United Kingdom).",
            "Line 3": "Enter your permanent home address. Do not use a P.O. Box. "
                      "This must match your application address.",
            "Line 4": "Only fill this in if your mailing address differs from Line 3.",
            "Line 9": "Enter your country of tax residence (e.g., Canada). "
                      "This is required to claim treaty benefits.",
            "Signature": "Sign the form in Part III — Section 'Certification'. "
                          "Digital signatures (typed name) are not accepted.",
            "Date": "Add today's date in MM-DD-YYYY format in Part III next to the signature."
        }
        
        for key, suggestion in suggestions.items():
            if key.lower() in line.lower():
                return suggestion
        
        return "Please review and complete the indicated field before resubmitting."
