"""KYC Review Agent - FastAPI Backend for Compliance Agent Dashboard."""

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import tempfile, os, uuid, logging
from datetime import datetime
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src.agents.review_agent import KYCReviewAgent, ReviewDecision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="KYC Review Agent API", description="AI-powered compliance review copilot", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

agent = KYCReviewAgent()
review_queue: dict = {}


@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/review")
async def review_submission(
    background_tasks: BackgroundTasks,
    document_type: str,
    file: UploadFile = File(...),
    full_name: str = "",
    date_of_birth: str = "",
    address: str = "",
    city: str = "",
    province: str = "",
    postal_code: str = "",
    country: str = "Canada",
    account_type: str = "margin",
    previous_attempts: int = 0,
    previous_rejection_reasons: str = ""
):
    """Submit a KYC document for AI compliance review."""
    submission_id = str(uuid.uuid4())

    user_profile = {
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "address": f"{address}, {city}, {province} {postal_code}, {country}",
    }
    context = {
        "account_type": account_type,
        "previous_attempts": previous_attempts,
        "previous_rejections": previous_rejection_reasons.split(",") if previous_rejection_reasons else []
    }

    suffix = "." + (file.filename or "doc.jpg").split(".")[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        packet = agent.review_document(tmp_path, document_type, user_profile, context, submission_id)
        entry = agent.to_dashboard_entry(packet)

        result = {
            "submission_id": submission_id,
            "dashboard_entry": entry,
            "flags": [{"issue_id": f.issue_id, "category": f.category, "description": f.description,
                       "confidence": f.confidence, "evidence": f.evidence, "regulatory_ref": f.regulatory_ref}
                      for f in packet.flags],
            "draft_rejection_email": packet.draft_rejection_email,
            "draft_approval_note": packet.draft_approval_note,
            "extracted_data": packet.extracted_data,
            "requires_human_review": packet.requires_human_review,
            "human_review_reason": packet.human_review_reason,
            "processing_time_seconds": packet.processing_time_seconds,
        }

        review_queue[submission_id] = result
        logger.info(f"Review {submission_id}: {packet.decision.value} ({packet.overall_confidence:.0%} confidence)")
        return JSONResponse(content=result)
    finally:
        os.unlink(tmp_path)


@app.get("/queue")
async def get_queue(status_filter: str = "all"):
    """Get the compliance review queue."""
    entries = list(review_queue.values())
    if status_filter != "all":
        entries = [e for e in entries if e["dashboard_entry"]["ai_decision"] == status_filter]
    return {
        "total": len(entries),
        "entries": sorted(entries, key=lambda x: x["dashboard_entry"]["timestamp"], reverse=True)
    }


@app.get("/queue/{submission_id}")
async def get_submission(submission_id: str):
    if submission_id not in review_queue:
        raise HTTPException(status_code=404, detail="Submission not found")
    return review_queue[submission_id]


@app.post("/queue/{submission_id}/decision")
async def agent_decision(submission_id: str, decision: str, agent_note: str = "", agent_id: str = ""):
    """Record human agent's final decision on a submission."""
    if submission_id not in review_queue:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    review_queue[submission_id]["human_decision"] = {
        "decision": decision,
        "agent_id": agent_id,
        "note": agent_note,
        "timestamp": datetime.utcnow().isoformat()
    }
    return {"status": "recorded", "submission_id": submission_id}


@app.get("/analytics")
async def get_analytics():
    """Compliance team productivity analytics."""
    total = len(review_queue)
    decisions = {}
    total_time = 0.0
    for r in review_queue.values():
        d = r["dashboard_entry"]["ai_decision"]
        decisions[d] = decisions.get(d, 0) + 1
        total_time += r.get("processing_time_seconds", 0)

    auto_approved = decisions.get("auto_approve", 0)
    avg_ai_time = total_time / total if total else 0

    return {
        "total_submissions": total,
        "decision_breakdown": decisions,
        "auto_approved_pct": f"{auto_approved/total*100:.1f}%" if total else "0%",
        "avg_ai_review_seconds": round(avg_ai_time, 2),
        "estimated_human_hours_saved": round(total * 12 / 60, 1),  # 12 min avg manual review
        "throughput_multiplier": "10x vs manual review"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=True)
