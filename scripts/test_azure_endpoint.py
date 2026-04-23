"""Test Azure endpoint variations to find the correct URL format."""
import asyncio, os, traceback
from openai import AsyncOpenAI

llm_key = os.getenv("LLM_API_KEY", "")
llm_endpoint = os.getenv("LLM_ENDPOINT", "").rstrip("/")
# Try both with and without stripping the prefix
models_to_try = [
    os.getenv("LLM_MODEL", ""),           # raw: openai/gpt-5-nano
    os.getenv("LLM_MODEL", "").replace("openai/", ""),  # stripped: gpt-5-nano
]
urls_to_try = [
    llm_endpoint,              # .../models
    llm_endpoint + "/v1",      # .../models/v1  (OpenAI compat path)
]

async def probe(base_url, model):
    client = AsyncOpenAI(api_key=llm_key, base_url=base_url, timeout=15)
    r = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Say OK"}],
        max_tokens=3,
    )
    return r.choices[0].message.content

async def main():
    print(f"Key prefix: {llm_key[:12]}...")
    for url in urls_to_try:
        for model in models_to_try:
            label = f"  url={url}  model={model}"
            try:
                result = await probe(url, model)
                print(f"[OK] {label}  -> {result!r}")
            except Exception as e:
                print(f"[FAIL] {label}  -> {type(e).__name__}: {str(e)[:120]}")

asyncio.run(main())
