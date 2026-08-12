"""Cross-source opportunity identity without loss of listing provenance."""

from internship_monitor.opportunities.grouping import OpportunityGrouper
from internship_monitor.opportunities.models import MatchConfidence, OpportunityGroup

__all__ = ["MatchConfidence", "OpportunityGroup", "OpportunityGrouper"]
