"""Qualitative classifications. Confidence is never a percentage."""

from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceStatus(str, Enum):
    CONFIRMED = "confirmed"
    POTENTIAL = "potential"
    UNKNOWN = "unknown"


class Recommendation(str, Enum):
    NOT_RECOMMENDED = "not_recommended"
    REQUIRES_INVESTIGATION = "requires_investigation"
    ELIGIBLE_FOR_REVIEW = "eligible_for_review"


RECOMMENDATION_LABELS = {
    Recommendation.NOT_RECOMMENDED: "Not recommended for deployment",
    Recommendation.REQUIRES_INVESTIGATION: "Requires further investigation",
    Recommendation.ELIGIBLE_FOR_REVIEW: "Eligible for review after testing",
}
