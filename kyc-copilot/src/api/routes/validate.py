"""
Validation Routes - Core KYC document validation endpoints
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks
from typing import Optional, List
import uuid
import time

from src.agents.orchestrator import KYCOrchestrator
from src.models.schemas import (
    ValidationRequest,
    ValidationResponse,
    DocumentType,
    ValidationStatus
)
from src.api.middleware.auth import verify_token
from src.utils.s3_client import upload_to_s3
from src.utils.logger import setup_logger

logger = setup_logger(__name__)
router = APIRouter()
orchestrator = KYCOrchestrator()


@router.post("/document", response_model=ValidationResponse)
async def validate_document(
    file: UploadFile = File(...),
    document_type: DocumentType = DocumentType.PHOTO_ID,
    user_id: str = None,
    session_id: Optional[str] = None,
    token: str = Depends(verify_token)
):
    """
    Validate a KYC document before submission.
    
    - **file**: The document image/PDF to validate
    - **document_type**: Type of document (PHOTO_ID, W8BEN, FINANCIAL_DOC)
    - **user_id**: User's profile ID for cross-referencing
    - **session_id**: Optional session ID for tracking
    
    Returns a detailed validation report with issues and suggestions.
    """
    start_time = time.time()
    validation_id = str(uuid.uuid4())
    
    logger.info(f"Starting validation {validation_id} for user {user_id}, doc type: {document_type}")
    
    # Read file content
    file_content = await file.read()
    
    if len(file_content) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10MB.")
    
    allowed_types = ["image/jpeg", "image/png", "image/webp", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {allowed_types}"
        )
    
    try:
        # Upload to S3 temporarily (encrypted, auto-deleted after 24h)
        s3_key = f"validations/{validation_id}/{file.filename}"
        s3_url = await upload_to_s3(file_content, s3_key, content_type=file.content_type)
        
        # Run AI validation pipeline
        validation_request = ValidationRequest(
            validation_id=validation_id,
            user_id=user_id,
            document_type=document_type,
            file_content=file_content,
            file_name=file.filename,
            content_type=file.content_type,
            s3_url=s3_url
        )
        
        result = await orchestrator.validate(validation_request)
        
        processing_time = time.time() - start_time
        logger.info(f"Validation {validation_id} completed in {processing_time:.2f}s. Score: {result.overall_score}")
        
        return ValidationResponse(
            validation_id=validation_id,
            status=ValidationStatus.COMPLETE,
            overall_score=result.overall_score,
            is_ready_to_submit=result.overall_score >= 85,
            issues=result.issues,
            suggestions=result.suggestions,
            checks_performed=result.checks_performed,
            processing_time_seconds=round(processing_time, 2)
        )
        
    except Exception as e:
        logger.error(f"Validation failed for {validation_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


@router.post("/batch", response_model=List[ValidationResponse])
async def validate_batch(
    files: List[UploadFile] = File(...),
    document_types: List[DocumentType] = None,
    user_id: str = None,
    token: str = Depends(verify_token)
):
    """Validate multiple documents in a single request (max 5)."""
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 files per batch request.")
    
    results = []
    for i, file in enumerate(files):
        doc_type = document_types[i] if document_types and i < len(document_types) else DocumentType.PHOTO_ID
        result = await validate_document(file=file, document_type=doc_type, user_id=user_id, token=token)
        results.append(result)
    
    return results


@router.get("/{validation_id}", response_model=ValidationResponse)
async def get_validation_result(
    validation_id: str,
    token: str = Depends(verify_token)
):
    """Retrieve a previous validation result by ID."""
    # In production, fetch from Redis cache or database
    raise HTTPException(status_code=404, detail="Validation result not found or expired.")
