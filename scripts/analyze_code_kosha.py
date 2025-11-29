#!/usr/bin/env python3
"""
Code Kosha Database Statistics Analyzer

Usage:
    python scripts/analyze_code_kosha.py
    python scripts/analyze_code_kosha.py --json  # Output as JSON
"""

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions


def load_env():
    """Load environment variables from .env file"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ[key] = val


def get_cluster():
    """Connect to Couchbase cluster"""
    load_env()
    cluster = Cluster(
        f"couchbase://{os.getenv('COUCHBASE_HOST', 'localhost')}",
        ClusterOptions(PasswordAuthenticator(
            os.getenv('COUCHBASE_USERNAME', 'Administrator'),
            os.getenv('COUCHBASE_PASSWORD', 'password')
        ))
    )
    cluster.wait_until_ready(timedelta(seconds=10))
    return cluster


def query_stats(cluster, bucket_name: str) -> dict:
    """Query all statistics from the database"""
    stats = {}

    # 1. Total document count
    query = f"SELECT COUNT(*) as total FROM `{bucket_name}`._default._default"
    result = list(cluster.query(query))
    total = result[0]["total"] if result else 0
    stats["total_documents"] = total

    # 2. Document type distribution
    query = f"""
    SELECT
        IFMISSING(d.type, "unknown") as doc_type,
        COUNT(*) as count
    FROM `{bucket_name}`._default._default d
    GROUP BY IFMISSING(d.type, "unknown")
    ORDER BY count DESC
    """
    result = list(cluster.query(query))
    stats["document_types"] = {row["doc_type"]: row["count"] for row in result}

    # 3. Schema version distribution
    query = f"""
    SELECT
        IFMISSING(d.version.schema_version, "legacy/none") as version,
        COUNT(*) as count
    FROM `{bucket_name}`._default._default d
    GROUP BY IFMISSING(d.version.schema_version, "legacy/none")
    ORDER BY count DESC
    """
    result = list(cluster.query(query))
    stats["schema_versions"] = {row["version"]: row["count"] for row in result}

    # 4. Enrichment level distribution (V3)
    query = f"""
    SELECT
        IFMISSING(d.version.enrichment_level, "n/a") as enrich_level,
        COUNT(*) as cnt
    FROM `{bucket_name}`._default._default d
    WHERE d.version.schema_version = "v3.0"
    GROUP BY IFMISSING(d.version.enrichment_level, "n/a")
    ORDER BY cnt DESC
    """
    result = list(cluster.query(query))
    stats["enrichment_levels"] = {row["enrich_level"]: row["cnt"] for row in result}

    # 5. Language distribution
    query = f"""
    SELECT
        IFMISSING(d.metadata.`language`, IFMISSING(d.`language`, "unknown")) as lang,
        COUNT(*) as cnt
    FROM `{bucket_name}`._default._default d
    GROUP BY IFMISSING(d.metadata.`language`, IFMISSING(d.`language`, "unknown"))
    ORDER BY cnt DESC
    LIMIT 20
    """
    result = list(cluster.query(query))
    stats["languages"] = {row["lang"]: row["cnt"] for row in result}

    # 6. Repository distribution
    query = f"""
    SELECT
        d.repo_id,
        COUNT(*) as cnt
    FROM `{bucket_name}`._default._default d
    WHERE d.repo_id IS NOT MISSING
    GROUP BY d.repo_id
    ORDER BY cnt DESC
    LIMIT 20
    """
    result = list(cluster.query(query))
    stats["repositories"] = {str(row["repo_id"]): row["cnt"] for row in result}

    # 7. Total repo count
    query = f"""
    SELECT COUNT(DISTINCT d.repo_id) as repo_count
    FROM `{bucket_name}`._default._default d
    WHERE d.repo_id IS NOT MISSING
    """
    result = list(cluster.query(query))
    stats["total_repositories"] = result[0]["repo_count"] if result else 0

    # 8. V3 chunk breakdown
    query = f"""
    SELECT
        d.type as chunk_type,
        COUNT(*) as cnt,
        AVG(d.metadata.line_count) as avg_lines,
        SUM(CASE WHEN d.metadata.is_underchunked = true THEN 1 ELSE 0 END) as underchunked
    FROM `{bucket_name}`._default._default d
    WHERE d.version.schema_version = "v3.0"
    GROUP BY d.type
    """
    result = list(cluster.query(query))
    stats["v3_breakdown"] = {}
    for row in result:
        ct = row.get('chunk_type', 'unknown')
        stats["v3_breakdown"][ct] = {
            "count": row['cnt'],
            "avg_lines": row.get("avg_lines"),
            "underchunked": row.get("underchunked")
        }

    # 9. Embedding stats
    query = f"""
    SELECT COUNT(*) as with_embedding
    FROM `{bucket_name}`._default._default d
    WHERE d.embedding IS NOT MISSING
      AND ARRAY_LENGTH(d.embedding) > 0
    """
    result = list(cluster.query(query))
    with_emb = result[0]["with_embedding"] if result else 0
    stats["embeddings"] = {
        "with_embedding": with_emb,
        "without_embedding": total - with_emb
    }

    # Embedding dimension
    query = f"""
    SELECT ARRAY_LENGTH(d.embedding) as dim
    FROM `{bucket_name}`._default._default d
    WHERE d.embedding IS NOT MISSING
      AND ARRAY_LENGTH(d.embedding) > 0
    LIMIT 1
    """
    result = list(cluster.query(query))
    if result:
        stats["embeddings"]["dimension"] = result[0]['dim']

    # 10. Content length stats
    query = f"""
    SELECT
        d.type as chunk_type,
        AVG(LENGTH(d.content)) as avg_len,
        MIN(LENGTH(d.content)) as min_len,
        MAX(LENGTH(d.content)) as max_len
    FROM `{bucket_name}`._default._default d
    WHERE d.content IS NOT MISSING
    GROUP BY d.type
    ORDER BY avg_len DESC
    """
    result = list(cluster.query(query))
    stats["content_lengths"] = {}
    for row in result:
        ct = row.get('chunk_type', 'unknown')
        stats["content_lengths"][ct] = {
            "avg": row.get('avg_len', 0) or 0,
            "min": row.get('min_len', 0) or 0,
            "max": row.get('max_len', 0) or 0
        }

    # 11. Symbol type distribution
    query = f"""
    SELECT
        d.symbol_type,
        COUNT(*) as cnt
    FROM `{bucket_name}`._default._default d
    WHERE d.type = "symbol_index"
    GROUP BY d.symbol_type
    ORDER BY cnt DESC
    """
    result = list(cluster.query(query))
    stats["symbol_types"] = {
        (row.get('symbol_type') or 'unknown'): row['cnt']
        for row in result
    }

    return stats


def print_stats(stats: dict):
    """Print statistics in human-readable format"""
    total = stats["total_documents"]

    print("=" * 70)
    print("CODE_KOSHA DATABASE STATISTICS")
    print("=" * 70)

    print(f"\n📊 TOTAL DOCUMENTS: {total:,}")

    # Document types
    print("\n" + "-" * 50)
    print("📁 DOCUMENT TYPES")
    print("-" * 50)
    for doc_type, count in stats["document_types"].items():
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {doc_type:<20} {count:>8,} ({pct:>5.1f}%)")

    # Schema versions
    print("\n" + "-" * 50)
    print("📋 SCHEMA VERSIONS")
    print("-" * 50)
    for version, count in stats["schema_versions"].items():
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {version:<20} {count:>8,} ({pct:>5.1f}%)")

    # Enrichment levels
    print("\n" + "-" * 50)
    print("🔬 ENRICHMENT LEVELS (V3 only)")
    print("-" * 50)
    for level, count in stats["enrichment_levels"].items():
        print(f"  {level:<20} {count:>8,}")

    # Languages
    print("\n" + "-" * 50)
    print("💻 LANGUAGE DISTRIBUTION")
    print("-" * 50)
    for lang, count in stats["languages"].items():
        lang_name = lang if lang else "unknown"
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {lang_name:<20} {count:>8,} ({pct:>5.1f}%)")

    # Repositories
    print("\n" + "-" * 50)
    print(f"📦 REPOSITORY DISTRIBUTION (top 20 of {stats['total_repositories']})")
    print("-" * 50)
    for repo, count in list(stats["repositories"].items())[:15]:
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {repo:<40} {count:>8,} ({pct:>5.1f}%)")

    # V3 breakdown
    print("\n" + "-" * 50)
    print("🏗️ V3 CHUNK BREAKDOWN")
    print("-" * 50)
    for chunk_type, data in stats["v3_breakdown"].items():
        print(f"  {chunk_type}:")
        print(f"    Count: {data['count']:,}")
        if data.get("avg_lines"):
            print(f"    Avg lines: {data['avg_lines']:.1f}")
        if data.get("underchunked"):
            print(f"    Underchunked: {data['underchunked']:,}")

    # Embeddings
    print("\n" + "-" * 50)
    print("🧮 EMBEDDING STATISTICS")
    print("-" * 50)
    emb = stats["embeddings"]
    with_pct = (emb["with_embedding"] / total * 100) if total > 0 else 0
    without_pct = (emb["without_embedding"] / total * 100) if total > 0 else 0
    print(f"  With embeddings:    {emb['with_embedding']:>10,} ({with_pct:.1f}%)")
    print(f"  Without embeddings: {emb['without_embedding']:>10,} ({without_pct:.1f}%)")
    if "dimension" in emb:
        print(f"  Embedding dimension: {emb['dimension']}")

    # Content lengths
    print("\n" + "-" * 50)
    print("📏 CONTENT LENGTH STATS (by type)")
    print("-" * 50)
    for chunk_type, data in stats["content_lengths"].items():
        print(f"  {chunk_type}:")
        print(f"    Avg: {data['avg']:,.0f} chars | Min: {data['min']:,} | Max: {data['max']:,}")

    # Symbol types
    print("\n" + "-" * 50)
    print("🔣 SYMBOL TYPES (symbol_index)")
    print("-" * 50)
    for symbol_type, count in stats["symbol_types"].items():
        print(f"  {symbol_type:<20} {count:>8,}")

    # Summary
    print("\n" + "-" * 50)
    print("📊 SUMMARY")
    print("-" * 50)
    print(f"  Total repositories: {stats['total_repositories']}")
    print(f"  Total documents: {total:,}")

    v3_count = sum(d["count"] for d in stats["v3_breakdown"].values())
    legacy_count = total - v3_count
    print(f"\n  V3 chunks: {v3_count:,} ({v3_count/total*100:.1f}%)")
    print(f"  Legacy chunks: {legacy_count:,} ({legacy_count/total*100:.1f}%)")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Analyze Code Kosha database statistics")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--bucket", default=None, help="Bucket name (default: from .env)")
    args = parser.parse_args()

    load_env()
    bucket_name = args.bucket or os.getenv("COUCHBASE_BUCKET", "code_kosha")

    print(f"Connecting to Couchbase...", file=sys.stderr)
    cluster = get_cluster()

    print(f"Querying bucket: {bucket_name}", file=sys.stderr)
    stats = query_stats(cluster, bucket_name)

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print_stats(stats)


if __name__ == "__main__":
    main()
