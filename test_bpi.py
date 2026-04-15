import asyncio
import json
import logging
from httpx import AsyncClient

logging.basicConfig(level=logging.INFO)

async def test_bpi():
    try:
        with open('bpi_france_events.json', 'r', encoding='utf-8') as f:
            events = json.load(f)
    except FileNotFoundError:
        print("bpi_france_events.json not found")
        return
        
    tenant_id = "bpi_tester"
    
    async with AsyncClient(base_url="http://localhost:8000") as client:
        print(f"1. Injecting {len(events)} facts into Memory (triggers cognify automatically)...")
        for i, event in enumerate(events):
            fact = f"Le {event['date']}, {event['title']} ({event['category']}). Description: {event['description']} Entités: {', '.join(event['entities'])}. Montant: {event['amount']}."
            
            try:
                resp = await client.post("/api/v1/memory/record", json={
                    "user_id": tenant_id,
                    "fact": fact
                }, timeout=120.0)
                
                if resp.status_code == 202:
                    print(f"Recorded ({i+1}/{len(events)}): {event['title']}")
                else:
                    print(f"Failed to record {i+1}:", resp.text)
            except Exception as e:
                print(f"HTTP Error on {i+1}: {e}")
                
            await asyncio.sleep(5.0)

        print("\nWaiting a few seconds before searching...")
        await asyncio.sleep(5)
        
        search_query = "Quels sont les programmes liés à la French Tech ou à l'innovation ?"
        print(f"\n2. Testing Search tool for {tenant_id}")
        print(f"Query: {search_query}")
        
        try:
            search_resp = await client.post("/api/v1/memory/recall", json={
                "user_id": tenant_id,
                "query": search_query
            }, timeout=180.0)
            
            if search_resp.status_code == 200:
                data = search_resp.json()
                print("\n--- Search Results ---")
                print(f"Retrieval Method: {data['retrieval_method']}")
                print(f"Is Degraded: {data['is_degraded']}")
                print("\nContext Data:")
                for item in data['context_data']:
                    print("-", item)
            else:
                print(f"Search failed (Status {search_resp.status_code}):", search_resp.text)
        except Exception as e:
            print(f"Search request failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_bpi())

