#!/usr/bin/env python3
"""
Migration script: CouchDB (idea system) -> Couchbase (Chief of Staff)

Migrates documents from the old /idea CouchDB system to the new /cos Couchbase system.

Usage:
    python migrate_idea_to_cos.py [--dry-run] [--limit N]

Environment variables needed:
    Source (CouchDB):
        COUCHDB_URL, COUCHDB_USERNAME, COUCHDB_PASSWORD, COUCHDB_DATABASE

    Target (CoS API):
        COS_API_URL, COS_EMAIL, COS_PASSWORD
"""

import argparse
import os
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv

# Load environment
load_dotenv()


class CouchDBReader:
    """Read documents from CouchDB."""

    def __init__(self):
        self.url = os.getenv("COUCHDB_URL", "http://localhost:5984")
        self.username = os.getenv("COUCHDB_USERNAME")
        self.password = os.getenv("COUCHDB_PASSWORD")
        self.database = os.getenv("COUCHDB_DATABASE", "ideas")
        self.session = httpx.Client(auth=(self.username, self.password), verify=False)

    def get_all_ideas(self, limit: int = None) -> list[dict]:
        """Fetch all idea documents from CouchDB."""
        params = {"include_docs": "true"}
        if limit:
            params["limit"] = limit

        url = f"{self.url}/{self.database}/_all_docs"
        response = self.session.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        ideas = []
        for row in data.get("rows", []):
            doc = row.get("doc", {})
            # Skip design documents and non-idea docs
            if doc.get("_id", "").startswith("_design"):
                continue
            if doc.get("type") == "idea":
                ideas.append(doc)

        return ideas

    def close(self):
        self.session.close()


class CoSWriter:
    """Write documents to CoS API."""

    def __init__(self):
        self.api_url = os.getenv("COS_API_URL", "http://localhost")
        self.email = os.getenv("COS_EMAIL")
        self.password = os.getenv("COS_PASSWORD")
        self._token = None
        self.session = httpx.Client(verify=False, timeout=30.0)

    def _get_token(self) -> str:
        """Authenticate and get JWT token."""
        if self._token:
            return self._token

        response = self.session.post(
            f"{self.api_url}/api/auth/login",
            json={"email": self.email, "password": self.password},
        )
        response.raise_for_status()
        self._token = response.json()["token"]
        return self._token

    def create_document(self, doc: dict) -> dict:
        """Create a document via the CoS API."""
        token = self._get_token()
        response = self.session.post(
            f"{self.api_url}/api/cos/docs",
            json=doc,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()

    def close(self):
        self.session.close()


def transform_idea_to_cos(idea: dict) -> dict:
    """Transform a CouchDB idea document to CoS format."""
    # Map status values
    status_map = {
        "todo": "todo",
        "in-progress": "in-progress",
        "done": "done",
        "archived": "archived",
        # Add inbox for items without explicit status
        None: "inbox",
    }

    old_status = idea.get("status", "todo")
    new_status = status_map.get(old_status, "inbox")

    # Determine doc_type - the old system only had "idea" type
    # but we can infer based on content patterns
    content = idea.get("content", "")
    doc_type = "idea"  # default

    # Heuristics for doc_type
    content_lower = content.lower()
    if any(
        word in content_lower
        for word in ["fix", "add", "implement", "create", "update", "remove", "refactor"]
    ):
        doc_type = "task"
    elif old_status in ["todo", "in-progress"]:
        doc_type = "task"

    # Build the CoS document
    cos_doc = {
        "doc_type": doc_type,
        "content": content,
        "tags": idea.get("tags", []),
        "priority": idea.get("priority", "medium"),
        "status": new_status,
        "source": {
            "client": "migration",
            "project": idea.get("metadata", {}).get("project"),
        },
        "metadata": {
            "migrated_from": "couchdb-idea",
            "original_id": idea.get("_id"),
            "original_created": idea.get("created"),
            "original_updated": idea.get("updated"),
            **idea.get("metadata", {}),
        },
    }

    return cos_doc


def main():
    parser = argparse.ArgumentParser(description="Migrate ideas from CouchDB to CoS")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be migrated")
    parser.add_argument("--limit", type=int, help="Limit number of documents to migrate")
    args = parser.parse_args()

    # Validate environment
    required_source = ["COUCHDB_URL", "COUCHDB_USERNAME", "COUCHDB_PASSWORD"]
    required_target = ["COS_API_URL", "COS_EMAIL", "COS_PASSWORD"]

    missing = [v for v in required_source + required_target if not os.getenv(v)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}")
        print("\nRequired environment variables:")
        print("  Source: COUCHDB_URL, COUCHDB_USERNAME, COUCHDB_PASSWORD, COUCHDB_DATABASE")
        print("  Target: COS_API_URL, COS_EMAIL, COS_PASSWORD")
        sys.exit(1)

    # Initialize clients
    reader = CouchDBReader()
    writer = CoSWriter()

    try:
        # Fetch all ideas
        print(f"Fetching ideas from CouchDB ({reader.url}/{reader.database})...")
        ideas = reader.get_all_ideas(limit=args.limit)
        print(f"Found {len(ideas)} ideas to migrate")

        if not ideas:
            print("No ideas found to migrate.")
            return

        # Stats
        migrated = 0
        failed = 0
        skipped = 0

        for i, idea in enumerate(ideas, 1):
            original_id = idea.get("_id", "unknown")[:8]
            content_preview = idea.get("content", "")[:50]

            if args.dry_run:
                cos_doc = transform_idea_to_cos(idea)
                print(f"[DRY-RUN] {i}/{len(ideas)} Would migrate: {original_id} -> {cos_doc['doc_type']}: {content_preview}...")
                continue

            try:
                cos_doc = transform_idea_to_cos(idea)
                result = writer.create_document(cos_doc)
                new_id = result.get("id", "unknown")[:8]
                print(f"[OK] {i}/{len(ideas)} Migrated {original_id} -> {new_id}: {content_preview}...")
                migrated += 1
            except httpx.HTTPStatusError as e:
                print(f"[FAIL] {i}/{len(ideas)} Failed {original_id}: {e.response.status_code} - {e.response.text[:100]}")
                failed += 1
            except Exception as e:
                print(f"[FAIL] {i}/{len(ideas)} Failed {original_id}: {e}")
                failed += 1

        # Summary
        print("\n" + "=" * 50)
        if args.dry_run:
            print(f"DRY RUN - Would have migrated {len(ideas)} documents")
        else:
            print(f"Migration complete!")
            print(f"  Migrated: {migrated}")
            print(f"  Failed:   {failed}")
            print(f"  Skipped:  {skipped}")

    finally:
        reader.close()
        writer.close()


if __name__ == "__main__":
    main()
