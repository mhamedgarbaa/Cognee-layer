from pydantic import BaseModel
from typing import List


class ContextEnvelope(BaseModel):
    """Domain model representing fused context returned to the BPI agent."""
    context_data: List[str]
    is_degraded: bool
    retrieval_method: str
