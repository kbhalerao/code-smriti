#!/usr/bin/env python3
"""
Check FTS index configuration to see if 'type' field is stored
"""
import asyncio
import httpx
import json

async def main():
    print("=" * 80)
    print("CHECKING: FTS Index Configuration")
    print("=" * 80)

    async with httpx.AsyncClient() as client:
        # Get index definition
        response = await client.get(
            "http://localhost:8094/api/index/code_vector_index",
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"ERROR: {response.status_code}")
            print(response.text)
            return

        index_def = response.json()

        print("\nIndex type:", index_def.get('type'))
        print("\nParams:")
        params = index_def.get('params', {})
        print(json.dumps(params, indent=2))

        # Check if doc_config stores the type field
        doc_config = params.get('doc_config', {})
        print("\nDocument Config:")
        print(json.dumps(doc_config, indent=2))

        # Check mapping
        mapping = params.get('mapping', {})
        print("\nMapping:")
        print(json.dumps(mapping, indent=2)[:1000], "...")

asyncio.run(main())
