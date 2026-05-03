from pydantic import BaseModel, Field
from typing import Any, Dict, List


class RecordMemoryRequest(BaseModel):
    user_id: str = Field(..., description="Tenant or User ID for isolation")
    fact: str = Field(..., description="The observation to record")


class RecallMemoryRequest(BaseModel):
    user_id: str = Field(..., description="Tenant or User ID for isolation")
    query: str = Field(..., description="The temporal or semantic question")


class ContextResponseSchema(BaseModel):
    context_data: List[str]
    is_degraded: bool
    retrieval_method: str


class DeleteMemoryRequest(BaseModel):
    data_id: str = Field(..., description="UUID of the data item to delete")
    dataset_id: str = Field(..., description="UUID of the dataset containing the data")
    mode: str = Field("soft", description="Deletion mode: 'soft' or 'hard'")


class DataInventoryResponse(BaseModel):
    datasets: List[Any] = Field(default_factory=list, description="List of datasets and data items")


class DeleteMemoryResponse(BaseModel):
    status: str
    details: Dict[str, Any] = {}


class PruneMemoryResponse(BaseModel):
    status: str
    message: str


class CognifyStatusResponse(BaseModel):
    status: str
    details: Dict[str, Any] = {}


class DatasetDeleteResponse(BaseModel):
    dataset_id: str
    deleted: int = Field(..., description="Number of data items successfully deleted")
    failed: int = Field(0, description="Number of items that could not be deleted")


class FeedbackRequest(BaseModel):
    qa_id: str = Field(..., description="QA entry ID from a previous recall result")
    session_id: str = Field(..., description="Session ID that produced the recall")
    user_id: str = Field(..., description="User submitting the rating")
    feedback_score: int = Field(..., ge=1, le=5, description="Quality rating 1 (wrong) – 5 (perfect)")
    feedback_text: str | None = Field(None, description="Optional free-text correction or note")


class FeedbackResponse(BaseModel):
    status: str
    qa_id: str
    feedback_score: int


class TraceRequest(BaseModel):
    origin_function: str = Field(..., description="Tool or function name that was called")
    session_id: str = Field(..., description="Current MCP session ID")
    user_id: str = Field(..., description="User performing the action")
    status: str = Field("success", description="success or error")
    memory_query: str = Field("", description="Query sent to memory, if any")
    method_params: Dict[str, Any] = Field(default_factory=dict, description="Tool arguments to store")
    error_message: str = Field("", description="Error detail if status=error")


class TraceResponse(BaseModel):
    status: str
    trace_id: str | None = None
