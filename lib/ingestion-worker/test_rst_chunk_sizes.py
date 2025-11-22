#!/usr/bin/env python3
"""Test RST chunking - show size distribution"""

import asyncio
from pathlib import Path
from parsers.document_parser import DocumentParser

RST_FILE = "/Users/kaustubh/Documents/codesmriti-repos/kbhalerao_labcore/docs/source/user_guides/order_status_management.rst"

async def main():
    parser = DocumentParser()
    file_path = Path(RST_FILE)
    content = file_path.read_text()

    # Mock repo and git metadata
    repo_path = file_path.parent.parent.parent
    repo_id = "kbhalerao/labcore"

    chunks = await parser.parse_file(file_path, repo_path, repo_id)

    print(f"Total chunks: {len(chunks)}")
    print(f"Original file size: {len(content):,} chars\n")
    print("=" * 80)

    # Show all chunk sizes
    for i, chunk in enumerate(chunks, 1):
        chunk_size = len(chunk.content)
        section_title = chunk.metadata.get("section_title", "(no title)")
        print(f"Chunk {i:2d}: {chunk_size:5,} chars - {section_title[:60]}")

    # Statistics
    sizes = [len(chunk.content) for chunk in chunks]
    avg_size = sum(sizes) / len(sizes)
    max_size = max(sizes)
    min_size = min(sizes)

    print("=" * 80)
    print(f"\nChunk Size Statistics:")
    print(f"  Average: {avg_size:,.0f} chars (~{avg_size * 0.75:,.0f} tokens)")
    print(f"  Largest: {max_size:,} chars (~{max_size * 0.75:,.0f} tokens)")
    print(f"  Smallest: {min_size:,} chars (~{min_size * 0.75:,.0f} tokens)")

    # Check for problems
    over_limit = [c for c in chunks if len(c.content) > 6000]
    if over_limit:
        print(f"\n⚠️  WARNING: {len(over_limit)} chunks exceed 6000 char limit!")
        for chunk in over_limit:
            print(f"  - {chunk.metadata.get('section_title', 'unknown')}: {len(chunk.content):,} chars")
    else:
        print(f"\n✓ All chunks under 6000 char limit!")

if __name__ == "__main__":
    asyncio.run(main())
