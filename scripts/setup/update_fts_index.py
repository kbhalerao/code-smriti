#!/usr/bin/env python3
"""
Update the code_vector_index FTS index in Couchbase.

Usage:
    python update_fts_index.py [--delete-first]

Options:
    --delete-first    Delete and recreate the index (required for major mapping changes)
"""

import os
import json
import sys
import argparse
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


def get_couchbase_config():
    """Get Couchbase connection config from environment."""
    return {
        "host": os.getenv("COUCHBASE_HOST", "localhost"),
        "username": os.getenv("COUCHBASE_USERNAME", "Administrator"),
        "password": os.environ["COUCHBASE_PASSWORD"],
    }


def get_index_definition():
    """Load index definition from JSON file."""
    script_dir = Path(__file__).parent
    index_file = script_dir / "code_vector_index.json"

    with open(index_file) as f:
        return json.load(f)


def delete_index(config: dict) -> bool:
    """Delete the existing FTS index."""
    url = f"http://{config['host']}:8094/api/index/code_vector_index"

    print(f"Deleting FTS index...")
    response = httpx.delete(
        url,
        auth=(config["username"], config["password"]),
        timeout=30.0
    )

    if response.status_code == 200:
        print("Index deleted successfully")
        return True
    elif response.status_code == 404:
        print("Index doesn't exist, nothing to delete")
        return True
    else:
        print(f"Failed to delete index: {response.status_code} - {response.text}")
        return False


def create_or_update_index(config: dict, definition: dict) -> bool:
    """Create or update the FTS index."""
    url = f"http://{config['host']}:8094/api/index/code_vector_index"

    # Remove uuid field if present (Couchbase generates this)
    if "uuid" in definition:
        del definition["uuid"]

    print(f"Creating/updating FTS index...")
    response = httpx.put(
        url,
        json=definition,
        auth=(config["username"], config["password"]),
        timeout=60.0
    )

    if response.status_code in (200, 201):
        print("Index created/updated successfully")
        return True
    else:
        print(f"Failed to create/update index: {response.status_code} - {response.text}")
        return False


def wait_for_index_ready(config: dict, max_wait: int = 120) -> bool:
    """Wait for index to be ready for queries."""
    import time

    url = f"http://{config['host']}:8094/api/index/code_vector_index/count"

    print(f"Waiting for index to be ready...")
    start = time.time()

    while time.time() - start < max_wait:
        try:
            response = httpx.get(
                url,
                auth=(config["username"], config["password"]),
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json()
                doc_count = data.get("count", 0)
                print(f"Index ready with {doc_count:,} documents indexed")
                return True
        except Exception:
            pass

        time.sleep(5)
        print(".", end="", flush=True)

    print(f"\nTimeout waiting for index after {max_wait}s")
    return False


def main():
    parser = argparse.ArgumentParser(description="Update Couchbase FTS index")
    parser.add_argument(
        "--delete-first",
        action="store_true",
        help="Delete and recreate the index (required for major mapping changes)"
    )
    args = parser.parse_args()

    try:
        config = get_couchbase_config()
    except KeyError as e:
        print(f"Missing required environment variable: {e}")
        print("Set COUCHBASE_PASSWORD in .env file")
        sys.exit(1)

    definition = get_index_definition()

    if args.delete_first:
        if not delete_index(config):
            sys.exit(1)

    if not create_or_update_index(config, definition):
        sys.exit(1)

    if not wait_for_index_ready(config):
        print("Warning: Index may not be fully ready")
        sys.exit(1)

    print("\nFTS index update complete!")


if __name__ == "__main__":
    main()
