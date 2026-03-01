"""
Test Suite — Cross-Reference Agent
Specifically tests all 4 rejection scenarios from Sana's Questrade experience
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date, timedelta

from src.agents.crossref_agent import CrossRefAgent
from src.models.schemas import (
    ValidationRequest,
    DocumentType,
    IssueSeverity,
    UserProfile
)


MOCK_PROFILE = UserProfile(
    user_id="user-123",
    full_name="Sana Khan",
    date_of_birth=date(1990, 5, 15),
    address="123 Main Street, Toronto, ON M5V 1A1",
    email="sana@example.com",
    country_of_residence="Canada"
)

MOCK_BLURRY_BYTES = b"fake-image-bytes"


def make_request(doc_type: DocumentType) -> ValidationRequest:
    return ValidationRequest(
        validation_id="test-crossref",
        user_id="user-123",
        document_type=doc_type,
        file_content=MOCK_BLURRY_BYTES,
        file_name="test.jpg",
        content_type="image/jpeg"
    )


class TestCrossRefAgent:
    """
    Tests modeled after real Questrade rejections:
    1. Jan 1, 2025 — Photo ID: too blurry (quality, not crossref)
    2. Dec 19, 2024 — Photo ID: DOB doesn't match profile
    3. Apr 29, 2025 — W-8BEN: address mismatch + missing Section 9
    4. May 25, 2025 — Financial doc: bank account number truncated
    """
    
    @pytest.fixture
    def agent(self):
        return CrossRefAgent()
    
    @pytest.fixture
    def mock_profile_lookup(self):
        with patch('src.agents.crossref_agent.get_user_profile', 
                   new_callable=AsyncMock, return_value=MOCK_PROFILE) as mock:
            yield mock
    
    # ── Scenario 2: DOB Mismatch (Dec 19, 2024 rejection) ──────────────
    
    @pytest.mark.asyncio
    async def test_dob_mismatch_detected(self, agent, mock_profile_lookup):
        """
        Reproduce: Photo ID with DOB 1991-05-15 submitted but profile has 1990-05-15.
        Should generate CRITICAL dob_mismatch issue.
        """
        extracted_data = {
            "full_name": "Sana Khan",
            "date_of_birth": "1991-05-15",  # Wrong year
            "expiry_date": "2028-05-15"
        }
        
        with patch.object(agent, '_extract_document_data', 
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.PHOTO_ID)
            issues = await agent.validate(request)
        
        dob_issues = [i for i in issues if i.check_name == "dob_mismatch"]
        assert len(dob_issues) == 1, "Should detect one DOB mismatch"
        assert dob_issues[0].severity == IssueSeverity.CRITICAL
        assert "1991" in dob_issues[0].message or "birth" in dob_issues[0].message.lower()
    
    @pytest.mark.asyncio
    async def test_dob_match_passes(self, agent, mock_profile_lookup):
        """Correct DOB should not flag any issues."""
        extracted_data = {
            "full_name": "Sana Khan",
            "date_of_birth": "1990-05-15",  # Correct
            "expiry_date": "2028-05-15"
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.PHOTO_ID)
            issues = await agent.validate(request)
        
        dob_issues = [i for i in issues if i.check_name == "dob_mismatch"]
        assert len(dob_issues) == 0, "Matching DOB should not flag any issues"
    
    # ── Scenario 3a: W-8BEN Address Mismatch (Apr 29, 2025 rejection) ──
    
    @pytest.mark.asyncio
    async def test_w8ben_address_mismatch_detected(self, agent, mock_profile_lookup):
        """
        Reproduce: W-8BEN Section 3 shows "123 Main St" but profile has "123 Main Street".
        Should detect address mismatch.
        """
        extracted_data = {
            "full_name": "Sana Khan",
            "country_of_citizenship": "Canada",
            "permanent_address": "456 Different Road, Ottawa, ON K1A 0A9",  # Wrong address
            "country_of_residence": "Canada",
            "signature_present": True
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.W8BEN)
            issues = await agent.validate(request)
        
        addr_issues = [i for i in issues if i.check_name == "address_mismatch"]
        assert len(addr_issues) == 1, "Should detect address mismatch"
        assert addr_issues[0].severity == IssueSeverity.CRITICAL
    
    @pytest.mark.asyncio
    async def test_w8ben_minor_address_variation_passes(self, agent, mock_profile_lookup):
        """Minor address variation (St vs Street) should NOT flag as mismatch."""
        extracted_data = {
            "full_name": "Sana Khan",
            "permanent_address": "123 Main St, Toronto, ON M5V 1A1",  # Minor variation
            "country_of_residence": "Canada",
            "signature_present": True
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.W8BEN)
            issues = await agent.validate(request)
        
        addr_issues = [i for i in issues if i.check_name == "address_mismatch"]
        assert len(addr_issues) == 0, "Minor address variation (St vs Street) should pass with fuzzy matching"
    
    # ── Scenario 3b: W-8BEN Missing Country (Apr 29, 2025 rejection) ───
    
    @pytest.mark.asyncio
    async def test_w8ben_missing_country_detected(self, agent, mock_profile_lookup):
        """
        Reproduce: Section 9 country of residence blank.
        'Part II Section 9 is missing the country that you currently reside in.'
        """
        extracted_data = {
            "full_name": "Sana Khan",
            "permanent_address": "123 Main Street, Toronto, ON M5V 1A1",
            "country_of_residence": None,  # Missing!
            "signature_present": True
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.W8BEN)
            issues = await agent.validate(request)
        
        country_issues = [i for i in issues if i.check_name == "missing_country"]
        assert len(country_issues) == 1, "Missing Section 9 country should be flagged"
        assert country_issues[0].severity == IssueSeverity.HIGH
        assert "section 9" in country_issues[0].message.lower() or "country" in country_issues[0].message.lower()
    
    # ── Scenario 4: Truncated Account Number (May 25, 2025 rejection) ──
    
    @pytest.mark.asyncio
    async def test_truncated_account_number_detected(self, agent, mock_profile_lookup):
        """
        Reproduce: Bank statement with account number showing as "XXXX1234" or "****1234".
        'The financial document you have submitted is not fully visible as the bank account number is truncated.'
        """
        extracted_data = {
            "account_holder_name": "Sana Khan",
            "account_number": "XXXX-XXXX-1234",  # Masked/truncated
            "bank_name": "TD Canada Trust",
            "statement_date": "2025-04-15",
            "is_account_number_complete": False
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.FINANCIAL_DOC)
            issues = await agent.validate(request)
        
        account_issues = [i for i in issues if i.check_name == "truncated_account"]
        assert len(account_issues) == 1, "Truncated account number should be flagged"
        assert account_issues[0].severity == IssueSeverity.CRITICAL
    
    @pytest.mark.asyncio
    async def test_full_account_number_passes(self, agent, mock_profile_lookup):
        """Full account number visible should not flag any issues."""
        extracted_data = {
            "account_holder_name": "Sana Khan",
            "account_number": "001234567890",  # Full number
            "bank_name": "TD Canada Trust",
            "statement_date": "2025-04-15",
            "is_account_number_complete": True
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.FINANCIAL_DOC)
            issues = await agent.validate(request)
        
        account_issues = [i for i in issues if i.check_name == "truncated_account"]
        assert len(account_issues) == 0, "Full account number should not flag issues"
    
    # ── Expired ID ───────────────────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_expired_id_detected(self, agent, mock_profile_lookup):
        """Expired ID should be caught."""
        extracted_data = {
            "full_name": "Sana Khan",
            "date_of_birth": "1990-05-15",
            "expiry_date": "2020-01-01"  # Expired in 2020
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.PHOTO_ID)
            issues = await agent.validate(request)
        
        expiry_issues = [i for i in issues if i.check_name == "expired_id"]
        assert len(expiry_issues) == 1, "Expired ID should be flagged"
        assert expiry_issues[0].severity == IssueSeverity.CRITICAL
    
    # ── Name Matching ─────────────────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_name_format_variations_pass(self, agent, mock_profile_lookup):
        """Different name formats should pass with fuzzy matching."""
        name_variations = [
            "SANA KHAN",          # All caps
            "Khan, Sana",         # Last, First
            "S. Khan",            # Initial
        ]
        
        for name in name_variations:
            extracted_data = {
                "full_name": name,
                "date_of_birth": "1990-05-15",
                "expiry_date": "2028-05-15"
            }
            
            with patch.object(agent, '_extract_document_data',
                              new_callable=AsyncMock, return_value=extracted_data):
                request = make_request(DocumentType.PHOTO_ID)
                issues = await agent.validate(request)
            
            name_issues = [i for i in issues if i.check_name == "name_mismatch"]
            assert len(name_issues) == 0, f"Name variation '{name}' should pass fuzzy match"
    
    @pytest.mark.asyncio
    async def test_completely_different_name_flagged(self, agent, mock_profile_lookup):
        """Completely different name should be flagged."""
        extracted_data = {
            "full_name": "John Smith",  # Completely different
            "date_of_birth": "1990-05-15",
            "expiry_date": "2028-05-15"
        }
        
        with patch.object(agent, '_extract_document_data',
                          new_callable=AsyncMock, return_value=extracted_data):
            request = make_request(DocumentType.PHOTO_ID)
            issues = await agent.validate(request)
        
        name_issues = [i for i in issues if i.check_name == "name_mismatch"]
        assert len(name_issues) == 1, "Completely different name should be flagged"
