#!/usr/bin/env python3
"""Check if expected Svelte files exist in database"""
from app.database.couchbase_client import CouchbaseClient

db = CouchbaseClient()

# Check for ask-kev-2026 repo files
expected_files = [
    "src/lib/components/chat/ChatInput.svelte",
    "src/lib/components/chat/SuggestionButton.svelte",
    "src/lib/components/landing/GoogleSignInButton.svelte",
]

print("Checking for expected Svelte files in ask-kev-2026...")
print("=" * 80)

for file_path in expected_files:
    n1ql = f"""
        SELECT META().id, file_path, repo_id, LENGTH(content) as len
        FROM `code_kosha`
        WHERE repo_id LIKE '%ask-kev-2026%'
          AND file_path LIKE '%{file_path.split('/')[-1]}%'
          AND type = 'code_chunk'
        LIMIT 3
    """

    result = db.cluster.query(n1ql)
    rows = list(result)

    print(f"\n{file_path.split('/')[-1]}:")
    if rows:
        print(f"  ✅ Found {len(rows)} chunks")
        for row in rows:
            print(f"     - {row['file_path']} ({row['len']} chars)")
    else:
        print(f"  ❌ NOT FOUND in database")

# Check what ask-kev-2026 files we DO have
print("\n" + "=" * 80)
print("All files from ask-kev-2026 repo:")
print("=" * 80)

n1ql = """
    SELECT DISTINCT file_path
    FROM `code_kosha`
    WHERE repo_id LIKE '%ask-kev-2026%'
      AND type = 'code_chunk'
    ORDER BY file_path
"""

result = db.cluster.query(n1ql)
files = list(result)

if files:
    print(f"\nFound {len(files)} distinct files:")
    for row in files[:20]:  # Show first 20
        print(f"  - {row['file_path']}")
    if len(files) > 20:
        print(f"  ... and {len(files) - 20} more")
else:
    print("❌ No files found from ask-kev-2026 repo!")
