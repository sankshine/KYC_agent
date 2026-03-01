"""Unit tests for KYC document validator."""
import pytest
from unittest.mock import patch
import numpy as np
import cv2
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.validators.document_validator import KYCValidator, DocumentType, ValidationResult, ValidationIssue

USER_PROFILE = {
    "full_name": "Sana Khan",
    "date_of_birth": "1990-05-15",
    "address": "123 Main St, Toronto, ON M5V 1A1, Canada",
}

@pytest.fixture
def validator():
    return KYCValidator()

def make_image(tmp_path, name, noise=True, size=(600, 800)):
    h, w = size
    img = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8) if noise else np.ones((h, w, 3), dtype=np.uint8) * 128
    path = str(tmp_path / name)
    cv2.imwrite(path, img)
    return path

def test_blur_blurry(validator, tmp_path):
    path = make_image(tmp_path, "blurry.jpg", noise=False)
    ok, _ = validator.image_checker.check_blur(path)
    assert not ok

def test_blur_sharp(validator, tmp_path):
    path = make_image(tmp_path, "sharp.jpg", noise=True)
    ok, _ = validator.image_checker.check_blur(path)
    assert ok

@patch("src.validators.document_validator.AIDocumentAnalyzer.analyze_photo_id")
def test_dob_mismatch(mock_analyze, validator, tmp_path):
    path = make_image(tmp_path, "id.jpg", size=(800, 1200))
    mock_analyze.return_value = {
        "extracted_fields": {"date_of_birth": "1985-03-20"},
        "mismatches": [{"field": "date_of_birth", "document_value": "1985-03-20", "profile_value": "1990-05-15", "severity": "error"}],
        "is_document_readable": True, "readability_issues": [], "confidence": 0.9
    }
    result = validator.validate(path, DocumentType.PHOTO_ID, USER_PROFILE)
    assert not result.passed
    assert any("DATE_OF_BIRTH" in i.code for i in result.issues)

@patch("src.validators.document_validator.AIDocumentAnalyzer.analyze_financial_document")
def test_truncated_account(mock_analyze, validator, tmp_path):
    path = make_image(tmp_path, "bank.jpg", size=(800, 1200))
    mock_analyze.return_value = {
        "extracted_fields": {"account_number_truncated": True, "account_number_visible": True},
        "issues": [], "is_complete_and_readable": False, "confidence": 0.85
    }
    result = validator.validate(path, DocumentType.FINANCIAL_DOCUMENT, USER_PROFILE)
    assert not result.passed
    assert any(i.code == "FIN_TRUNCATED_ACCOUNT" for i in result.issues)
