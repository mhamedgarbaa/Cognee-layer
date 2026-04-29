"""
Patch LiteLLMEmbeddingEngine to call AsyncAzureOpenAI directly instead of litellm.aembedding.

Root cause: the EMBEDDING_ENDPOINT uses *.services.ai.azure.com which requires the
Azure OpenAI /openai/deployments/{model}/embeddings format. Using AsyncOpenAI (OpenAI-
compatible client) sends to /models/embeddings which returns a 200 with empty body —
Azure's gRPC router replies "Unrecognized endpoint (/v1/engines/.../embeddings)", causing
the SDK to return a string instead of a response object:
    AttributeError: 'str' object has no attribute 'data'

Fix: replace the litellm.aembedding call with AsyncAzureOpenAI.embeddings.create(),
stripping any /models suffix from the endpoint so the deployment URL resolves correctly.
"""
import re
import sys

PATH = (
    "/app/.venv/lib/python3.12/site-packages/cognee/infrastructure"
    "/databases/vector/embeddings/LiteLLMEmbeddingEngine.py"
)

with open(PATH) as f:
    src = f.read()

if "_DirectOAI" in src:
    print("Patch already applied — skipping.")
    sys.exit(0)

# Match the litellm.aembedding block + the following return statement.
# Capture the indentation levels so the replacement is indentation-agnostic.
pattern = (
    r"([ \t]+)# Ensure each attempt does not hang indefinitely\n"
    r"\1response = await asyncio\.wait_for\(\n"
    r"[ \t]+litellm\.aembedding\(\*\*embedding_kwargs\),\n"
    r"[ \t]+timeout=30\.0,\n"
    r"\1\)\n"
    r"\n"
    r"([ \t]+)return \[data\[\"embedding\"\] for data in response\.data\]"
)


def _replacement(m: re.Match) -> str:
    ii = m.group(1)   # inner indent (inside async-with block)
    oi = m.group(2)   # outer indent (return statement level)
    return (
        f'{ii}# Azure fix: use AsyncAzureOpenAI — the /models/embeddings path returns\n'
        f'{ii}# empty body (gRPC routing error); the deployment format works correctly.\n'
        f'{ii}from openai import AsyncAzureOpenAI as _AzureOAI\n'
        f'{ii}_base = embedding_kwargs["api_base"].rstrip("/")\n'
        f'{ii}if _base.endswith("/models"):\n'
        f'{ii}    _base = _base[:-7]\n'
        f'{ii}_mdl = embedding_kwargs["model"].split("/")[-1]\n'
        f'{ii}_ver = embedding_kwargs.get("api_version") or "2024-02-01"\n'
        f'{ii}_cli = _AzureOAI(\n'
        f'{ii}    api_key=embedding_kwargs["api_key"],\n'
        f'{ii}    azure_endpoint=_base,\n'
        f'{ii}    api_version=_ver,\n'
        f'{ii})\n'
        f'{ii}_kw = {{"model": _mdl, "input": embedding_kwargs["input"]}}\n'
        f'{ii}if "dimensions" in embedding_kwargs:\n'
        f'{ii}    _kw["dimensions"] = embedding_kwargs["dimensions"]\n'
        f'{ii}_res = await asyncio.wait_for(\n'
        f'{ii}    _cli.embeddings.create(**_kw),\n'
        f'{ii}    timeout=30.0,\n'
        f'{ii})\n'
        f'{oi}return [item.embedding for item in _res.data]'
    )


new_src, n = re.subn(pattern, _replacement, src)

if n == 0:
    print("ERROR: pattern not found — no changes made.", file=sys.stderr)
    idx = src.find("litellm.aembedding")
    if idx >= 0:
        print("Context around litellm.aembedding:", file=sys.stderr)
        print(repr(src[max(0, idx - 300): idx + 300]), file=sys.stderr)
    sys.exit(1)

with open(PATH, "w") as f:
    f.write(new_src)

print(f"Patch applied ({n} replacement(s)) → {PATH}")
