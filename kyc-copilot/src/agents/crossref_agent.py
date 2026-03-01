"""
Cross-Reference Agent
Extracts data from submitted documents and cross-references against user profile.
Catches mismatches like: DOB, name, address — exactly what Questrade kept rejecting.
"""

import re
import json
from typing import List, Dict, Optional, Tuple
from difflib import SequenceMatcher
import base64
from datetime import datetime

import boto3
from openai import AsyncOpenAI

from src.models.schemas import (
    ValidationRequest,
    ValidationIssue,
    IssueSeverity,
    DocumentType,
    UserProfile
)
from src.models.database import get_user_profile
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Matching thresholds
NAME_SIMILARITY_THRESHOLD = 0.85  # Fuzzy match threshold for names
ADDRESS_SIMILARITY_THRESHOLD = 0.80


class CrossRefAgent:
    """
    Agent that extracts structured data from documents using AWS Textract
    and cross-references against the user's stored profile.
    
    This directly addresses the most common KYC rejections:
    - DOB on ID doesn't match profile
    - Address on W-8BEN doesn't match application
    - Name variations (middle name, maiden name, etc.)
    """
    
    def __init__(self):
        self.textract = boto3.client("textract", region_name="ca-central-1")
        self.openai_client = AsyncOpenAI()
    
    async def validate(self, request: ValidationRequest) -> List[ValidationIssue]:
        """Extract and cross-reference document data against user profile."""
        issues = []
        
        # Fetch user profile from database
        profile = await get_user_profile(request.user_id)
        if not profile:
            logger.warning(f"No profile found for user {request.user_id} — skipping cross-reference")
            return []
        
        # Extract structured data from document using Textract + GPT-4o
        extracted_data = await self._extract_document_data(
            request.file_content,
            request.document_type
        )
        
        logger.info(f"Extracted data: {extracted_data}")
        
        # Run appropriate cross-reference checks based on document type
        if request.document_type == DocumentType.PHOTO_ID:
            issues.extend(self._check_name_match(extracted_data, profile))
            issues.extend(self._check_dob_match(extracted_data, profile))
            issues.extend(self._check_id_expiry(extracted_data))
        
        elif request.document_type == DocumentType.W8BEN:
            issues.extend(self._check_name_match(extracted_data, profile))
            issues.extend(self._check_address_match(extracted_data, profile))
            issues.extend(self._check_country_present(extracted_data))
        
        elif request.document_type == DocumentType.FINANCIAL_DOC:
            issues.extend(self._check_name_match(extracted_data, profile))
            issues.extend(self._check_account_number_visible(extracted_data))
            issues.extend(self._check_document_recency(extracted_data))
        
        return issues
    
    async def _extract_document_data(
        self,
        file_content: bytes,
        document_type: DocumentType
    ) -> Dict:
        """
        Two-stage extraction:
        1. AWS Textract: Fast, cheap, gets all raw text
        2. GPT-4o: Structures the text into fields we need
        """
        # Stage 1: Textract raw text extraction
        raw_text = self._run_textract(file_content)
        
        # Stage 2: GPT-4o structures the extracted text
        structured_data = await self._structure_with_gpt(raw_text, document_type)
        
        return structured_data
    
    def _run_textract(self, file_content: bytes) -> str:
        """Run AWS Textract on document to extract all text."""
        try:
            response = self.textract.detect_document_text(
                Document={"Bytes": file_content}
            )
            
            lines = []
            for block in response.get("Blocks", []):
                if block["BlockType"] == "LINE":
                    lines.append(block.get("Text", ""))
            
            raw_text = "\n".join(lines)
            logger.debug(f"Textract extracted {len(lines)} lines")
            return raw_text
            
        except Exception as e:
            logger.error(f"Textract failed: {e}")
            return ""
    
    async def _structure_with_gpt(self, raw_text: str, document_type: DocumentType) -> Dict:
        """Use GPT-4o to parse Textract output into structured fields."""
        
        field_specs = {
            DocumentType.PHOTO_ID: """
Extract these fields from the ID document text:
- full_name: Full name as printed
- date_of_birth: DOB in YYYY-MM-DD format
- expiry_date: Document expiry in YYYY-MM-DD format  
- address: Full address if present
- id_number: ID/license number
- country_of_issue: Country that issued the document
""",
            DocumentType.W8BEN: """
Extract these fields from the W-8BEN form:
- full_name: Name on line 1
- country_of_citizenship: Country on line 2
- permanent_address: Address on line 3 (Section 3)
- mailing_address: Address on line 4 (if different)
- country_of_residence: Country on line 9 (Part II)
- signature_present: true/false
- date_signed: Date of signature in YYYY-MM-DD format
""",
            DocumentType.FINANCIAL_DOC: """
Extract these fields from the bank/financial document:
- account_holder_name: Name on the account
- account_number: Full account number (check if truncated/masked)
- bank_name: Name of the financial institution
- statement_date: Most recent date on the document in YYYY-MM-DD format
- is_account_number_complete: true if full account number visible, false if truncated
"""
        }
        
        fields = field_specs.get(document_type, "Extract all relevant identity fields.")
        
        prompt = f"""Parse the following text extracted from a KYC document. 
        
{fields}

Return ONLY a JSON object with these fields. If a field is not found, use null.
Do not add explanation or markdown.

Extracted text:
---
{raw_text[:3000]}
---
"""
        
        try:
            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                response_format={"type": "json_object"}
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"GPT structuring failed: {e}")
            return {}
    
    def _check_name_match(self, extracted: Dict, profile: UserProfile) -> List[ValidationIssue]:
        """Check if name on document matches profile name."""
        doc_name = extracted.get("full_name") or extracted.get("account_holder_name")
        if not doc_name or not profile.full_name:
            return []
        
        similarity = self._name_similarity(doc_name.lower(), profile.full_name.lower())
        
        logger.debug(f"Name similarity: {similarity:.2f} ('{doc_name}' vs '{profile.full_name}')")
        
        if similarity < NAME_SIMILARITY_THRESHOLD:
            return [ValidationIssue(
                check_name="name_mismatch",
                severity=IssueSeverity.HIGH,
                message=f"Name on document ('{doc_name}') doesn't closely match your profile name ('{profile.full_name}').",
                field="full_name",
                suggestion="Ensure the name on your document exactly matches the name you used when creating your account. "
                           "If you've changed your name, contact support to update your profile first.",
                technical_detail={"similarity_score": round(similarity, 2), "extracted_name": doc_name}
            )]
        
        return []
    
    def _check_dob_match(self, extracted: Dict, profile: UserProfile) -> List[ValidationIssue]:
        """
        Check if DOB on document matches profile.
        This was Sana's second rejection — Dec 19, 2024 email.
        """
        doc_dob = extracted.get("date_of_birth")
        if not doc_dob or not profile.date_of_birth:
            return []
        
        try:
            doc_dob_parsed = datetime.strptime(doc_dob, "%Y-%m-%d").date()
            if doc_dob_parsed != profile.date_of_birth:
                return [ValidationIssue(
                    check_name="dob_mismatch",
                    severity=IssueSeverity.CRITICAL,
                    message=f"Date of birth on document ({doc_dob}) does not match your profile ({profile.date_of_birth}). "
                            f"This will cause automatic rejection.",
                    field="date_of_birth",
                    suggestion="If your date of birth on the document is correct, contact Questrade support to update "
                               "your profile DOB before resubmitting. If the document DOB is wrong, submit a different ID.",
                    technical_detail={"document_dob": doc_dob, "profile_dob": str(profile.date_of_birth)}
                )]
        except ValueError as e:
            logger.warning(f"Could not parse DOB: {doc_dob} — {e}")
        
        return []
    
    def _check_address_match(self, extracted: Dict, profile: UserProfile) -> List[ValidationIssue]:
        """
        Check if address in W-8BEN Section 3 matches application address.
        This was Sana's third rejection — Apr 29, 2025 email.
        """
        doc_address = extracted.get("permanent_address")
        if not doc_address or not profile.address:
            if not doc_address:
                return [ValidationIssue(
                    check_name="address_mismatch",
                    severity=IssueSeverity.HIGH,
                    message="Could not read address from Section 3 of your W-8BEN.",
                    field="permanent_address",
                    suggestion="Ensure Section 3 (Permanent residence address) is clearly filled in with your full address."
                )]
            return []
        
        similarity = SequenceMatcher(
            None,
            self._normalize_address(doc_address),
            self._normalize_address(profile.address)
        ).ratio()
        
        logger.debug(f"Address similarity: {similarity:.2f}")
        
        if similarity < ADDRESS_SIMILARITY_THRESHOLD:
            return [ValidationIssue(
                check_name="address_mismatch",
                severity=IssueSeverity.CRITICAL,
                message=f"Address in Section 3 of your W-8BEN ('{doc_address}') doesn't match "
                        f"your application address ('{profile.address}').",
                field="permanent_address",
                suggestion="Option 1: Update Section 3 to exactly match your address on file. "
                           "Option 2: Log into your account and update your profile address to match the form.",
                technical_detail={"doc_address": doc_address, "profile_address": profile.address, "similarity": round(similarity, 2)}
            )]
        
        return []
    
    def _check_country_present(self, extracted: Dict) -> List[ValidationIssue]:
        """
        Check if W-8BEN Part II Section 9 has country of residence.
        This was also part of Sana's Apr 29, 2025 rejection.
        """
        country = extracted.get("country_of_residence")
        
        if not country or country.strip() == "":
            return [ValidationIssue(
                check_name="missing_country",
                severity=IssueSeverity.HIGH,
                message="Part II Section 9 of your W-8BEN is missing your country of residence.",
                field="country_of_residence",
                suggestion="Fill in your current country of residence in Part II, Section 9. "
                           "For Canadian residents, enter 'Canada'."
            )]
        
        return []
    
    def _check_account_number_visible(self, extracted: Dict) -> List[ValidationIssue]:
        """
        Check if bank account number is not truncated.
        This was Sana's fourth rejection — May 25, 2025 email.
        """
        is_complete = extracted.get("is_account_number_complete")
        account_number = extracted.get("account_number", "")
        
        # Also check using pattern: if account number contains 'XXXX' or '****' it's masked
        is_masked = account_number and bool(re.search(r'[Xx\*]{3,}', str(account_number)))
        
        if is_complete is False or is_masked:
            return [ValidationIssue(
                check_name="truncated_account",
                severity=IssueSeverity.CRITICAL,
                message="Bank account number appears to be truncated or masked in this document. "
                        "The full account number must be visible.",
                field="account_number",
                suggestion="Upload a different document that shows the complete account number: "
                           "try a void cheque, official bank letter, or a statement page that includes the full account details."
            )]
        
        return []
    
    def _check_id_expiry(self, extracted: Dict) -> List[ValidationIssue]:
        """Check that the ID hasn't expired."""
        expiry = extracted.get("expiry_date")
        if not expiry:
            return []
        
        try:
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            today = datetime.now().date()
            
            if expiry_date < today:
                return [ValidationIssue(
                    check_name="expired_id",
                    severity=IssueSeverity.CRITICAL,
                    message=f"Your ID expired on {expiry}. Expired IDs cannot be accepted.",
                    field="expiry_date",
                    suggestion="Please submit a valid, non-expired government-issued ID."
                )]
        except ValueError:
            pass
        
        return []
    
    def _check_document_recency(self, extracted: Dict) -> List[ValidationIssue]:
        """Check that financial document is not older than 90 days."""
        stmt_date = extracted.get("statement_date")
        if not stmt_date:
            return []
        
        try:
            from datetime import timedelta
            doc_date = datetime.strptime(stmt_date, "%Y-%m-%d").date()
            today = datetime.now().date()
            age_days = (today - doc_date).days
            
            if age_days > 90:
                return [ValidationIssue(
                    check_name="outdated_document",
                    severity=IssueSeverity.HIGH,
                    message=f"Financial document is {age_days} days old (dated {stmt_date}). "
                            f"Documents must be within 90 days.",
                    field="statement_date",
                    suggestion="Submit a recent bank statement from the last 3 months."
                )]
        except ValueError:
            pass
        
        return []
    
    def _name_similarity(self, name1: str, name2: str) -> float:
        """Fuzzy name matching that handles reordered names and initials."""
        # Direct similarity
        direct = SequenceMatcher(None, name1, name2).ratio()
        
        # Token-based (handles "Khan, Sana" vs "Sana Khan")
        tokens1 = set(name1.split())
        tokens2 = set(name2.split())
        
        if tokens1 and tokens2:
            intersection = tokens1 & tokens2
            token_sim = len(intersection) / max(len(tokens1), len(tokens2))
            return max(direct, token_sim)
        
        return direct
    
    def _normalize_address(self, address: str) -> str:
        """Normalize address for comparison (lowercase, remove punctuation, expand abbreviations)."""
        address = address.lower().strip()
        
        # Common abbreviation expansions
        abbrevs = {
            "st.": "street", "st ": "street ", "ave.": "avenue", "ave ": "avenue ",
            "blvd.": "boulevard", "dr.": "drive", "rd.": "road",
            "apt.": "apartment", "apt ": "apartment ", "#": "unit "
        }
        
        for abbrev, full in abbrevs.items():
            address = address.replace(abbrev, full)
        
        # Remove commas, periods, extra spaces
        address = re.sub(r'[,\.]', '', address)
        address = re.sub(r'\s+', ' ', address)
        
        return address.strip()
