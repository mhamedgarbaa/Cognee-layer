# ── Fix 1: tiktoken aliases ───────────────────────────────────────────────────
# Register Azure deployment name variants so token counting works for models
# like text-embedding-3-small-1 (Azure appends numeric suffixes).
try:
    import tiktoken
    _extra = [
        "text-embedding-3-small-1",
        "openai/text-embedding-3-small-1",
        "text-embedding-3-large-1",
    ]
    for _m in _extra:
        tiktoken.model.MODEL_TO_ENCODING.setdefault(_m, "cl100k_base")
except Exception:
    pass

# ── Fix 2: litellm Azure AI Foundry embedding patch ───────────────────────────
# LiteLLM adds "X-Stainless-Raw-Response: true" to every embedding request,
# which forces the OpenAI SDK's _legacy_response.parse() code path. Azure AI
# Foundry's /models endpoint returns a response that path cannot handle:
#   AttributeError: 'str' object has no attribute 'data'
# We install a builtins.__import__ hook that fires the moment any litellm
# module is first imported and replaces litellm.aembedding with a version
# that calls AsyncOpenAI directly — no LiteLLM, no legacy header, no crash.
import builtins as _builtins
_orig_import = _builtins.__import__


def _patching_import(name, globals=None, locals=None, fromlist=(), level=0):
    mod = _orig_import(name, globals, locals, fromlist, level)
    if "litellm" in name:
        import sys as _sys
        lm = _sys.modules.get("litellm")
        if lm is not None and not getattr(lm, "_azure_embed_fixed", False):
            try:
                _patch_litellm_module(lm)
                lm._azure_embed_fixed = True
            except Exception:
                pass
        # Propagate to litellm.main for `from litellm.main import aembedding`
        lm_main = _sys.modules.get("litellm.main")
        if (
            lm_main is not None
            and not getattr(lm_main, "_azure_embed_fixed", False)
            and lm is not None
            and getattr(lm, "_azure_embed_fixed", False)
        ):
            try:
                if hasattr(lm_main, "aembedding"):
                    lm_main.aembedding = lm.aembedding
                lm_main._azure_embed_fixed = True
            except Exception:
                pass
    return mod


def _patch_litellm_module(litellm_mod):
    import sys as _sys

    _orig_aemb = getattr(litellm_mod, "aembedding", None)

    async def _fixed_aembedding(model, input, **kw):
        import os as _os

        try:
            from openai import AsyncAzureOpenAI

            endpoint = _os.getenv("EMBEDDING_ENDPOINT", "").rstrip("/")
            if endpoint.endswith("/models"):
                endpoint = endpoint[:-7]
            api_key = (
                _os.getenv("EMBEDDING_API_KEY")
                or _os.getenv("LLM_API_KEY")
                or ""
            )
            if not endpoint or not api_key:
                if _orig_aemb:
                    return await _orig_aemb(model, input, **kw)
                raise RuntimeError("EMBEDDING_ENDPOINT / EMBEDDING_API_KEY not set")
            clean_model = model.split("/")[-1] if "/" in model else model
            api_version = kw.get("api_version") or "2024-02-01"
            client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=endpoint,
                api_version=api_version,
            )
            result = await client.embeddings.create(
                model=clean_model,
                input=input if isinstance(input, list) else [input],
            )

            class _D:
                __slots__ = ("embedding",)

                def __init__(self, emb):
                    self.embedding = emb

            class _R:
                __slots__ = ("data",)

                def __init__(self, data):
                    self.data = data

            return _R([_D(item.embedding) for item in result.data])
        except Exception:
            if _orig_aemb:
                return await _orig_aemb(model, input, **kw)
            raise

    litellm_mod.aembedding = _fixed_aembedding
    # Also patch litellm.main if it's already in sys.modules
    lm_main = _sys.modules.get("litellm.main")
    if lm_main is not None and hasattr(lm_main, "aembedding"):
        lm_main.aembedding = _fixed_aembedding


_builtins.__import__ = _patching_import
