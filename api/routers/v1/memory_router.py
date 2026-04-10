from fastapi import APIRouter, Depends, status

from api.schemas.memory_schema import (
    RecordMemoryRequest,
    RecallMemoryRequest,
    ContextResponseSchema,
)
from api.custom_api_exceptions import MemoryServiceError
from service.custom_service_exceptions import MemoryServiceUnavailable
from service.memory_service import MemoryService
from api.dependencies import get_memory_service

router = APIRouter(prefix="/memory", tags=["MEMORY"])


@router.post("/record", status_code=status.HTTP_202_ACCEPTED)
async def record_memory(
    request: RecordMemoryRequest,
    service: MemoryService = Depends(get_memory_service)
):
    """Logs a fact to Short-Term Memory and triggers Long-Term graph consolidation."""
    try:
        await service.record_agent_memory(request.user_id, request.fact)
        return {"status": "accepted", "message": "Memory recorded and processing."}
    except MemoryServiceUnavailable as e:
        raise MemoryServiceError(detail=str(e))


@router.post("/recall", response_model=ContextResponseSchema)
async def recall_memory(
    request: RecallMemoryRequest,
    service: MemoryService = Depends(get_memory_service)
):
    """Retrieves temporal context for the agent with graceful degradation."""
    try:
        context_envelope = await service.recall_context(request.user_id, request.query)
        # Map domain model to API schema
        return ContextResponseSchema(
            context_data=context_envelope.context_data,
            is_degraded=context_envelope.is_degraded,
            retrieval_method=context_envelope.retrieval_method
        )
    except MemoryServiceUnavailable as e:
        raise MemoryServiceError(detail=str(e))
