from typing import List, Dict, Any, Optional
from .models import (
    QCAction,
    DefectSeverity,
    QCDefect,
    Tier1Result,
    Tier2Result,
    Tier3Result,
    AggregatedQCReportV2
)

class QCDecisionEngine:
    """
    Tier 4 Decision Engine & Action Router.
    Aggregates Tier 1, Tier 2, and Tier 3 into deterministic action routes:
    - PASS: All gates met and weighted score >= pass_threshold
    - RETRY: Critical gate failed and retries remain
    - MANUAL_REVIEW: Borderline confidence or max retries exhausted
    """

    def __init__(
        self,
        pass_threshold: float = 0.75,
        borderline_threshold: float = 0.65,
        max_attempts: int = 3
    ):
        self.pass_threshold = pass_threshold
        self.borderline_threshold = borderline_threshold
        self.max_attempts = max_attempts

    def decide(
        self,
        tier1: Tier1Result,
        tier2: Optional[Tier2Result],
        tier3: Optional[Tier3Result],
        attempt_number: int = 1
    ) -> AggregatedQCReportV2:
        all_defects: List[QCDefect] = list(tier1.defects)
        negative_additions: List[str] = []
        repair_instructions: List[str] = []

        # Tier 1 defects & negative suggestions
        for d in tier1.defects:
            if d.suggested_fix:
                repair_instructions.append(d.suggested_fix)
            if d.category == "blur":
                negative_additions.append("blurry, motion blur, out-of-focus soft edges")
            elif d.category == "exposure":
                negative_additions.append("overexposed blown-out highlights, crushed dark shadows")

        # Tier 2 defects & negative suggestions
        if tier2:
            all_defects.extend(tier2.defects)
            for d in tier2.defects:
                if d.suggested_fix:
                    repair_instructions.append(d.suggested_fix)
                if d.category == "garment_color":
                    negative_additions.append("wrong garment color, altered fabric shade, wrong dye color")
                elif d.category == "garment_texture":
                    negative_additions.append("flat solid fabric without texture, missing garment pattern")

        # Tier 3 defects & negative suggestions
        if tier3:
            all_defects.extend(tier3.defects)
            negative_additions.extend(tier3.negative_prompt_additions)
            repair_instructions.extend(tier3.targeted_repair_instructions)

        # 1. Calculate Weighted Overall Score
        if not tier1.passed:
            overall_score = tier1.score
            overall_passed = False
        else:
            w1 = 0.20
            w2 = 0.35 if tier2 else 0.0
            w3 = 0.45 if tier3 else 0.0

            total_w = w1 + w2 + w3
            s1 = tier1.score
            s2 = tier2.score if tier2 else 1.0
            s3 = tier3.score if tier3 else 1.0

            overall_score = round((w1 * s1 + w2 * s2 + w3 * s3) / max(0.01, total_w), 3)

            # Check Hard-Failure Gates
            has_critical = any(d.severity == DefectSeverity.CRITICAL for d in all_defects)
            garment_failed = (tier2 is not None and tier2.garment_status == "FAIL" and (tier3 is None or not tier3.passed))
            tier3_failed = (tier3 is not None and not tier3.passed)

            hard_gates_passed = (not has_critical) and (not garment_failed) and (not tier3_failed)
            overall_passed = hard_gates_passed and (overall_score >= self.pass_threshold)

        # 2. Determine Recommended Action
        if overall_passed:
            action = QCAction.PASS
            summary_reason = f"All critical quality gates passed. Overall score: {overall_score:.2f}."
        elif attempt_number >= self.max_attempts:
            action = QCAction.MANUAL_REVIEW
            summary_reason = f"Max retries ({self.max_attempts}) reached. Routing to manual review. Final score: {overall_score:.2f}."
        else:
            action = QCAction.RETRY
            critical_names = [d.description for d in all_defects if d.severity in (DefectSeverity.CRITICAL, DefectSeverity.HIGH)]
            top_reason = critical_names[0] if critical_names else f"Quality score {overall_score:.2f} < {self.pass_threshold}"
            summary_reason = f"Refining quality ({top_reason}). Triggering self-healing retry {attempt_number + 1}."

        # 3. Build In-Place Targeted Repair Prompt
        unique_repairs = list(dict.fromkeys([r.strip() for r in repair_instructions if r and r.strip()]))
        repair_prompt = None
        if unique_repairs:
            directives = "; ".join(unique_repairs[:3])
            repair_prompt = (
                f"STRICT DEFECT FIX: {directives}. "
                "Keep the rest of the model pose, fabric silhouette, lighting, and background composition completely intact."
            )

        unique_negatives = list(dict.fromkeys([n.strip() for n in negative_additions if n and n.strip()]))

        return AggregatedQCReportV2(
            action=action,
            overall_passed=overall_passed,
            overall_score=overall_score,
            tier1=tier1,
            tier2=tier2,
            tier3=tier3,
            all_defects=all_defects,
            negative_prompt_additions=unique_negatives,
            targeted_repair_prompt=repair_prompt,
            summary_reason=summary_reason,
            attempt_number=attempt_number
        )
