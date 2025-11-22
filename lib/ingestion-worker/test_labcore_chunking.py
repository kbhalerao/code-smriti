#!/usr/bin/env python3
"""Test chunking on a real labcore file (clients/models/gis.py)"""

import asyncio
import sys
from pathlib import Path
from parsers.code_parser import CodeParser

# Path to the labcore gis.py file
GIS_FILE = "/Users/kaustubh/Documents/codesmriti-repos/kbhalerao_labcore/clients/models/gis.py"

async def main():
    parser = CodeParser()

    # Read the file
    file_path = Path(GIS_FILE)
    if not file_path.exists():
        print(f"❌ File not found: {GIS_FILE}")
        return

    content = file_path.read_text()

    print(f"Testing chunking on: {file_path.name}")
    print(f"File size: {len(content):,} chars ({len(content.splitlines())} lines)")
    print("=" * 80)

    # Mock git metadata for testing
    git_metadata = {
        "commit_hash": "abc123",
        "commit_message": "Test commit",
        "commit_author": "test@example.com",
        "commit_date": "2024-11-20",
        "branch": "main"
    }

    # Parse the file
    chunks = await parser.parse_python_file(
        file_path=file_path,
        content=content,
        repo_id="kbhalerao/labcore",
        relative_path="clients/models/gis.py",
        git_metadata=git_metadata
    )

    print(f"\n✓ TOTAL CHUNKS CREATED: {len(chunks)}")
    print("=" * 80)

    # Group chunks by type
    chunks_by_type = {}
    for chunk in chunks:
        chunk_type = chunk.chunk_type
        if chunk_type not in chunks_by_type:
            chunks_by_type[chunk_type] = []
        chunks_by_type[chunk_type].append(chunk)

    # Print summary
    print("\nCHUNK BREAKDOWN:")
    print("-" * 80)
    for chunk_type, type_chunks in sorted(chunks_by_type.items()):
        print(f"\n{chunk_type.upper()} ({len(type_chunks)} chunks):")
        for i, chunk in enumerate(type_chunks, 1):
            metadata = chunk.metadata

            # Get identifying info
            if chunk_type == "class":
                name = metadata.get("class_name", "unknown")
                size = len(chunk.code_text)
                print(f"  {i}. Class: {name} ({size:,} chars)")

            elif chunk_type == "method":
                class_name = metadata.get("class_name", "")
                method_name = metadata.get("method_name", "unknown")
                params = metadata.get("parameters", "()")
                size = len(chunk.code_text)
                full_name = f"{class_name}.{method_name}" if class_name else method_name
                print(f"  {i}. Method: {full_name}{params} ({size:,} chars)")

            elif chunk_type == "function":
                func_name = metadata.get("function_name", "unknown")
                params = metadata.get("parameters", "()")
                size = len(chunk.code_text)
                print(f"  {i}. Function: {func_name}{params} ({size:,} chars)")

            else:
                size = len(chunk.code_text)
                print(f"  {i}. {chunk_type} ({size:,} chars)")

    # Print size distribution
    print("\n" + "=" * 80)
    print("CHUNK SIZE DISTRIBUTION:")
    print("-" * 80)

    sizes = [len(chunk.code_text) for chunk in chunks]
    avg_size = sum(sizes) / len(sizes) if sizes else 0
    max_size = max(sizes) if sizes else 0
    min_size = min(sizes) if sizes else 0

    print(f"Average chunk size: {avg_size:,.0f} chars (~{avg_size * 0.75:,.0f} tokens)")
    print(f"Largest chunk: {max_size:,} chars (~{max_size * 0.75:,.0f} tokens)")
    print(f"Smallest chunk: {min_size:,} chars (~{min_size * 0.75:,.0f} tokens)")

    # Check for truncation
    truncated = [chunk for chunk in chunks if len(chunk.code_text) >= 6000]
    if truncated:
        print(f"\n⚠️  WARNING: {len(truncated)} chunks hit the 6000 char limit (may be truncated)")
        for chunk in truncated[:5]:  # Show first 5
            metadata = chunk.metadata
            if chunk.chunk_type == "method":
                name = f"{metadata.get('class_name', '')}.{metadata.get('method_name', 'unknown')}"
            elif chunk.chunk_type == "class":
                name = metadata.get("class_name", "unknown")
            elif chunk.chunk_type == "function":
                name = metadata.get("function_name", "unknown")
            else:
                name = chunk.chunk_type
            print(f"  - {name}: {len(chunk.code_text):,} chars")
    else:
        print(f"\n✓ No truncation detected! All chunks are under 6000 chars.")

    print("\n" + "=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
