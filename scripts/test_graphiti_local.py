import asyncio, sys, json, os, traceback
sys.path.insert(0, '/app/src')

print("=== ENV ===")
for k in ["OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_ENDPOINT", "LLM_API_KEY", "GRAPHITI_LLM_PROVIDER", "GRAPHITI_LLM_MODEL"]:
    print(f"{k}={os.environ.get(k, '(not set)')}")
print()

try:
    from cognee.tasks.temporal_awareness import build_graph_with_temporal_awareness

    texts = ["Meyssen is fired"]

    async def run():
        graphiti = await build_graph_with_temporal_awareness(texts)
        await graphiti.close()
        print("Ingested OK")

    asyncio.run(run())

except Exception as e:
    print("=== FULL TRACEBACK ===")
    traceback.print_exc()
