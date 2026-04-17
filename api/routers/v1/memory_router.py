from fastapi import APIRouter, Depends, Query, status

from api.schemas.memory_schema import (
    RecordMemoryRequest,
    RecallMemoryRequest,
    ContextResponseSchema,
    DeleteMemoryRequest,
    DataInventoryResponse,
    DeleteMemoryResponse,
    PruneMemoryResponse,
    CognifyStatusResponse,
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


@router.get("/data", response_model=DataInventoryResponse)
async def list_data(
    dataset_id: str | None = Query(None, description="Optional dataset UUID to filter by"),
    service: MemoryService = Depends(get_memory_service),
):
    """List all datasets and data items in the Cognee knowledge graph."""
    try:
        inventory = await service.get_data_inventory(dataset_id=dataset_id)
        return DataInventoryResponse(datasets=inventory.datasets)
    except MemoryServiceUnavailable as e:
        raise MemoryServiceError(detail=str(e))


@router.delete("/data", response_model=DeleteMemoryResponse)
async def delete_data(
    request: DeleteMemoryRequest,
    service: MemoryService = Depends(get_memory_service),
):
    """Delete a specific data item by UUID from a dataset."""
    try:
        result = await service.delete_memory_data(
            data_id=request.data_id,
            dataset_id=request.dataset_id,
            mode=request.mode,
        )
        return DeleteMemoryResponse(status=result.status, details=result.details)
    except MemoryServiceUnavailable as e:
        raise MemoryServiceError(detail=str(e))


@router.delete("/prune", response_model=PruneMemoryResponse)
async def prune_memory(
    service: MemoryService = Depends(get_memory_service),
):
    """Permanently wipe ALL data from the Cognee knowledge graph. USE WITH CAUTION."""
    try:
        result = await service.prune_all_memory()
        return PruneMemoryResponse(status=result.status, message=result.message)
    except MemoryServiceUnavailable as e:
        raise MemoryServiceError(detail=str(e))


@router.get("/cognify/status", response_model=CognifyStatusResponse)
async def cognify_status(
    service: MemoryService = Depends(get_memory_service),
):
    """Check the current status of the cognify pipeline."""
    try:
        result = await service.get_cognify_status()
        return CognifyStatusResponse(status=result.status, details=result.details)
    except MemoryServiceUnavailable as e:
        raise MemoryServiceError(detail=str(e))
