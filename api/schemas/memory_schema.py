from pydantic import BaseModel, Field
from typing import List


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
