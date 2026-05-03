from pydantic import BaseModel
from typing import Any, Dict, List


class ContextEnvelope(BaseModel):
    """Domain model representing fused context returned to the BPI agent."""
    context_data: List[str]
    is_degraded: bool
    retrieval_method: str


class DataInventory(BaseModel):
    """Domain model wrapping list_data results from Cognee MCP."""
    datasets: List[Any]


class DeleteResult(BaseModel):
    """Domain model wrapping a delete operation result."""
    status: str
    details: Dict[str, Any] = {}


class PruneResult(BaseModel):
    """Domain model wrapping a prune (full reset) confirmation."""
    status: str
    message: str


class CognifyStatus(BaseModel):
    """Domain model wrapping the cognify pipeline status."""
    status: str
    details: Dict[str, Any] = {}


class FeedbackResult(BaseModel):
    """Domain model wrapping a feedback submission result."""
    status: str
    qa_id: str
    feedback_score: int


class TraceResult(BaseModel):
    """Domain model wrapping an agent trace recording result."""
    status: str
    trace_id: str | None = None
