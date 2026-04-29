#!/usr/bin/env python3
"""
Test script for LLM and Embedding with detailed diagnostics.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_with_api_versions():
    """Test different API versions to find the correct one."""
    print("\n" + "="*60)
    print("Testing Different API Versions")
    print("="*60)

    from openai import AzureOpenAI

    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_endpoint = os.getenv("LLM_ENDPOINT", "").rstrip("/").replace("/models", "")

    api_versions = [
        "2025-04-01-preview",
    ]

    models = ["gpt-5.4-nano"]

    print(f"Endpoint: {llm_endpoint}")
    print(f"API Key: {llm_api_key[:20]}...\n")

    for api_version in api_versions:
        print(f"\nTrying API version: {api_version}")
        for model in models:
            try:
                client = AzureOpenAI(
                    api_key=llm_api_key,
                    api_version=api_version,
                    azure_endpoint=llm_endpoint,
                )
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=5,
                )
                print(f"  SUCCESS with model '{model}' on API version {api_version}")
                return llm_endpoint, model, api_version
            except Exception as e:
                error_str = str(e).lower()
                if "401" in error_str or "unauthorized" in error_str:
                    print(f"  Auth error (401) - check API key")
                    break  # No point trying more models with bad auth
                elif "404" in error_str:
                    continue
                else:
                    continue

    return None, None, None


def check_config():
    """Check current configuration from .env"""
    print("\n" + "="*60)
    print("Current Configuration")
    print("="*60)

    llm_model = os.getenv("LLM_MODEL", "NOT SET")
    llm_endpoint = os.getenv("LLM_ENDPOINT", "NOT SET")
    llm_api_key = os.getenv("LLM_API_KEY", "NOT SET")
    embedding_model = os.getenv("EMBEDDING_MODEL", "NOT SET")
    embedding_endpoint = os.getenv("EMBEDDING_ENDPOINT", "NOT SET")
    embedding_api_key = os.getenv("EMBEDDING_API_KEY", "NOT SET")

    print(f"LLM_MODEL:             {llm_model}")
    print(f"LLM_ENDPOINT:          {llm_endpoint}")
    print(f"LLM_API_KEY:           {llm_api_key[:20] if llm_api_key != 'NOT SET' else 'NOT SET'}...")
    print(f"\nEMBEDDING_MODEL:       {embedding_model}")
    print(f"EMBEDDING_ENDPOINT:    {embedding_endpoint}")
    print(f"EMBEDDING_API_KEY:     {embedding_api_key[:20] if embedding_api_key != 'NOT SET' else 'NOT SET'}...")


def test_llm(endpoint, model, api_version):
    """Test LLM with discovered endpoint."""
    print("\n" + "="*60)
    print(f"Testing LLM Connectivity")
    print("="*60)

    try:
        from openai import AzureOpenAI

        llm_api_key = os.getenv("LLM_API_KEY", "")

        print(f"Endpoint:    {endpoint}")
        print(f"Model:       {model}")
        print(f"API Version: {api_version}")

        client = AzureOpenAI(
            api_key=llm_api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'LLM is working!' in exactly those words."}
            ],
            max_tokens=50,
            temperature=0.7,
        )

        result = response.choices[0].message.content
        print(f"\n[OK] LLM is working!")
        print(f"Response: {result}")
        return True

    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        return False


def main():
    print("\n" + "="*60)
    print("LLM & Embedding Service Test")
    print("="*60)

    check_config()

    endpoint, model, api_version = test_with_api_versions()

    if endpoint and model and api_version:
        print(f"\n\nFound working configuration!")
        print(f"Endpoint: {endpoint}")
        print(f"Model: {model}")
        print(f"API Version: {api_version}")

        llm_ok = test_llm(endpoint, model, api_version)

        print("\n" + "="*60)
        print("Recommendations:")
        print("="*60)
        if llm_ok:
            print(f"Update .env with:")
            print(f"  LLM_ENDPOINT=\"{endpoint}\"")
            print(f"  LLM_MODEL=\"{model}\"")

        return 0 if llm_ok else 1
    else:
        print("\n\n[FAIL] Could not find working configuration")
        print("\nPossible issues:")
        print("1. API key is invalid or expired")
        print("2. Azure OpenAI resource not created")
        print("3. Deployments not created in Azure portal")
        print("4. API key doesn't have permissions")
        print("\nVerify your Azure OpenAI setup in Azure Portal")
        return 1


if __name__ == "__main__":
    sys.exit(main())
