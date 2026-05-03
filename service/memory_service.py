from data.repositories.cognee_repository import CogneeRepository
from data.custom_data_exceptions import CogneeConnectionError, CogneeToolError
from service.custom_service_exceptions import MemoryServiceUnavailable
from service.business_models import (
    ContextEnvelope,
    DataInventory,
    DeleteResult,
    PruneResult,
    CognifyStatus,
    FeedbackResult,
    TraceResult,
)
from configuration.logging_setup import logger
import asyncio
import re


class MemoryService:
    def __init__(self, repo: CogneeRepository):
        self.repo = repo

    @staticmethod
    def _is_non_critical_save_interaction_error(error_message: str) -> bool:
        """Detect known Cognee rule-generation schema issues after interaction save."""
        patterns = [
            r"validation error for RuleSet",
            r"Field required \[type=missing",
            r"Input should be a valid integer",
            r"InstructorRetryException",
            r"Failed to Save interaction: <failed_attempts>",
        ]
        return any(re.search(pattern, error_message, re.IGNORECASE) for pattern in patterns)

    async def record_agent_memory(self, user_id: str, fact: str) -> None:
        """Stores a fact and triggers background temporal consolidation."""
        try:
            # 1. Fast Append to STM (SQLite)
            try:
                await self.repo.save_interaction(user_id=user_id, data=fact)
            except CogneeToolError as e:
                if self._is_non_critical_save_interaction_error(str(e)):
                    logger.warning(
                        "Ignoring non-critical Cognee RuleSet validation error after save_interaction",
                        extra={"user_id": user_id, "error": str(e)},
                    )
                else:
                    raise

            # 2. Trigger LTM Consolidation (Neo4j/Kuzu Temporal Graph)
            await self.repo.trigger_cognify(user_id=user_id, data=fact)

            logger.info("Memory recorded and consolidation triggered", extra={"user_id": user_id})
        except CogneeConnectionError as e:
            logger.error(f"Failed to record memory: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during record: {e}")
            raise MemoryServiceUnavailable("Memory processing failed.")

    async def recall_context(self, user_id: str, query: str) -> ContextEnvelope:
        """Retrieves context with Graceful Degradation (Circuit Breaker)."""
        try:
            # Attempt Primary: Synthesized Temporal Graph Retrieval (Uses LLM)
            results = await asyncio.wait_for(
                self.repo.search(
                    user_id=user_id,
                    query=query,
                    query_type="GRAPH_COMPLETION"
                ),
                timeout=60,
            )
            return ContextEnvelope(
                context_data=[str(r) for r in results],
                is_degraded=False,
                retrieval_method="GRAPH_COMPLETION"
            )

        except asyncio.TimeoutError:
            logger.warning("GRAPH_COMPLETION timed out", extra={"user_id": user_id})
            return ContextEnvelope(
                context_data=[],
                is_degraded=True,
                retrieval_method="GRAPH_TIMEOUT"
            )

        except (CogneeConnectionError, CogneeToolError) as e:
            logger.warning(f"GRAPH_COMPLETION failed, falling back to CHUNKS. Error: {e}", extra={"user_id": user_id})

            try:
                # Attempt Fallback: Raw Semantic Search (Bypasses LLM bottleneck)
                fallback_results = await asyncio.wait_for(
                    self.repo.search(
                        user_id=user_id,
                        query=query,
                        query_type="CHUNKS"
                    ),
                    timeout=20,
                )
                return ContextEnvelope(
                    context_data=[str(r) for r in fallback_results],
                    is_degraded=True,
                    retrieval_method="CHUNKS_FALLBACK"
                )
            except Exception as critical_error:
                logger.error(f"Total memory subsystem failure: {critical_error}")
                # Final Fallback: Return empty context, agent uses immediate chat history
                return ContextEnvelope(
                    context_data=[],
                    is_degraded=True,
                    retrieval_method="NONE_FAILED"
                )

    # ──────────────────────────────────────────────
    # Management & Admin Operations
    # ──────────────────────────────────────────────

    async def get_data_inventory(self, dataset_id: str | None = None) -> DataInventory:
        """List all datasets and data items from the Cognee knowledge graph."""
        try:
            items = await self.repo.list_data(dataset_id=dataset_id)
            return DataInventory(datasets=items)
        except CogneeConnectionError as e:
            logger.error(f"Failed to list data: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during list_data: {e}")
            raise MemoryServiceUnavailable("Failed to retrieve data inventory.")

    async def delete_memory_data(self, data_id: str, dataset_id: str, mode: str = "soft") -> DeleteResult:
        """Delete a specific data item from a dataset."""
        try:
            result = await self.repo.delete_data(data_id=data_id, dataset_id=dataset_id, mode=mode)
            return DeleteResult(
                status="deleted",
                details=result if isinstance(result, dict) else {"raw": result},
            )
        except CogneeConnectionError as e:
            logger.error(f"Failed to delete data: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during delete: {e}")
            raise MemoryServiceUnavailable("Failed to delete data.")

    async def delete_dataset(self, dataset_id: str, mode: str = "soft") -> DeleteResult:
        """Delete all data items in a specific dataset."""
        try:
            result = await self.repo.delete_dataset(dataset_id=dataset_id, mode=mode)
            return DeleteResult(status="deleted", details=result)
        except CogneeConnectionError as e:
            logger.error(f"Failed to delete dataset: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during delete_dataset: {e}")
            raise MemoryServiceUnavailable("Failed to delete dataset.")

    async def prune_all_memory(self) -> PruneResult:
        """Permanently wipe ALL data from the Cognee knowledge graph."""
        try:
            result = await self.repo.prune()
            message = result.get("message", "All data has been pruned.") if isinstance(result, dict) else str(result)
            return PruneResult(status="pruned", message=message)
        except CogneeConnectionError as e:
            logger.error(f"Failed to prune: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during prune: {e}")
            raise MemoryServiceUnavailable("Failed to prune memory data.")

    # ──────────────────────────────────────────────
    # Feedback & Trace Operations
    # ──────────────────────────────────────────────

    async def submit_feedback(
        self,
        user_id: str,
        session_id: str,
        qa_id: str,
        feedback_score: int,
        feedback_text: str | None = None,
    ) -> FeedbackResult:
        """Submit quality feedback for a previous recall result."""
        try:
            result = await self.repo.submit_feedback(
                user_id=user_id,
                session_id=session_id,
                qa_id=qa_id,
                feedback_score=feedback_score,
                feedback_text=feedback_text,
            )
            return FeedbackResult(
                status=result.get("status", "accepted"),
                qa_id=result.get("qa_id", qa_id),
                feedback_score=feedback_score,
            )
        except CogneeConnectionError as e:
            logger.error(f"Failed to submit feedback: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during submit_feedback: {e}")
            raise MemoryServiceUnavailable("Failed to submit feedback.")

    async def record_trace(
        self,
        user_id: str,
        session_id: str,
        origin_function: str,
        status: str = "success",
        memory_query: str = "",
        method_params: dict | None = None,
        error_message: str = "",
    ) -> TraceResult:
        """Record an agent trace step for observability."""
        try:
            result = await self.repo.record_trace(
                user_id=user_id,
                session_id=session_id,
                origin_function=origin_function,
                status=status,
                memory_query=memory_query,
                method_params=method_params,
                error_message=error_message,
            )
            return TraceResult(
                status=result.get("status", "recorded"),
                trace_id=result.get("trace_id"),
            )
        except CogneeConnectionError as e:
            logger.error(f"Failed to record trace: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during record_trace: {e}")
            raise MemoryServiceUnavailable("Failed to record trace.")

    async def get_cognify_status(self) -> CognifyStatus:
        """Check the status of the cognify pipeline."""
        try:
            result = await self.repo.cognify_status()
            status_val = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
            return CognifyStatus(
                status=status_val,
                details=result if isinstance(result, dict) else {"raw": result},
            )
        except CogneeConnectionError as e:
            logger.error(f"Failed to get cognify status: {e}")
            raise MemoryServiceUnavailable("Memory subsystem is unreachable.")
        except CogneeToolError as e:
            logger.error(f"Cognee tool error during cognify_status: {e}")
            raise MemoryServiceUnavailable("Failed to retrieve cognify status.")
