from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional

class QCAction(str, Enum):
    PASS = "PASS"
    RETRY = "RETRY"
    MANUAL_REVIEW = "MANUAL_REVIEW"

class DefectSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

@dataclass
class QCDefect:
    category: str  # e.g., "blur", "garment_fidelity", "anatomy", "face_identity", "exposure"
    severity: DefectSeverity
    description: str
    suggested_fix: Optional[str] = None

@dataclass
class Tier1Result:
    passed: bool
    score: float  # 0.0 to 1.0
    width: int = 0
    height: int = 0
    aspect_ratio_str: str = ""
    aspect_ratio_diff: float = 0.0
    full_laplacian_sharpness: float = 0.0
    face_laplacian_sharpness: Optional[float] = None
    garment_laplacian_sharpness: Optional[float] = None
    highlight_clipping_pct: float = 0.0
    shadow_clipping_pct: float = 0.0
    contrast_std: float = 0.0
    defects: List[QCDefect] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Tier2Result:
    passed: bool
    score: float  # 0.0 to 1.0
    # Face evaluation
    face_detected_count: int = 0
    face_identity_score: Optional[float] = None  # None if no person reference
    face_status: str = "PASS"  # PASS, FAIL, SKIPPED_NO_REFERENCE
    # Garment evaluation
    garment_color_similarity: float = 0.0  # HSV histogram correlation
    garment_texture_similarity: float = 0.0
    garment_fidelity_score: float = 0.0
    garment_status: str = "PASS"
    # Anatomy heuristics
    anatomy_score: float = 1.0
    anatomy_status: str = "PASS"
    defects: List[QCDefect] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Tier3Result:
    passed: bool
    score: float  # 0.0 to 1.0
    garment_silhouette_preserved: bool = True
    garment_details_fidelity: bool = True
    anatomy_integrity: bool = True
    skin_micro_texture_natural: bool = True
    scene_spatial_realism: bool = True
    defects: List[QCDefect] = field(default_factory=list)
    targeted_repair_instructions: List[str] = field(default_factory=list)
    negative_prompt_additions: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AggregatedQCReportV2:
    action: QCAction  # PASS, RETRY, MANUAL_REVIEW
    overall_passed: bool
    overall_score: float  # 0.0 to 1.0
    tier1: Tier1Result
    tier2: Optional[Tier2Result] = None
    tier3: Optional[Tier3Result] = None
    all_defects: List[QCDefect] = field(default_factory=list)
    negative_prompt_additions: List[str] = field(default_factory=list)
    targeted_repair_prompt: Optional[str] = None
    summary_reason: str = ""
    attempt_number: int = 1
