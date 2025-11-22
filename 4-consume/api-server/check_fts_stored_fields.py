#!/usr/bin/env python3
"""
Check which fields are stored in the FTS index
"""
import asyncio
import httpx
import json

async def main():
    print("=" * 80)
    print("FTS FIELD STORAGE STATUS")
    print("=" * 80)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8094/api/index/code_vector_index",
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        index_def = response.json()
        props = index_def['indexDef']['params']['mapping']['types']['code_chunk']['properties']

        print(f"\n{'Field':<15} | {'Stored':<7} | {'Indexed':<7} | Type")
        print("-" * 60)

        for field_name, field_config in sorted(props.items()):
            fields = field_config.get('fields', [])
            if fields:
                field_def = fields[0]
                is_stored = field_def.get('store', False)
                is_indexed = field_def.get('index', False)
                field_type = field_def.get('type', 'unknown')
                stored_str = "YES" if is_stored else "NO"
                indexed_str = "YES" if is_indexed else "NO"
                print(f"{field_name:<15} | {stored_str:<7} | {indexed_str:<7} | {field_type}")

        print("\n" + "=" * 80)
        print("ISSUE: Fields need 'store': true to be returned in search results")
        print("=" * 80)

asyncio.run(main())
