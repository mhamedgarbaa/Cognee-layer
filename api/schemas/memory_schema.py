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
