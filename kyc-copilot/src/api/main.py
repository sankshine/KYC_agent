"""
KYC Copilot - FastAPI Backend
REST API for pre-submission document validation.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import tempfile, os, uuid, logging
from datetime import datetime
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from validators.document_validator import KYCValidator, DocumentType, ValidationResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="KYC Copilot API",
    description="AI-powered pre-submission document validation for KYC compliance",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

validator = KYCValidator()
validation_history: dict = {}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "KYC Copilot API", "timestamp": datetime.utcnow().isoformat()}


@app.post("/validate")
async def validate_document(
    document_type: str,
    file: UploadFile = File(...),
    full_name: str = "",
    date_of_birth: str = "",
    address: str = "",
    city: str = "",
    province: str = "",
    postal_code: str = "",
    country: str = "Canada",
    email: str = ""
):
    """Validate a KYC document before submission."""
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid document_type.")

    allowed_types = ["image/jpeg", "image/png", "image/jpg", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"File type not supported.")

    user_profile = {
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "address": f"{address}, {city}, {province} {postal_code}, {country}",
        "email": email
    }

    suffix = "." + (file.filename or "doc.jpg").split(".")[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result: ValidationResult = validator.validate(tmp_path, doc_type, user_profile)
        validation_id = str(uuid.uuid4())
        response_data = {
            "validation_id": validation_id,
            "document_type": result.document_type.value,
            "passed": result.passed,
            "issues": [
                {"code": i.code, "severity": i.severity, "field": i.field,
                 "description": i.description, "suggestion": i.suggestion}
                for i in result.issues
            ],
            "extracted_fields": result.extracted_fields,
            "confidence_score": result.confidence_score,
            "timestamp": datetime.utcnow().isoformat(),
            "user_report": validator.format_user_report(result)
        }
        validation_history[validation_id] = response_data
        logger.info(f"Validation {validation_id}: {'PASSED' if result.passed else 'FAILED'}")
        return JSONResponse(content=response_data)
    finally:
        os.unlink(tmp_path)


@app.get("/validation/{validation_id}")
async def get_validation(validation_id: str):
    if validation_id not in validation_history:
        raise HTTPException(status_code=404, detail="Not found")
    return validation_history[validation_id]


@app.get("/analytics/summary")
async def get_summary():
    total = len(validation_history)
    passed = sum(1 for v in validation_history.values() if v["passed"])
    issue_counts: dict = {}
    for v in validation_history.values():
        for issue in v.get("issues", []):
            code = issue["code"]
            issue_counts[code] = issue_counts.get(code, 0) + 1
    return {
        "total_validations": total,
        "passed": passed,
        "failed": total - passed,
        "most_common_issues": sorted(issue_counts, key=lambda k: -issue_counts[k])[:5]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
