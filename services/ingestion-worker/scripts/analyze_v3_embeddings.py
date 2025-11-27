#!/usr/bin/env python3
"""
Analyze V3 embedding space after ingestion run.

Compares V3 (file_index, symbol_index with LLM summaries) to legacy chunks.
"""

import numpy as np
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
import matplotlib.pyplot as plt
from collections import Counter
import json
from datetime import datetime

from storage.couchbase_client import CouchbaseClient
from config import WorkerConfig

config = WorkerConfig()


def fetch_chunks_with_embeddings(db, bucket, chunk_types=None, schema_version=None, limit=10000):
    """Fetch chunks with embeddings from database."""

    type_filter = ""
    if chunk_types:
        type_list = ", ".join(f'"{t}"' for t in chunk_types)
        type_filter = f"AND d.type IN [{type_list}]"

    version_filter = ""
    if schema_version:
        if schema_version == "legacy":
            version_filter = "AND (d.version IS MISSING OR d.version.schema_version IS MISSING)"
        else:
            version_filter = f'AND d.version.schema_version = "{schema_version}"'

    query = f'''
    SELECT RAW d
    FROM `{bucket}`._default._default d
    WHERE d.embedding IS NOT MISSING
      AND ARRAY_LENGTH(d.embedding) > 0
      {type_filter}
      {version_filter}
    LIMIT {limit}
    '''

    result = list(db.cluster.query(query))
    return result


def analyze_embedding_space(chunks, label="Dataset"):
    """Analyze embedding space structure."""

    embeddings = []
    metadata = []

    for chunk in chunks:
        emb = chunk.get("embedding")
        if emb and len(emb) > 0:
            embeddings.append(emb)
            metadata.append({
                "chunk_id": chunk.get("chunk_id", "")[:16],
                "type": chunk.get("type", "unknown"),
                "repo_id": chunk.get("repo_id", "unknown"),
                "file_path": chunk.get("file_path", ""),
                "language": chunk.get("metadata", {}).get("language", "unknown"),
                "enrichment": chunk.get("version", {}).get("enrichment_level", "unknown"),
                "content_preview": chunk.get("content", "")[:100]
            })

    if len(embeddings) < 100:
        print(f"Not enough embeddings ({len(embeddings)}) for analysis")
        return None

    X = np.array(embeddings)
    print(f"\n=== {label} Analysis ===")
    print(f"Chunks: {len(X)}")
    print(f"Embedding dim: {X.shape[1]}")

    # PCA
    pca = PCA(n_components=min(20, X.shape[1]))
    X_pca = pca.fit_transform(X)

    var_explained = pca.explained_variance_ratio_
    cumulative_var = np.cumsum(var_explained)

    print(f"\nPCA Variance Explained:")
    print(f"  PC1: {var_explained[0]:.1%}")
    print(f"  PC2: {var_explained[1]:.1%}")
    print(f"  PC1+PC2: {cumulative_var[1]:.1%}")
    print(f"  Top 5: {cumulative_var[4]:.1%}")
    print(f"  Top 10: {cumulative_var[9]:.1%}")

    # Clustering
    n_clusters = 15
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    cluster_labels = kmeans.fit_predict(X)

    sil_score = silhouette_score(X, cluster_labels, sample_size=min(5000, len(X)))
    print(f"\nClustering (k={n_clusters}):")
    print(f"  Silhouette score: {sil_score:.4f}")

    # Cluster composition
    print(f"\nCluster Composition:")
    for c in range(n_clusters):
        mask = cluster_labels == c
        cluster_size = mask.sum()
        cluster_meta = [metadata[i] for i in range(len(metadata)) if mask[i]]

        types = Counter(m["type"] for m in cluster_meta)
        languages = Counter(m["language"] for m in cluster_meta)
        enrichments = Counter(m["enrichment"] for m in cluster_meta)

        dominant_type = types.most_common(1)[0] if types else ("?", 0)
        dominant_lang = languages.most_common(1)[0] if languages else ("?", 0)

        print(f"  Cluster {c}: {cluster_size} chunks")
        print(f"    Type: {dominant_type[0]} ({dominant_type[1]})")
        print(f"    Lang: {dominant_lang[0]} ({dominant_lang[1]})")

    return {
        "X_pca": X_pca,
        "cluster_labels": cluster_labels,
        "metadata": metadata,
        "pca": pca,
        "var_explained": var_explained,
        "silhouette": sil_score
    }


def compare_v3_vs_legacy(db, bucket):
    """Compare V3 embeddings to legacy embeddings."""

    print("\n" + "="*60)
    print("FETCHING DATA...")
    print("="*60)

    # Fetch V3 chunks
    v3_chunks = fetch_chunks_with_embeddings(
        db, bucket,
        chunk_types=["file_index", "symbol_index"],
        schema_version="v3.0",
        limit=10000
    )
    print(f"V3 chunks fetched: {len(v3_chunks)}")

    # Fetch legacy chunks for comparison
    legacy_chunks = fetch_chunks_with_embeddings(
        db, bucket,
        chunk_types=["code_chunk", "document", "commit"],
        schema_version="legacy",
        limit=10000
    )
    print(f"Legacy chunks fetched: {len(legacy_chunks)}")

    # Analyze V3
    v3_analysis = analyze_embedding_space(v3_chunks, "V3 (file_index + symbol_index)")

    # Analyze legacy
    legacy_analysis = analyze_embedding_space(legacy_chunks, "Legacy (code_chunk + document)")

    # Generate comparison plot
    if v3_analysis and legacy_analysis:
        fig, axes = plt.subplots(2, 2, figsize=(16, 14))

        # V3 by cluster
        ax1 = axes[0, 0]
        scatter = ax1.scatter(
            v3_analysis["X_pca"][:, 0],
            v3_analysis["X_pca"][:, 1],
            c=v3_analysis["cluster_labels"],
            cmap="tab20",
            alpha=0.6,
            s=20
        )
        ax1.set_xlabel("PC1")
        ax1.set_ylabel("PC2")
        ax1.set_title(f"V3 Embedding Space by Cluster (n={len(v3_chunks)})\nSilhouette: {v3_analysis['silhouette']:.3f}")

        # V3 by type
        ax2 = axes[0, 1]
        types = [m["type"] for m in v3_analysis["metadata"]]
        type_map = {t: i for i, t in enumerate(set(types))}
        type_colors = [type_map[t] for t in types]
        scatter2 = ax2.scatter(
            v3_analysis["X_pca"][:, 0],
            v3_analysis["X_pca"][:, 1],
            c=type_colors,
            cmap="Set1",
            alpha=0.6,
            s=20
        )
        ax2.set_xlabel("PC1")
        ax2.set_ylabel("PC2")
        ax2.set_title("V3 Embedding Space by Type")
        # Add legend
        for t, i in type_map.items():
            ax2.scatter([], [], c=[plt.cm.Set1(i/len(type_map))], label=t)
        ax2.legend()

        # Legacy by cluster
        ax3 = axes[1, 0]
        scatter3 = ax3.scatter(
            legacy_analysis["X_pca"][:, 0],
            legacy_analysis["X_pca"][:, 1],
            c=legacy_analysis["cluster_labels"],
            cmap="tab20",
            alpha=0.6,
            s=20
        )
        ax3.set_xlabel("PC1")
        ax3.set_ylabel("PC2")
        ax3.set_title(f"Legacy Embedding Space by Cluster (n={len(legacy_chunks)})\nSilhouette: {legacy_analysis['silhouette']:.3f}")

        # Legacy by type
        ax4 = axes[1, 1]
        types_leg = [m["type"] for m in legacy_analysis["metadata"]]
        type_map_leg = {t: i for i, t in enumerate(set(types_leg))}
        type_colors_leg = [type_map_leg[t] for t in types_leg]
        scatter4 = ax4.scatter(
            legacy_analysis["X_pca"][:, 0],
            legacy_analysis["X_pca"][:, 1],
            c=type_colors_leg,
            cmap="Set1",
            alpha=0.6,
            s=20
        )
        ax4.set_xlabel("PC1")
        ax4.set_ylabel("PC2")
        ax4.set_title("Legacy Embedding Space by Type")
        for t, i in type_map_leg.items():
            ax4.scatter([], [], c=[plt.cm.Set1(i/len(type_map_leg))], label=t)
        ax4.legend()

        plt.tight_layout()
        plt.savefig("embedding_v3_vs_legacy.png", dpi=150)
        print(f"\nPlot saved to: embedding_v3_vs_legacy.png")

    # Summary comparison
    print("\n" + "="*60)
    print("COMPARISON SUMMARY")
    print("="*60)

    if v3_analysis and legacy_analysis:
        print(f"\n{'Metric':<30} {'V3':>15} {'Legacy':>15}")
        print("-"*60)
        print(f"{'Chunks analyzed':<30} {len(v3_chunks):>15} {len(legacy_chunks):>15}")
        print(f"{'PC1 variance':<30} {v3_analysis['var_explained'][0]:>14.1%} {legacy_analysis['var_explained'][0]:>14.1%}")
        print(f"{'PC1+PC2 variance':<30} {sum(v3_analysis['var_explained'][:2]):>14.1%} {sum(legacy_analysis['var_explained'][:2]):>14.1%}")
        print(f"{'Silhouette score':<30} {v3_analysis['silhouette']:>15.4f} {legacy_analysis['silhouette']:>15.4f}")

        # Improvement
        sil_improvement = (v3_analysis['silhouette'] - legacy_analysis['silhouette']) / abs(legacy_analysis['silhouette']) * 100
        print(f"\nSilhouette improvement: {sil_improvement:+.1f}%")

    return v3_analysis, legacy_analysis


def analyze_v3_quality(db, bucket):
    """Analyze V3 specific quality metrics."""

    print("\n" + "="*60)
    print("V3 QUALITY METRICS")
    print("="*60)

    # Enrichment level distribution
    query = f'''
    SELECT
        IFMISSING(d.version.enrichment_level, "unknown") as level,
        COUNT(*) as count
    FROM `{bucket}`._default._default d
    WHERE d.version.schema_version = "v3.0"
    GROUP BY IFMISSING(d.version.enrichment_level, "unknown")
    '''
    result = list(db.cluster.query(query))
    print("\nEnrichment Levels:")
    for row in result:
        print(f"  {row['level']}: {row['count']}")

    # Underchunked files
    query = f'''
    SELECT COUNT(*) as count
    FROM `{bucket}`._default._default d
    WHERE d.type = "file_index"
      AND d.metadata.is_underchunked = true
    '''
    result = list(db.cluster.query(query))
    underchunked = result[0]["count"] if result else 0

    query = f'''
    SELECT COUNT(*) as count
    FROM `{bucket}`._default._default d
    WHERE d.type = "file_index"
    '''
    result = list(db.cluster.query(query))
    total_files = result[0]["count"] if result else 0

    print(f"\nUnderchunked files: {underchunked}/{total_files} ({underchunked/total_files*100:.1f}%)")

    # Language distribution in V3
    query = f'''
    SELECT
        IFMISSING(d.metadata.language, "unknown") as language,
        COUNT(*) as count
    FROM `{bucket}`._default._default d
    WHERE d.type = "file_index"
    GROUP BY IFMISSING(d.metadata.language, "unknown")
    ORDER BY count DESC
    LIMIT 15
    '''
    result = list(db.cluster.query(query))
    print("\nLanguage Distribution (file_index):")
    for row in result:
        print(f"  {row['language']}: {row['count']}")

    # Repos processed with V3
    query = f'''
    SELECT DISTINCT d.repo_id
    FROM `{bucket}`._default._default d
    WHERE d.version.schema_version = "v3.0"
    '''
    result = list(db.cluster.query(query))
    repos = [r["repo_id"] for r in result]
    print(f"\nRepos with V3 data: {len(repos)}")
    for repo in repos[:10]:
        print(f"  - {repo}")
    if len(repos) > 10:
        print(f"  ... and {len(repos) - 10} more")


if __name__ == "__main__":
    db = CouchbaseClient()
    bucket = config.couchbase_bucket

    # Run comparison
    v3_analysis, legacy_analysis = compare_v3_vs_legacy(db, bucket)

    # V3 specific quality
    analyze_v3_quality(db, bucket)

    # Save results
    results = {
        "timestamp": datetime.now().isoformat(),
        "v3": {
            "chunks": len(v3_analysis["metadata"]) if v3_analysis else 0,
            "silhouette": float(v3_analysis["silhouette"]) if v3_analysis else 0,
            "pc1_variance": float(v3_analysis["var_explained"][0]) if v3_analysis else 0,
            "pc2_variance": float(v3_analysis["var_explained"][1]) if v3_analysis else 0,
        },
        "legacy": {
            "chunks": len(legacy_analysis["metadata"]) if legacy_analysis else 0,
            "silhouette": float(legacy_analysis["silhouette"]) if legacy_analysis else 0,
            "pc1_variance": float(legacy_analysis["var_explained"][0]) if legacy_analysis else 0,
            "pc2_variance": float(legacy_analysis["var_explained"][1]) if legacy_analysis else 0,
        }
    }

    with open("embedding_analysis_v3.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: embedding_analysis_v3.json")
