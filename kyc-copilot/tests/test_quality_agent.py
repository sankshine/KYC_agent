"""
Test Suite — Document Quality Agent
Tests cover all rejection scenarios Sana experienced
"""

import pytest
import asyncio
import numpy as np
import cv2
from unittest.mock import AsyncMock, patch, MagicMock
from io import BytesIO
from PIL import Image

from src.agents.quality_agent import DocumentQualityAgent
from src.models.schemas import ValidationRequest, DocumentType, IssueSeverity


def create_test_image(
    width: int = 1200,
    height: int = 800,
    blur_sigma: float = 0,
    brightness: int = 128
) -> bytes:
    """Create a synthetic test image with configurable properties."""
    img = np.ones((height, width, 3), dtype=np.uint8) * brightness
    
    # Add some text-like content
    cv2.putText(img, "SANA KHAN", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    cv2.putText(img, "DOB: 1990-05-15", (100, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 2)
    cv2.putText(img, "EXP: 2028-05-15", (100, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 2)
    
    # Apply blur if requested
    if blur_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur_sigma)
    
    # Encode to JPEG bytes
    _, buffer = cv2.imencode('.jpg', img)
    return buffer.tobytes()


def make_request(file_bytes: bytes, doc_type: DocumentType = DocumentType.PHOTO_ID) -> ValidationRequest:
    """Helper to create a test ValidationRequest."""
    return ValidationRequest(
        validation_id="test-001",
        user_id="user-123",
        document_type=doc_type,
        file_content=file_bytes,
        file_name="test_document.jpg",
        content_type="image/jpeg"
    )


class TestDocumentQualityAgent:
    """Tests for DocumentQualityAgent."""
    
    @pytest.fixture
    def agent(self):
        return DocumentQualityAgent()
    
    @pytest.fixture
    def sharp_image(self):
        """Sharp, clear image — should pass quality checks."""
        return create_test_image(width=1200, height=800, blur_sigma=0, brightness=128)
    
    @pytest.fixture
    def blurry_image(self):
        """Very blurry image — Sana's first rejection scenario (Jan 1, 2025)."""
        return create_test_image(width=1200, height=800, blur_sigma=25, brightness=128)
    
    @pytest.fixture
    def low_res_image(self):
        """Low resolution image."""
        return create_test_image(width=300, height=200, blur_sigma=0, brightness=128)
    
    @pytest.fixture
    def dark_image(self):
        """Very dark image."""
        return create_test_image(width=1200, height=800, blur_sigma=0, brightness=20)
    
    @pytest.fixture
    def overexposed_image(self):
        """Overexposed/washed out image."""
        return create_test_image(width=1200, height=800, blur_sigma=0, brightness=240)
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_sharp_image_passes(self, mock_vision, agent, sharp_image):
        """Sharp, well-lit image should produce no quality issues."""
        mock_vision.return_value = []
        
        request = make_request(sharp_image)
        issues = await agent.validate(request)
        
        quality_issues = [i for i in issues if i.check_name in ["blur_check", "resolution_check", "brightness_check"]]
        assert len(quality_issues) == 0, f"Sharp image should have no quality issues, got: {quality_issues}"
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_blurry_image_detected(self, mock_vision, agent, blurry_image):
        """
        Blurry image should be caught.
        This simulates Sana's rejection on Jan 1, 2025:
        'The document you have submitted for your margin account is not acceptable as it is too blurry or unclear.'
        """
        mock_vision.return_value = []
        
        request = make_request(blurry_image)
        issues = await agent.validate(request)
        
        blur_issues = [i for i in issues if i.check_name == "blur_check"]
        assert len(blur_issues) > 0, "Blurry image should trigger blur_check issue"
        assert blur_issues[0].severity in [IssueSeverity.CRITICAL, IssueSeverity.HIGH]
        assert "blur" in blur_issues[0].message.lower() or "blurry" in blur_issues[0].message.lower()
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_low_resolution_detected(self, mock_vision, agent, low_res_image):
        """Low resolution image should be flagged."""
        mock_vision.return_value = []
        
        request = make_request(low_res_image)
        issues = await agent.validate(request)
        
        res_issues = [i for i in issues if i.check_name == "resolution_check"]
        assert len(res_issues) > 0, "Low resolution image should trigger resolution_check"
        assert "resolution" in res_issues[0].message.lower() or "pixel" in res_issues[0].message.lower()
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_dark_image_detected(self, mock_vision, agent, dark_image):
        """Dark image should trigger brightness issue."""
        mock_vision.return_value = []
        
        request = make_request(dark_image)
        issues = await agent.validate(request)
        
        brightness_issues = [i for i in issues if i.check_name == "brightness_check"]
        assert len(brightness_issues) > 0, "Dark image should trigger brightness_check"
        assert "dark" in brightness_issues[0].message.lower()
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_overexposed_image_detected(self, mock_vision, agent, overexposed_image):
        """Overexposed image should trigger brightness issue."""
        mock_vision.return_value = []
        
        request = make_request(overexposed_image)
        issues = await agent.validate(request)
        
        brightness_issues = [i for i in issues if i.check_name == "brightness_check"]
        assert len(brightness_issues) > 0, "Overexposed image should trigger brightness_check"
        assert "bright" in brightness_issues[0].message.lower() or "overexposed" in brightness_issues[0].message.lower()
    
    @pytest.mark.asyncio
    async def test_corrupted_file_handled_gracefully(self, agent):
        """Corrupted/invalid file should return a clear error, not crash."""
        request = make_request(b"this is not an image file at all!!!")
        issues = await agent.validate(request)
        
        assert len(issues) > 0, "Corrupted file should produce at least one issue"
        assert issues[0].severity == IssueSeverity.CRITICAL
        assert "corrupted" in issues[0].message.lower() or "read" in issues[0].message.lower()
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_critical_blur_skips_are_caught(self, mock_vision, agent):
        """Extremely blurry images should get CRITICAL severity."""
        mock_vision.return_value = []
        
        # Create extremely blurry image (sigma=50)
        very_blurry = create_test_image(blur_sigma=50)
        request = make_request(very_blurry)
        issues = await agent.validate(request)
        
        critical_issues = [i for i in issues if i.severity == IssueSeverity.CRITICAL]
        assert len(critical_issues) > 0, "Extremely blurry image should have CRITICAL issues"
    
    def test_blur_score_calculation(self, agent):
        """Test that the Laplacian variance correctly distinguishes blur levels."""
        sharp = create_test_image(blur_sigma=0)
        blurry = create_test_image(blur_sigma=15)
        very_blurry = create_test_image(blur_sigma=40)
        
        import numpy as np
        import cv2
        
        def get_blur_score(img_bytes):
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            return cv2.Laplacian(gray, cv2.CV_64F).var()
        
        sharp_score = get_blur_score(sharp)
        blurry_score = get_blur_score(blurry)
        very_blurry_score = get_blur_score(very_blurry)
        
        assert sharp_score > blurry_score, "Sharp should have higher blur score than blurry"
        assert blurry_score > very_blurry_score, "Blurry should have higher score than very blurry"
    
    @pytest.mark.asyncio
    @patch.object(DocumentQualityAgent, '_check_with_vision', new_callable=AsyncMock)
    async def test_all_suggestions_are_actionable(self, mock_vision, agent, blurry_image):
        """All issues returned should have suggestions."""
        mock_vision.return_value = []
        
        request = make_request(blurry_image)
        issues = await agent.validate(request)
        
        for issue in issues:
            assert issue.suggestion is not None, f"Issue '{issue.check_name}' has no suggestion"
            assert len(issue.suggestion) > 10, f"Suggestion for '{issue.check_name}' is too short"
