"""DeepResearch's package surface: public class, model menu, and I/O types."""

from vibrantine.commissions.deep_research.commission import DeepResearchCommission
from vibrantine.commissions.deep_research.models import DeepResearchModelMenu
from vibrantine.commissions.deep_research.types import ResearchInput, ResearchOutput

__all__ = [
    "DeepResearchCommission",
    "DeepResearchModelMenu",
    "ResearchInput",
    "ResearchOutput",
]
