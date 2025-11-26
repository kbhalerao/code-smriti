#!/usr/bin/env python3
"""Test document parsing on Sphinx docs from labcore"""

import asyncio
from pathlib import Path
from parsers.document_parser import DocumentParser

# Largest .rst and .html files from labcore docs
RST_FILE = "/Users/kaustubh/Documents/codesmriti-repos/kbhalerao_labcore/docs/source/user_guides/order_status_management.rst"
HTML_FILE = "/Users/kaustubh/Documents/codesmriti-repos/kbhalerao_labcore/docs/build/html/modules/samples.html"

async def test_file(parser, file_path_str, file_type):
    """Test parsing a single documentation file"""
    file_path = Path(file_path_str)

    if not file_path.exists():
        print(f"❌ File not found: {file_path_str}")
        return None

    content = file_path.read_text()

    print(f"\n{'=' * 80}")
    print(f"Testing {file_type.upper()} file: {file_path.name}")
    print(f"{'=' * 80}")
    print(f"File size: {len(content):,} chars ({len(content.splitlines())} lines)")
    print(f"Estimated tokens: {int(len(content) * 0.75):,}")

    # Mock repo path and git metadata
    repo_path = file_path.parent.parent.parent  # Go up to docs root
    repo_id = "kbhalerao/labcore"

    # Parse the file
    chunks = await parser.parse_file(file_path, repo_path, repo_id)

    print(f"\n✓ Created {len(chunks)} chunk(s)")

    if chunks:
        chunk = chunks[0]
        print(f"\nChunk details:")
        print(f"  - Type: {chunk.type}")
        print(f"  - Doc type: {chunk.doc_type}")
        print(f"  - File path: {chunk.file_path}")
        print(f"  - Content length: {len(chunk.content):,} chars")
        print(f"  - Content preview (first 200 chars):")
        print(f"    {chunk.content[:200]}...")

        print(f"\n  Metadata:")
        for key, value in chunk.metadata.items():
            if key == "frontmatter" and not value:
                continue  # Skip empty frontmatter
            if isinstance(value, str) and len(value) > 100:
                print(f"    - {key}: {value[:100]}... (truncated)")
            else:
                print(f"    - {key}: {value}")

    return chunks

async def main():
    parser = DocumentParser()

    print("Testing Sphinx Documentation Parsing")
    print("=" * 80)

    # Test RST file
    rst_chunks = await test_file(parser, RST_FILE, "rst")

    # Test HTML file
    html_chunks = await test_file(parser, HTML_FILE, "html")

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"RST file: {len(rst_chunks) if rst_chunks else 0} chunk(s)")
    print(f"HTML file: {len(html_chunks) if html_chunks else 0} chunk(s)")

    # Check for issues
    if rst_chunks:
        rst_size = len(rst_chunks[0].content)
        if rst_size > 6000:
            print(f"\n⚠️  WARNING: RST chunk is {rst_size:,} chars (exceeds 6000 limit)")
        else:
            print(f"\n✓ RST chunk size OK: {rst_size:,} chars")

    if html_chunks:
        html_size = len(html_chunks[0].content)
        if html_size > 6000:
            print(f"⚠️  WARNING: HTML chunk is {html_size:,} chars (exceeds 6000 limit)")
        else:
            print(f"✓ HTML chunk size OK: {html_size:,} chars")

if __name__ == "__main__":
    asyncio.run(main())
