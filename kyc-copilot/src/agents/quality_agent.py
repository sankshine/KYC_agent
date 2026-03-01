"""
Document Quality Agent
Checks image quality: blur, resolution, cropping, legibility
Uses OpenCV + custom thresholds calibrated for KYC documents
"""

import cv2
import numpy as np
from PIL import Image
import io
from typing import List, Tuple
import base64

from openai import AsyncOpenAI
from src.models.schemas import (
    ValidationRequest,
    ValidationIssue,
    IssueSeverity,
    DocumentType
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Quality thresholds (calibrated through testing)
BLUR_THRESHOLD = 100.0          # Laplacian variance below this = blurry
MIN_RESOLUTION_WIDTH = 800      # Minimum width in pixels
MIN_RESOLUTION_HEIGHT = 500     # Minimum height in pixels
MIN_DPI = 150                   # Minimum DPI for scanned docs
BRIGHTNESS_MIN = 40             # Too dark
BRIGHTNESS_MAX = 220            # Too bright/washed out
MIN_DOCUMENT_AREA_RATIO = 0.4   # Document should fill at least 40% of frame


class DocumentQualityAgent:
    """
    Agent responsible for assessing the physical quality of submitted documents.
    
    Checks performed:
    1. Blur detection (Laplacian variance method)
    2. Resolution check
    3. Brightness/contrast check
    4. Document boundary detection (is doc cropped/cut off?)
    5. GPT-4o Vision legibility check (catches complex quality issues)
    """
    
    def __init__(self):
        self.openai_client = AsyncOpenAI()
    
    async def validate(self, request: ValidationRequest) -> List[ValidationIssue]:
        """Run all quality checks and return list of issues found."""
        issues = []
        
        # Convert file content to OpenCV image
        img = self._bytes_to_cv2(request.file_content)
        pil_img = Image.open(io.BytesIO(request.file_content))
        
        if img is None:
            issues.append(ValidationIssue(
                check_name="file_read",
                severity=IssueSeverity.CRITICAL,
                message="Could not read document. File may be corrupted.",
                field=None,
                suggestion="Try uploading the file again or use a different format (JPG, PNG, PDF)."
            ))
            return issues
        
        # Run all quality checks
        issues.extend(self._check_blur(img))
        issues.extend(self._check_resolution(img))
        issues.extend(self._check_brightness(img))
        issues.extend(self._check_document_boundaries(img))
        
        # GPT-4o Vision check for complex legibility (runs async)
        vision_issues = await self._check_with_vision(request.file_content, request.document_type)
        issues.extend(vision_issues)
        
        logger.info(f"Quality check complete: {len(issues)} issues found")
        return issues
    
    def _bytes_to_cv2(self, file_bytes: bytes):
        """Convert file bytes to OpenCV image array."""
        try:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            return img
        except Exception as e:
            logger.error(f"Failed to decode image: {e}")
            return None
    
    def _check_blur(self, img: np.ndarray) -> List[ValidationIssue]:
        """
        Detect image blur using Laplacian variance.
        
        High variance = sharp image
        Low variance = blurry image
        
        This is the most reliable single metric for KYC document blur detection.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        
        logger.debug(f"Blur score (Laplacian variance): {laplacian_var:.2f}")
        
        if laplacian_var < 50:
            return [ValidationIssue(
                check_name="blur_check",
                severity=IssueSeverity.CRITICAL,
                message=f"Document is too blurry (sharpness score: {laplacian_var:.0f}/100). "
                        f"Text may not be legible by reviewers.",
                field="image_quality",
                suggestion="Retake the photo: use good lighting, place document flat, hold phone steady, "
                           "and tap to focus on the document text before taking the photo.",
                technical_detail={"laplacian_variance": round(laplacian_var, 2), "threshold": BLUR_THRESHOLD}
            )]
        elif laplacian_var < BLUR_THRESHOLD:
            return [ValidationIssue(
                check_name="blur_check",
                severity=IssueSeverity.HIGH,
                message=f"Document image may be slightly blurry (sharpness score: {laplacian_var:.0f}/100). "
                        f"Consider retaking for clearer results.",
                field="image_quality",
                suggestion="For best results, ensure the document is in sharp focus. "
                           "Tap the screen on the document text when taking the photo.",
                technical_detail={"laplacian_variance": round(laplacian_var, 2), "threshold": BLUR_THRESHOLD}
            )]
        
        return []
    
    def _check_resolution(self, img: np.ndarray) -> List[ValidationIssue]:
        """Check if image resolution is sufficient for OCR and review."""
        height, width = img.shape[:2]
        
        logger.debug(f"Image resolution: {width}x{height}")
        
        if width < MIN_RESOLUTION_WIDTH or height < MIN_RESOLUTION_HEIGHT:
            return [ValidationIssue(
                check_name="resolution_check",
                severity=IssueSeverity.HIGH,
                message=f"Image resolution too low ({width}x{height}px). "
                        f"Minimum required: {MIN_RESOLUTION_WIDTH}x{MIN_RESOLUTION_HEIGHT}px.",
                field="image_quality",
                suggestion="Take the photo with your phone's main camera (not front camera). "
                           "Ensure you're not using a compressed or thumbnail version of the image.",
                technical_detail={"width": width, "height": height}
            )]
        
        return []
    
    def _check_brightness(self, img: np.ndarray) -> List[ValidationIssue]:
        """Check for over/under exposure."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mean_brightness = gray.mean()
        
        logger.debug(f"Mean brightness: {mean_brightness:.2f}")
        
        if mean_brightness < BRIGHTNESS_MIN:
            return [ValidationIssue(
                check_name="brightness_check",
                severity=IssueSeverity.MEDIUM,
                message="Document image is too dark. Text may not be readable.",
                field="image_quality",
                suggestion="Take the photo in a well-lit area. Avoid shadows on the document.",
                technical_detail={"mean_brightness": round(mean_brightness, 2)}
            )]
        elif mean_brightness > BRIGHTNESS_MAX:
            return [ValidationIssue(
                check_name="brightness_check",
                severity=IssueSeverity.MEDIUM,
                message="Document image is overexposed (too bright). Text may be washed out.",
                field="image_quality",
                suggestion="Avoid direct sunlight or flash. Try indirect or ambient lighting.",
                technical_detail={"mean_brightness": round(mean_brightness, 2)}
            )]
        
        return []
    
    def _check_document_boundaries(self, img: np.ndarray) -> List[ValidationIssue]:
        """
        Detect if document edges are cut off (cropped incorrectly).
        Uses edge detection to find document rectangle.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 75, 200)
        
        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return []
        
        # Find the largest contour (should be the document)
        largest_contour = max(contours, key=cv2.contourArea)
        doc_area = cv2.contourArea(largest_contour)
        frame_area = img.shape[0] * img.shape[1]
        
        area_ratio = doc_area / frame_area if frame_area > 0 else 0
        
        logger.debug(f"Document area ratio: {area_ratio:.2f}")
        
        # Check if document is too small in frame (too far away)
        if area_ratio < MIN_DOCUMENT_AREA_RATIO:
            return [ValidationIssue(
                check_name="document_boundary_check",
                severity=IssueSeverity.MEDIUM,
                message="Document appears too small in the frame or may be partially cut off.",
                field="image_framing",
                suggestion="Position the document so it fills most of the frame. "
                           "All four corners of the document should be visible.",
                technical_detail={"area_ratio": round(area_ratio, 2)}
            )]
        
        return []
    
    async def _check_with_vision(
        self, 
        file_content: bytes, 
        document_type: DocumentType
    ) -> List[ValidationIssue]:
        """
        Use GPT-4o Vision for complex quality checks that OpenCV misses:
        - Finger/hand obstructing part of document
        - Glare hotspots on specific text areas
        - Document placed at extreme angle
        - Photocopy of photocopy (multi-generation copy)
        """
        try:
            base64_image = base64.b64encode(file_content).decode("utf-8")
            
            doc_type_context = {
                DocumentType.PHOTO_ID: "government-issued photo ID (passport, driver's license)",
                DocumentType.W8BEN: "W-8BEN tax form",
                DocumentType.FINANCIAL_DOC: "bank statement or financial document"
            }.get(document_type, "identity document")
            
            prompt = f"""You are a KYC compliance quality checker. Analyze this {doc_type_context} image for quality issues.

Check ONLY for these specific problems:
1. Is any text obscured by a finger, hand, or other object?
2. Is there glare or reflection covering important text areas?
3. Is the document at an angle greater than 15 degrees (making text hard to read)?
4. Does this appear to be a photocopy of a photocopy (very low quality)?
5. Are there any visible security features (holograms, watermarks) that look tampered with?

Respond in JSON format:
{{
  "issues": [
    {{
      "type": "issue_type",
      "severity": "CRITICAL|HIGH|MEDIUM|LOW",
      "description": "specific description",
      "suggestion": "how to fix it"
    }}
  ]
}}

If no issues found, return: {{"issues": []}}
Be concise. Only flag real, clear problems."""

            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                            },
                            {"type": "text", "text": prompt}
                        ]
                    }
                ],
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            issues = []
            
            severity_map = {
                "CRITICAL": IssueSeverity.CRITICAL,
                "HIGH": IssueSeverity.HIGH,
                "MEDIUM": IssueSeverity.MEDIUM,
                "LOW": IssueSeverity.LOW
            }
            
            for issue_data in result.get("issues", []):
                issues.append(ValidationIssue(
                    check_name=f"vision_{issue_data.get('type', 'quality')}",
                    severity=severity_map.get(issue_data.get("severity", "MEDIUM"), IssueSeverity.MEDIUM),
                    message=issue_data.get("description", "Quality issue detected"),
                    field="image_quality",
                    suggestion=issue_data.get("suggestion", "Please retake the photo.")
                ))
            
            return issues
            
        except Exception as e:
            logger.warning(f"Vision check failed (non-critical): {e}")
            return []  # Don't fail validation if vision check fails
