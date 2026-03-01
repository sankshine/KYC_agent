"""Tests for KYC Review Agent."""
import pytest
from unittest.mock import patch, MagicMock
import json, os, sys, numpy as np, cv2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.agents.review_agent import KYCReviewAgent, ReviewDecision, IssueFlag

USER_PROFILE = {"full_name": "Sana Khan", "date_of_birth": "1990-05-15", "address": "123 Main St, Toronto, ON M5V 1A1, Canada"}
CONTEXT = {"account_type": "margin", "previous_attempts": 3, "previous_rejections": ["DOB mismatch", "Blurry image"]}

@pytest.fixture
def agent():
    return KYCReviewAgent()

def make_img(tmp_path, name="doc.jpg"):
    img = np.random.randint(0, 255, (800, 1200, 3), dtype=np.uint8)
    p = str(tmp_path / name)
    cv2.imwrite(p, img)
    return p

@patch("src.agents.review_agent.KYCReviewAgent._encode_image")
@patch("anthropic.Anthropic.messages")
def test_recommend_reject_on_flags(mock_msgs, mock_encode, agent, tmp_path):
    mock_encode.return_value = ("base64data", "image/jpeg")
    mock_content = MagicMock()
    mock_content.text = json.dumps({
        "decision": "recommend_reject",
        "overall_confidence": 0.88,
        "extracted_data": {},
        "flags": [{
            "issue_id": "F001",
            "category": "data_mismatch",
            "description": "DOB mismatch",
            "confidence": 0.92,
            "evidence": "Document shows 1985",
            "regulatory_ref": "FINTRAC ID Verification",
            "draft_rejection_reason": "DOB on document does not match profile"
        }],
        "requires_human_review": True,
        "human_review_reason": "Data mismatch requires agent confirmation"
    })
    mock_msgs.create.return_value = MagicMock(content=[mock_content])
    agent.client = MagicMock()
    agent.client.messages.create.return_value = MagicMock(content=[mock_content])

    p = make_img(tmp_path)
    packet = agent.review_document(p, "photo_id", USER_PROFILE, CONTEXT, "test-123")
    assert packet.decision == ReviewDecision.RECOMMEND_REJECT
    assert len(packet.flags) == 1
    assert "DOB" in packet.draft_rejection_email

def test_fraud_override(agent):
    """Fraud flag should override any other decision."""
    flags = [IssueFlag("F001", "fraud_indicator", "Tampered document", 0.85, "Evidence", "FINTRAC", "Fraud")]
    decision = agent._determine_decision("recommend_approve", flags, 0.9)
    assert decision == ReviewDecision.FRAUD_FLAG

def test_low_confidence_escalates(agent):
    """Very low confidence should escalate regardless of AI suggestion."""
    decision = agent._determine_decision("recommend_approve", [], 0.3)
    assert decision == ReviewDecision.ESCALATE

def test_high_confidence_auto_approve(agent):
    """High confidence with no flags should auto-approve."""
    decision = agent._determine_decision("auto_approve", [], 0.97)
    assert decision == ReviewDecision.AUTO_APPROVE

def test_below_threshold_degrades_to_recommend(agent):
    """Auto-approve below threshold should degrade."""
    decision = agent._determine_decision("auto_approve", [], 0.92)
    assert decision == ReviewDecision.RECOMMEND_APPROVE
