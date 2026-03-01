"""
KYC Orchestrator - LangGraph Multi-Agent Coordination
Coordinates all validation agents and aggregates results
"""

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from typing import TypedDict, List, Optional, Annotated
import asyncio
import operator

from src.agents.quality_agent import DocumentQualityAgent
from src.agents.crossref_agent import CrossRefAgent
from src.agents.form_agent import FormCompletenessAgent
from src.models.schemas import (
    ValidationRequest,
    ValidationResult,
    ValidationIssue,
    IssueSeverity,
    DocumentType
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class ValidationState(TypedDict):
    """Shared state across all validation agents."""
    request: ValidationRequest
    quality_issues: Annotated[List[ValidationIssue], operator.add]
    crossref_issues: Annotated[List[ValidationIssue], operator.add]
    form_issues: Annotated[List[ValidationIssue], operator.add]
    overall_score: float
    is_complete: bool
    error: Optional[str]


class KYCOrchestrator:
    """
    LangGraph-based orchestrator for multi-agent KYC document validation.
    
    Agents run in parallel where possible:
    - Quality Agent: Always runs first (fast, determines if doc is readable)
    - CrossRef Agent: Runs if quality passes
    - Form Agent: Runs in parallel with CrossRef
    
    Graph flow:
    START → quality_check → [crossref_check, form_check] (parallel) → aggregate → END
    """
    
    def __init__(self):
        self.quality_agent = DocumentQualityAgent()
        self.crossref_agent = CrossRefAgent()
        self.form_agent = FormCompletenessAgent()
        self.graph = self._build_graph()
    
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph validation pipeline."""
        workflow = StateGraph(ValidationState)
        
        # Add nodes
        workflow.add_node("quality_check", self._run_quality_check)
        workflow.add_node("parallel_checks", self._run_parallel_checks)
        workflow.add_node("aggregate_results", self._aggregate_results)
        
        # Define edges
        workflow.set_entry_point("quality_check")
        
        # After quality check: if critical failure, skip to aggregate; else run parallel
        workflow.add_conditional_edges(
            "quality_check",
            self._should_continue_after_quality,
            {
                "continue": "parallel_checks",
                "abort": "aggregate_results"
            }
        )
        
        workflow.add_edge("parallel_checks", "aggregate_results")
        workflow.add_edge("aggregate_results", END)
        
        return workflow.compile()
    
    def _should_continue_after_quality(self, state: ValidationState) -> str:
        """
        If document has critical quality issues (e.g., completely unreadable),
        skip further processing to save API costs.
        """
        critical_issues = [
            issue for issue in state["quality_issues"]
            if issue.severity == IssueSeverity.CRITICAL
        ]
        if critical_issues:
            logger.info("Critical quality issues found — skipping further checks")
            return "abort"
        return "continue"
    
    async def _run_quality_check(self, state: ValidationState) -> dict:
        """Run document quality validation agent."""
        logger.info(f"Running quality check for {state['request'].validation_id}")
        try:
            issues = await self.quality_agent.validate(state["request"])
            return {"quality_issues": issues}
        except Exception as e:
            logger.error(f"Quality agent failed: {e}")
            return {"quality_issues": [], "error": str(e)}
    
    async def _run_parallel_checks(self, state: ValidationState) -> dict:
        """Run cross-reference and form checks in parallel."""
        logger.info(f"Running parallel checks for {state['request'].validation_id}")
        try:
            crossref_task = self.crossref_agent.validate(state["request"])
            form_task = self.form_agent.validate(state["request"])
            
            crossref_issues, form_issues = await asyncio.gather(
                crossref_task, form_task, return_exceptions=True
            )
            
            if isinstance(crossref_issues, Exception):
                logger.error(f"CrossRef agent failed: {crossref_issues}")
                crossref_issues = []
            
            if isinstance(form_issues, Exception):
                logger.error(f"Form agent failed: {form_issues}")
                form_issues = []
            
            return {
                "crossref_issues": crossref_issues,
                "form_issues": form_issues
            }
        except Exception as e:
            logger.error(f"Parallel checks failed: {e}")
            return {"crossref_issues": [], "form_issues": [], "error": str(e)}
    
    async def _aggregate_results(self, state: ValidationState) -> dict:
        """Aggregate all agent results into a final validation score."""
        all_issues = (
            state.get("quality_issues", []) +
            state.get("crossref_issues", []) +
            state.get("form_issues", [])
        )
        
        # Scoring: start at 100, deduct per issue
        score = 100.0
        deductions = {
            IssueSeverity.CRITICAL: 40,
            IssueSeverity.HIGH: 20,
            IssueSeverity.MEDIUM: 10,
            IssueSeverity.LOW: 5,
            IssueSeverity.INFO: 0
        }
        
        for issue in all_issues:
            score -= deductions.get(issue.severity, 0)
        
        score = max(0.0, min(100.0, score))
        
        return {"overall_score": score, "is_complete": True}
    
    async def validate(self, request: ValidationRequest) -> ValidationResult:
        """
        Main entry point for document validation.
        Returns a complete ValidationResult with all issues and score.
        """
        initial_state = ValidationState(
            request=request,
            quality_issues=[],
            crossref_issues=[],
            form_issues=[],
            overall_score=0.0,
            is_complete=False,
            error=None
        )
        
        final_state = await self.graph.ainvoke(initial_state)
        
        all_issues = (
            final_state.get("quality_issues", []) +
            final_state.get("crossref_issues", []) +
            final_state.get("form_issues", [])
        )
        
        suggestions = self._generate_suggestions(all_issues, request.document_type)
        
        checks = {
            "quality_checks": len(final_state.get("quality_issues", [])) >= 0,
            "crossref_checks": len(final_state.get("crossref_issues", [])) >= 0,
            "form_checks": len(final_state.get("form_issues", [])) >= 0
        }
        
        return ValidationResult(
            validation_id=request.validation_id,
            overall_score=final_state.get("overall_score", 0),
            issues=all_issues,
            suggestions=suggestions,
            checks_performed=checks
        )
    
    def _generate_suggestions(
        self, 
        issues: List[ValidationIssue], 
        document_type: DocumentType
    ) -> List[str]:
        """Generate human-readable suggestions based on found issues."""
        suggestions = []
        
        issue_types = {issue.check_name for issue in issues}
        
        if "blur_check" in issue_types:
            suggestions.append(
                "📷 Retake the photo in good lighting. Place document on a flat, dark surface "
                "and hold your phone steady. Ensure all text is sharp and readable."
            )
        
        if "resolution_check" in issue_types:
            suggestions.append(
                "🔍 Use a higher resolution camera or scan the document at 300 DPI or higher."
            )
        
        if "dob_mismatch" in issue_types:
            suggestions.append(
                "📅 Your date of birth on this document doesn't match your profile. "
                "Either update your Questrade profile DOB or submit an ID with the matching date."
            )
        
        if "address_mismatch" in issue_types:
            suggestions.append(
                "🏠 The address in Section 3 of your W-8BEN doesn't match your application. "
                "Update Section 3 to match your address on file, or update your profile address first."
            )
        
        if "missing_country" in issue_types:
            suggestions.append(
                "🌍 Part II Section 9 of your W-8BEN is missing your country of residence. "
                "Fill in 'Canada' (or your current country) in Section 9 before resubmitting."
            )
        
        if "truncated_account" in issue_types:
            suggestions.append(
                "💳 Your bank account number appears truncated in the document. "
                "Upload a statement or void cheque where the full account number is visible."
            )
        
        if not suggestions:
            suggestions.append("✅ No issues detected. Your document looks ready to submit!")
        
        return suggestions
