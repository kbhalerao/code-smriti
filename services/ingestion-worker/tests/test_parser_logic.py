#!/usr/bin/env python3
"""
Test Parser Logic
Isolates parse_python_file and parse_javascript_file to verify behavior.
"""
import asyncio
import sys
from pathlib import Path
from loguru import logger

# Add ingestion-worker to path
sys.path.insert(0, str(Path(__file__).parent))

from parsers.code_parser import CodeParser

async def test_python_parsing():
    logger.info("Testing Python Parsing...")
    parser = CodeParser()
    
    code = """
import os

def my_function(a, b):
    \"\"\"This is a docstring\"\"\"
    return a + b

class MyClass:
    \"\"\"Class docstring\"\"\"
    def __init__(self):
        self.x = 1
        
    def method_one(self):
        return self.x
"""
    
    chunks = await parser.parse_python_file(
        file_path=Path("test.py"),
        content=code,
        repo_id="test-repo",
        relative_path="test.py",
        git_metadata={"commit_hash": "123"}
    )
    
    logger.info(f"Generated {len(chunks)} chunks for Python code")
    for chunk in chunks:
        logger.info(f"  - Type: {chunk.chunk_type}, Name: {chunk.metadata.get('function_name') or chunk.metadata.get('class_name')}")

async def test_tsx_parsing():
    logger.info("\nTesting TSX Parsing...")
    parser = CodeParser()
    
    code = """
import React from 'react';

interface Props {
  name: string;
}

export const MyComponent: React.FC<Props> = ({ name }) => {
  return <div>Hello {name}</div>;
};

function helperFunction(x: number): number {
    return x * 2;
}
"""
    
    chunks = await parser.parse_javascript_file(
        file_path=Path("test.tsx"),
        content=code,
        repo_id="test-repo",
        relative_path="test.tsx",
        git_metadata={"commit_hash": "123"},
        is_typescript=True
    )
    
    logger.info(f"Generated {len(chunks)} chunks for TSX code")
    for chunk in chunks:
        logger.info(f"  - Type: {chunk.chunk_type}, Name: {chunk.metadata.get('function_name') or chunk.metadata.get('class_name')}")

async def main():
    try:
        await test_python_parsing()
        await test_tsx_parsing()
    except Exception as e:
        logger.exception("Test failed")

if __name__ == "__main__":
    asyncio.run(main())
