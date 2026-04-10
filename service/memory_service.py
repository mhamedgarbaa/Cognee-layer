from data.repositories.cognee_repository import CogneeRepository
from data.custom_data_exceptions import CogneeConnectionError, CogneeToolError
from service.custom_service_exceptions import MemoryServiceUnavailable
from service.business_models import ContextEnvelope
from configuration.logging_setup import logger


class MemoryService:
    def __init__(self, repo: CogneeRepository):
        self.repo = repo

    async def record_agent_memory(self, user_id: str, fact: str) -> None:
        """Stores a fact and triggers background temporal consolidation."""
        try:
            # 1. Fast Append to STM (SQLite)
            await self.repo.save_interaction(user_id=user_id, data=fact)

            # 2. Trigger LTM Consolidation (Neo4j/Kuzu Temporal Graph)
            await self.repo.trigger_cognify(user_id=user_id)

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
            results = await self.repo.search(
                user_id=user_id,
                query=query,
                query_type="GRAPH_COMPLETION"
            )
            return ContextEnvelope(
                context_data=[str(r) for r in results],
                is_degraded=False,
                retrieval_method="GRAPH_COMPLETION"
            )

        except (CogneeConnectionError, CogneeToolError) as e:
            logger.warning(f"GRAPH_COMPLETION failed, falling back to CHUNKS. Error: {e}", extra={"user_id": user_id})

            try:
                # Attempt Fallback: Raw Semantic Search (Bypasses LLM bottleneck)
                fallback_results = await self.repo.search(
                    user_id=user_id,
                    query=query,
                    query_type="CHUNKS"
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
