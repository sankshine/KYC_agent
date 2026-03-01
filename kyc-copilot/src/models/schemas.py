"""
Pydantic Schemas for KYC Copilot
Data models for API requests, responses, and internal state
"""

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import date


class DocumentType(str, Enum):
    PHOTO_ID = "PHOTO_ID"
    W8BEN = "W8BEN"
    FINANCIAL_DOC = "FINANCIAL_DOC"
    PROOF_OF_ADDRESS = "PROOF_OF_ADDRESS"


class IssueSeverity(str, Enum):
    CRITICAL = "CRITICAL"   # Will definitely cause rejection
    HIGH = "HIGH"           # Very likely to cause rejection
    MEDIUM = "MEDIUM"       # May cause rejection
    LOW = "LOW"             # Minor issue, unlikely to cause rejection
    INFO = "INFO"           # Informational only


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ValidationIssue(BaseModel):
    """Represents a single validation problem found in the document."""
    check_name: str = Field(..., description="Internal name of the check that failed")
    severity: IssueSeverity
    message: str = Field(..., description="Human-readable description of the issue")
    field: Optional[str] = Field(None, description="Specific field or area of the document affected")
    suggestion: Optional[str] = Field(None, description="How to fix this issue")
    technical_detail: Optional[Dict[str, Any]] = Field(None, description="Technical debug info")
    
    class Config:
        use_enum_values = True


class ValidationRequest(BaseModel):
    """Internal request passed between agents."""
    validation_id: str
    user_id: Optional[str] = None
    document_type: DocumentType
    file_content: bytes
    file_name: str
    content_type: str
    s3_url: Optional[str] = None
    
    class Config:
        arbitrary_types_allowed = True


class ValidationResult(BaseModel):
    """Internal result from the orchestrator."""
    validation_id: str
    overall_score: float = Field(..., ge=0, le=100)
    issues: List[ValidationIssue]
    suggestions: List[str]
    checks_performed: Dict[str, bool]


class ValidationResponse(BaseModel):
    """API response returned to the client."""
    validation_id: str
    status: ValidationStatus
    overall_score: float = Field(..., ge=0, le=100, description="0-100 readiness score")
    is_ready_to_submit: bool = Field(..., description="True if score >= 85")
    issues: List[ValidationIssue]
    suggestions: List[str]
    checks_performed: Dict[str, bool]
    processing_time_seconds: float
    
    class Config:
        use_enum_values = True


class UserProfile(BaseModel):
    """User profile data fetched from database for cross-referencing."""
    user_id: str
    full_name: str
    date_of_birth: Optional[date] = None
    address: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    country_of_residence: Optional[str] = None
    nationality: Optional[str] = None
    account_created_at: Optional[str] = None
