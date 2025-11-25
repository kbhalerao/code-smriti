#!/usr/bin/env python3
"""
Embedding Space Analysis Tool

Analyzes the embedding space of CodeSmriti chunks using PCA and clustering
to understand codebase diversity and identify patterns for synthetic chunks.

Usage:
    python analyze_embeddings.py --sample 5000
    python analyze_embeddings.py --repo kbhalerao/labcore
    python analyze_embeddings.py --export embeddings_analysis.json
"""

import asyncio
import argparse
import json
import numpy as np
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from loguru import logger
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans, HDBSCAN
from sklearn.metrics import silhouette_score
from sklearn.manifold import TSNE

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logger.warning("matplotlib not installed - visualizations disabled")

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions

from config import WorkerConfig

config = WorkerConfig()


@dataclass
class ClusterInfo:
    """Information about a cluster of similar chunks"""
    cluster_id: int
    size: int
    centroid: np.ndarray
    sample_chunks: List[Dict]
    avg_similarity: float
    dominant_type: str
    dominant_repo: str
    dominant_language: str
    keywords: List[str]


@dataclass
class EmbeddingAnalysis:
    """Results of embedding space analysis"""
    total_chunks: int
    embedding_dim: int

    # PCA results
    pca_variance_explained: List[float]
    effective_dimensions: int  # Dimensions needed for 90% variance

    # Clustering results
    n_clusters: int
    cluster_sizes: List[int]
    silhouette_score: float
    clusters: List[ClusterInfo]

    # Diversity metrics
    avg_pairwise_distance: float
    coverage_radius: float  # Radius containing 90% of points

    # Duplication detection
    near_duplicates: List[Tuple[str, str, float]]  # (chunk_id1, chunk_id2, similarity)
    duplicate_groups: List[List[str]]


class EmbeddingAnalyzer:
    """Analyzes embedding space for patterns and diversity"""

    def __init__(self):
        logger.info(f"Connecting to Couchbase at {config.couchbase_host}")

        connection_string = f"couchbase://{config.couchbase_host}"
        auth = PasswordAuthenticator(config.couchbase_username, config.couchbase_password)

        self.cluster = Cluster(connection_string, ClusterOptions(auth))
        self.cluster.wait_until_ready(timedelta(seconds=10))
        self.bucket = self.cluster.bucket(config.couchbase_bucket)

        logger.info("Connected to Couchbase")

    def fetch_embeddings(
        self,
        repo_id: Optional[str] = None,
        chunk_types: Optional[List[str]] = None,
        limit: int = 10000
    ) -> Tuple[np.ndarray, List[Dict]]:
        """
        Fetch embeddings and metadata from Couchbase

        Returns:
            (embeddings_array, metadata_list)
        """
        # Build query
        conditions = ["embedding IS NOT NULL", "ARRAY_LENGTH(embedding) > 0"]

        if repo_id:
            conditions.append(f"repo_id = '{repo_id}'")

        if chunk_types:
            types_str = ", ".join(f"'{t}'" for t in chunk_types)
            conditions.append(f"type IN [{types_str}]")

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT META().id as chunk_id,
                   embedding,
                   type,
                   repo_id,
                   file_path,
                   SUBSTR(content, 0, 200) as content_preview,
                   metadata.`language` as lang
            FROM `{config.couchbase_bucket}`
            WHERE {where_clause}
            LIMIT {limit}
        """

        logger.info(f"Fetching embeddings (limit={limit})...")
        result = self.cluster.query(query)

        embeddings = []
        metadata = []

        for row in result:
            if row.get("embedding"):
                embeddings.append(row["embedding"])
                metadata.append({
                    "chunk_id": row["chunk_id"],
                    "type": row.get("type", "unknown"),
                    "repo_id": row.get("repo_id", "unknown"),
                    "file_path": row.get("file_path", ""),
                    "content_preview": row.get("content_preview", ""),
                    "language": row.get("lang", "unknown")
                })

        logger.info(f"Fetched {len(embeddings)} embeddings")

        return np.array(embeddings), metadata

    def analyze_pca(self, embeddings: np.ndarray) -> Dict[str, Any]:
        """Perform PCA analysis on embeddings"""
        logger.info("Running PCA analysis...")

        # Full PCA
        n_components = min(50, embeddings.shape[1], embeddings.shape[0])
        pca = PCA(n_components=n_components)
        pca.fit(embeddings)

        # Cumulative variance explained
        cumsum = np.cumsum(pca.explained_variance_ratio_)

        # Find effective dimensionality (90% variance)
        effective_dim = np.argmax(cumsum >= 0.9) + 1

        # Project to 2D for visualization
        pca_2d = PCA(n_components=2)
        embeddings_2d = pca_2d.fit_transform(embeddings)

        return {
            "variance_explained": pca.explained_variance_ratio_.tolist(),
            "cumulative_variance": cumsum.tolist(),
            "effective_dimensions": int(effective_dim),
            "embeddings_2d": embeddings_2d,
            "total_variance_2d": float(sum(pca_2d.explained_variance_ratio_))
        }

    def analyze_clusters(
        self,
        embeddings: np.ndarray,
        metadata: List[Dict],
        n_clusters: Optional[int] = None
    ) -> Dict[str, Any]:
        """Perform clustering analysis"""
        logger.info("Running clustering analysis...")

        # Auto-detect number of clusters if not specified
        if n_clusters is None:
            # Try different cluster counts and pick best silhouette
            best_score = -1
            best_n = 5

            for n in [5, 10, 15, 20, 30]:
                if n >= len(embeddings):
                    continue
                kmeans = KMeans(n_clusters=n, random_state=42, n_init=10)
                labels = kmeans.fit_predict(embeddings)
                score = silhouette_score(embeddings, labels, sample_size=min(5000, len(embeddings)))

                if score > best_score:
                    best_score = score
                    best_n = n

            n_clusters = best_n
            logger.info(f"Auto-selected {n_clusters} clusters (silhouette={best_score:.3f})")

        # Final clustering
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(embeddings)

        # Analyze each cluster
        clusters = []
        for cluster_id in range(n_clusters):
            mask = labels == cluster_id
            cluster_embeddings = embeddings[mask]
            cluster_metadata = [m for m, is_in in zip(metadata, mask) if is_in]

            # Calculate average intra-cluster similarity
            if len(cluster_embeddings) > 1:
                centroid = kmeans.cluster_centers_[cluster_id]
                distances = np.linalg.norm(cluster_embeddings - centroid, axis=1)
                avg_similarity = 1 / (1 + np.mean(distances))
            else:
                avg_similarity = 1.0

            # Find dominant attributes
            types = [m["type"] for m in cluster_metadata]
            repos = [m["repo_id"] for m in cluster_metadata]
            languages = [m.get("language", "unknown") for m in cluster_metadata]

            dominant_type = max(set(types), key=types.count) if types else "unknown"
            dominant_repo = max(set(repos), key=repos.count) if repos else "unknown"
            dominant_language = max(set(languages), key=languages.count) if languages else "unknown"

            # Extract keywords from content previews
            all_content = " ".join(m.get("content_preview", "") for m in cluster_metadata[:100])
            words = [w.lower() for w in all_content.split() if len(w) > 3 and w.isalpha()]
            word_freq = defaultdict(int)
            for w in words:
                word_freq[w] += 1
            keywords = sorted(word_freq.keys(), key=lambda x: -word_freq[x])[:10]

            clusters.append(ClusterInfo(
                cluster_id=cluster_id,
                size=len(cluster_metadata),
                centroid=kmeans.cluster_centers_[cluster_id],
                sample_chunks=cluster_metadata[:5],
                avg_similarity=avg_similarity,
                dominant_type=dominant_type,
                dominant_repo=dominant_repo,
                dominant_language=dominant_language,
                keywords=keywords
            ))

        # Sort clusters by size
        clusters.sort(key=lambda c: -c.size)

        return {
            "n_clusters": n_clusters,
            "labels": labels,
            "silhouette_score": float(silhouette_score(embeddings, labels, sample_size=min(5000, len(embeddings)))),
            "clusters": clusters,
            "cluster_sizes": [c.size for c in clusters]
        }

    def find_near_duplicates(
        self,
        embeddings: np.ndarray,
        metadata: List[Dict],
        threshold: float = 0.95
    ) -> List[Tuple[str, str, float]]:
        """
        Find near-duplicate chunks based on embedding similarity

        Uses cosine similarity with a high threshold
        """
        logger.info("Finding near-duplicates...")

        # Normalize embeddings for cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / (norms + 1e-10)

        duplicates = []

        # Sample for efficiency if too many embeddings
        sample_size = min(3000, len(embeddings))
        if sample_size < len(embeddings):
            indices = np.random.choice(len(embeddings), sample_size, replace=False)
            normalized_sample = normalized[indices]
            metadata_sample = [metadata[i] for i in indices]
        else:
            normalized_sample = normalized
            metadata_sample = metadata

        # Compute pairwise similarities (upper triangle only)
        for i in range(len(normalized_sample)):
            for j in range(i + 1, min(i + 100, len(normalized_sample))):  # Limit comparisons
                sim = np.dot(normalized_sample[i], normalized_sample[j])
                if sim >= threshold:
                    duplicates.append((
                        metadata_sample[i]["chunk_id"],
                        metadata_sample[j]["chunk_id"],
                        float(sim)
                    ))

        logger.info(f"Found {len(duplicates)} near-duplicate pairs")
        return sorted(duplicates, key=lambda x: -x[2])[:100]  # Top 100

    def calculate_diversity_metrics(self, embeddings: np.ndarray) -> Dict[str, float]:
        """Calculate overall diversity metrics"""
        logger.info("Calculating diversity metrics...")

        # Sample for efficiency
        sample_size = min(2000, len(embeddings))
        if sample_size < len(embeddings):
            indices = np.random.choice(len(embeddings), sample_size, replace=False)
            sample = embeddings[indices]
        else:
            sample = embeddings

        # Average pairwise distance (sampled)
        distances = []
        for i in range(min(500, len(sample))):
            for j in range(i + 1, min(i + 50, len(sample))):
                distances.append(np.linalg.norm(sample[i] - sample[j]))

        avg_distance = np.mean(distances) if distances else 0

        # Coverage radius (distance from centroid containing 90% of points)
        centroid = np.mean(embeddings, axis=0)
        distances_from_center = np.linalg.norm(embeddings - centroid, axis=1)
        coverage_radius = np.percentile(distances_from_center, 90)

        return {
            "avg_pairwise_distance": float(avg_distance),
            "coverage_radius": float(coverage_radius),
            "centroid_spread": float(np.std(distances_from_center))
        }

    def run_full_analysis(
        self,
        repo_id: Optional[str] = None,
        sample_size: int = 10000,
        n_clusters: Optional[int] = None
    ) -> EmbeddingAnalysis:
        """Run complete embedding space analysis"""

        # Fetch data
        embeddings, metadata = self.fetch_embeddings(
            repo_id=repo_id,
            limit=sample_size
        )

        if len(embeddings) < 10:
            raise ValueError(f"Not enough embeddings found: {len(embeddings)}")

        # Run analyses
        pca_results = self.analyze_pca(embeddings)
        cluster_results = self.analyze_clusters(embeddings, metadata, n_clusters)
        duplicates = self.find_near_duplicates(embeddings, metadata)
        diversity = self.calculate_diversity_metrics(embeddings)

        return EmbeddingAnalysis(
            total_chunks=len(embeddings),
            embedding_dim=embeddings.shape[1],
            pca_variance_explained=pca_results["variance_explained"],
            effective_dimensions=pca_results["effective_dimensions"],
            n_clusters=cluster_results["n_clusters"],
            cluster_sizes=cluster_results["cluster_sizes"],
            silhouette_score=cluster_results["silhouette_score"],
            clusters=cluster_results["clusters"],
            avg_pairwise_distance=diversity["avg_pairwise_distance"],
            coverage_radius=diversity["coverage_radius"],
            near_duplicates=duplicates,
            duplicate_groups=[]  # TODO: Group duplicates into connected components
        )

    def visualize(
        self,
        embeddings: np.ndarray,
        metadata: List[Dict],
        labels: np.ndarray,
        output_path: str = "embedding_space.png"
    ):
        """Create visualization of embedding space"""
        if not HAS_MATPLOTLIB:
            logger.warning("matplotlib not available for visualization")
            return

        logger.info("Creating visualization...")

        # PCA to 2D
        pca = PCA(n_components=2)
        coords = pca.fit_transform(embeddings)

        fig, axes = plt.subplots(1, 2, figsize=(16, 7))

        # Plot 1: By cluster
        scatter1 = axes[0].scatter(
            coords[:, 0], coords[:, 1],
            c=labels, cmap='tab20', alpha=0.6, s=10
        )
        axes[0].set_title(f"Embedding Space by Cluster (n={len(set(labels))})")
        axes[0].set_xlabel("PC1")
        axes[0].set_ylabel("PC2")

        # Plot 2: By repo
        repos = [m["repo_id"] for m in metadata]
        unique_repos = list(set(repos))[:20]  # Top 20 repos
        repo_to_idx = {r: i for i, r in enumerate(unique_repos)}
        repo_colors = [repo_to_idx.get(r, -1) for r in repos]

        scatter2 = axes[1].scatter(
            coords[:, 0], coords[:, 1],
            c=repo_colors, cmap='tab20', alpha=0.6, s=10
        )
        axes[1].set_title("Embedding Space by Repository")
        axes[1].set_xlabel("PC1")
        axes[1].set_ylabel("PC2")

        plt.tight_layout()
        plt.savefig(output_path, dpi=150)
        logger.info(f"Saved visualization to {output_path}")


def print_analysis_report(analysis: EmbeddingAnalysis):
    """Pretty print analysis results"""
    print("\n" + "=" * 80)
    print("CODESMRITI EMBEDDING SPACE ANALYSIS")
    print("=" * 80)

    print(f"\n📊 OVERVIEW")
    print(f"   Total chunks analyzed: {analysis.total_chunks:,}")
    print(f"   Embedding dimension: {analysis.embedding_dim}")

    print(f"\n🔬 PCA ANALYSIS")
    print(f"   Effective dimensions (90% variance): {analysis.effective_dimensions}")
    print(f"   Top 5 components explain: {sum(analysis.pca_variance_explained[:5])*100:.1f}% of variance")
    print(f"   Top 10 components explain: {sum(analysis.pca_variance_explained[:10])*100:.1f}% of variance")

    print(f"\n🎯 CLUSTERING")
    print(f"   Number of clusters: {analysis.n_clusters}")
    print(f"   Silhouette score: {analysis.silhouette_score:.3f}")
    print(f"   Cluster sizes: {analysis.cluster_sizes[:10]}...")

    print(f"\n📦 TOP CLUSTERS")
    for i, cluster in enumerate(analysis.clusters[:5]):
        print(f"\n   Cluster {i+1} ({cluster.size} chunks)")
        print(f"   - Dominant type: {cluster.dominant_type}")
        print(f"   - Dominant repo: {cluster.dominant_repo}")
        print(f"   - Dominant language: {cluster.dominant_language}")
        print(f"   - Keywords: {', '.join(cluster.keywords[:5])}")
        print(f"   - Avg similarity: {cluster.avg_similarity:.3f}")

    print(f"\n🌐 DIVERSITY METRICS")
    print(f"   Average pairwise distance: {analysis.avg_pairwise_distance:.3f}")
    print(f"   90% coverage radius: {analysis.coverage_radius:.3f}")

    print(f"\n🔄 NEAR-DUPLICATES")
    print(f"   Found {len(analysis.near_duplicates)} near-duplicate pairs")
    if analysis.near_duplicates:
        print(f"   Top duplicates:")
        for chunk1, chunk2, sim in analysis.near_duplicates[:5]:
            print(f"   - {chunk1[:16]}... <-> {chunk2[:16]}... (sim={sim:.3f})")

    print("\n" + "=" * 80)


async def main():
    parser = argparse.ArgumentParser(description="Analyze CodeSmriti embedding space")
    parser.add_argument("--repo", type=str, help="Analyze specific repository")
    parser.add_argument("--sample", type=int, default=10000, help="Sample size")
    parser.add_argument("--clusters", type=int, help="Number of clusters (auto if not set)")
    parser.add_argument("--export", type=str, help="Export results to JSON file")
    parser.add_argument("--visualize", type=str, help="Save visualization to file")
    args = parser.parse_args()

    analyzer = EmbeddingAnalyzer()

    analysis = analyzer.run_full_analysis(
        repo_id=args.repo,
        sample_size=args.sample,
        n_clusters=args.clusters
    )

    print_analysis_report(analysis)

    if args.export:
        # Convert to JSON-serializable format
        export_data = {
            "total_chunks": analysis.total_chunks,
            "embedding_dim": analysis.embedding_dim,
            "effective_dimensions": analysis.effective_dimensions,
            "pca_variance_explained": analysis.pca_variance_explained[:20],
            "n_clusters": analysis.n_clusters,
            "silhouette_score": analysis.silhouette_score,
            "cluster_sizes": analysis.cluster_sizes,
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "size": c.size,
                    "dominant_type": c.dominant_type,
                    "dominant_repo": c.dominant_repo,
                    "dominant_language": c.dominant_language,
                    "keywords": c.keywords,
                    "avg_similarity": c.avg_similarity,
                    "sample_chunks": c.sample_chunks
                }
                for c in analysis.clusters
            ],
            "diversity": {
                "avg_pairwise_distance": analysis.avg_pairwise_distance,
                "coverage_radius": analysis.coverage_radius
            },
            "near_duplicates": [
                {"chunk1": c1, "chunk2": c2, "similarity": s}
                for c1, c2, s in analysis.near_duplicates[:50]
            ]
        }

        with open(args.export, "w") as f:
            json.dump(export_data, f, indent=2)
        print(f"\n📄 Exported to {args.export}")

    if args.visualize:
        embeddings, metadata = analyzer.fetch_embeddings(
            repo_id=args.repo,
            limit=args.sample
        )
        cluster_results = analyzer.analyze_clusters(embeddings, metadata, args.clusters)
        analyzer.visualize(embeddings, metadata, cluster_results["labels"], args.visualize)


if __name__ == "__main__":
    asyncio.run(main())
