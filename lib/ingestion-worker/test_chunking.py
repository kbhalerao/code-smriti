#!/usr/bin/env python3
"""
Test script to prove current chunking behavior
Shows that entire classes are chunked together, not individual methods
"""
import asyncio
from pathlib import Path
from parsers.code_parser import CodeParser

# Test code with a class containing multiple methods
TEST_CODE = '''
def top_level_function():
    """This should be a separate chunk"""
    return "top level"

class TestClass:
    """A test class with multiple methods"""

    def __init__(self):
        self.value = 0

    def method1(self):
        """First method - should this be separate?"""
        return "method 1" * 100  # Make it have some content

    def method2(self):
        """Second method - should this be separate?"""
        return "method 2" * 100  # Make it have some content

    def method3(self):
        """Third method - should this be separate?"""
        return "method 3" * 100  # Make it have some content

class AnotherClass:
    def another_method(self):
        return "another"
'''

async def main():
    # Write test file
    test_file = Path("/tmp/test_chunking.py")
    test_file.write_text(TEST_CODE)

    # Parse it
    parser = CodeParser()
    chunks = await parser.parse_file(
        file_path=test_file,
        repo_path=Path("/tmp"),
        repo_id="test/repo"
    )

    print("=" * 80)
    print(f"TOTAL CHUNKS CREATED: {len(chunks)}")
    print("=" * 80)

    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {i}/{len(chunks)} ---")
        print(f"Type: {chunk.chunk_type}")
        print(f"Size: {len(chunk.code_text)} characters")

        if chunk.chunk_type == "function":
            print(f"Function name: {chunk.metadata.get('function_name')}")
            print(f"Code preview: {chunk.code_text[:100]}...")

        elif chunk.chunk_type == "class":
            print(f"Class name: {chunk.metadata.get('class_name')}")
            print(f"Code preview (first 200 chars): {chunk.code_text[:200]}...")
            print(f"Code preview (last 100 chars): ...{chunk.code_text[-100:]}")

            # Check if it contains all methods
            if "def method1" in chunk.code_text:
                print("  ⚠️  Contains method1")
            if "def method2" in chunk.code_text:
                print("  ⚠️  Contains method2")
            if "def method3" in chunk.code_text:
                print("  ⚠️  Contains method3")

    print("\n" + "=" * 80)
    print("ANALYSIS:")
    print("=" * 80)

    # Count chunk types
    functions = [c for c in chunks if c.chunk_type == "function"]
    classes = [c for c in chunks if c.chunk_type == "class"]

    print(f"Function chunks: {len(functions)}")
    print(f"Class chunks: {len(classes)}")

    if len(classes) > 0:
        print(f"\nClass chunk sizes:")
        for c in classes:
            print(f"  - {c.metadata.get('class_name')}: {len(c.code_text)} chars")

            # Count methods inside
            method_count = c.code_text.count("def ")
            print(f"    Contains {method_count} methods (including __init__)")

    print("\n" + "=" * 80)
    print("EXPECTED BEHAVIOR:")
    print("=" * 80)
    print("If method-level chunking worked:")
    print("  - top_level_function: 1 chunk")
    print("  - TestClass.__init__: 1 chunk")
    print("  - TestClass.method1: 1 chunk")
    print("  - TestClass.method2: 1 chunk")
    print("  - TestClass.method3: 1 chunk")
    print("  - AnotherClass.another_method: 1 chunk")
    print("  TOTAL: 6 chunks")

    print("\nACTUAL BEHAVIOR:")
    print("  - top_level_function: 1 chunk")
    print("  - TestClass (ALL methods together): 1 MASSIVE chunk")
    print("  - AnotherClass (ALL methods together): 1 chunk")
    print(f"  TOTAL: {len(chunks)} chunks")

    if len(chunks) == 3:
        print("\n❌ CONFIRMED: Entire classes are chunked together!")
        print("   Each class becomes ONE chunk containing ALL its methods.")
    else:
        print(f"\n🤔 Unexpected: Got {len(chunks)} chunks")

if __name__ == "__main__":
    asyncio.run(main())
