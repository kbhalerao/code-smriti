#!/usr/bin/env python3
"""
V4 Embedding Space Analysis - Run as script, save plots to files.
"""

import numpy as np
import pandas as pd
from pathlib import Path
import sys

# Visualization
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.decomposition import PCA
from sklearn.metrics.pairwise import cosine_similarity

# Couchbase
sys.path.insert(0, str(Path(__file__).parent))
from storage.couchbase_client import CouchbaseClient

OUTPUT_DIR = Path(__file__).parent / "analysis_output"
OUTPUT_DIR.mkdir(exist_ok=True)

plt.style.use('seaborn-v0_8-whitegrid')


def fetch_embeddings(cb, doc_type: str, limit: int = 5000) -> pd.DataFrame:
    """Fetch embeddings for a document type."""
    query = f"""
        SELECT
            META().id as doc_id,
            repo_id,
            CASE
                WHEN type = 'file_index' THEN file_path
                WHEN type = 'symbol_index' THEN file_path
                WHEN type = 'module_summary' THEN module_path
                ELSE repo_id
            END as path,
            CASE
                WHEN type = 'file_index' THEN metadata.language
                WHEN type = 'symbol_index' THEN metadata.language
                ELSE 'summary'
            END as language,
            embedding
        FROM `code_kosha`
        WHERE type = '{doc_type}'
          AND embedding IS NOT NULL
        LIMIT {limit}
    """

    results = list(cb.cluster.query(query))

    rows = []
    for r in results:
        if r.get('embedding'):
            rows.append({
                'doc_id': r['doc_id'],
                'repo_id': r['repo_id'],
                'path': r['path'],
                'language': r.get('language', 'unknown'),
                'type': doc_type,
                'embedding': np.array(r['embedding'])
            })

    return pd.DataFrame(rows)


def main():
    print("=" * 60)
    print("V4 EMBEDDING SPACE ANALYSIS")
    print("=" * 60)

    cb = CouchbaseClient()

    # 1. Load embeddings
    print("\n📥 Fetching embeddings...")
    df_file = fetch_embeddings(cb, 'file_index', 3000)
    df_symbol = fetch_embeddings(cb, 'symbol_index', 3000)
    df_module = fetch_embeddings(cb, 'module_summary', 1000)
    df_repo = fetch_embeddings(cb, 'repo_summary', 500)

    print(f"   file_index: {len(df_file)}")
    print(f"   symbol_index: {len(df_symbol)}")
    print(f"   module_summary: {len(df_module)}")
    print(f"   repo_summary: {len(df_repo)}")

    df_all = pd.concat([df_file, df_symbol, df_module, df_repo], ignore_index=True)
    print(f"   Total: {len(df_all)}")

    if len(df_all) == 0:
        print("No embeddings found!")
        return

    embeddings = np.vstack(df_all['embedding'].values)
    print(f"   Embedding dim: {embeddings.shape[1]}")

    # 2. PCA Analysis
    print("\n📊 PCA Analysis...")
    pca_full = PCA(n_components=min(100, embeddings.shape[1]))
    pca_full.fit(embeddings)

    cumvar = np.cumsum(pca_full.explained_variance_ratio_)
    n_90 = np.argmax(cumvar >= 0.9) + 1
    n_95 = np.argmax(cumvar >= 0.95) + 1
    print(f"   Components for 90% variance: {n_90}")
    print(f"   Components for 95% variance: {n_95}")

    # Plot variance
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    axes[0].plot(cumvar, 'b-', linewidth=2)
    axes[0].axhline(y=0.9, color='r', linestyle='--', label='90%')
    axes[0].axhline(y=0.95, color='orange', linestyle='--', label='95%')
    axes[0].set_xlabel('Components')
    axes[0].set_ylabel('Cumulative Variance')
    axes[0].set_title('PCA: Cumulative Variance Explained')
    axes[0].legend()

    axes[1].bar(range(30), pca_full.explained_variance_ratio_[:30])
    axes[1].set_xlabel('Component')
    axes[1].set_ylabel('Variance Ratio')
    axes[1].set_title('Variance per Component (first 30)')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / '1_pca_variance.png', dpi=150)
    plt.close()
    print(f"   Saved: 1_pca_variance.png")

    # 3. Document Type Separation
    print("\n🎯 Document Type Separation...")
    pca_2d = PCA(n_components=2)
    embeddings_2d = pca_2d.fit_transform(embeddings)
    df_all['pca_x'] = embeddings_2d[:, 0]
    df_all['pca_y'] = embeddings_2d[:, 1]

    fig, ax = plt.subplots(figsize=(12, 8))
    type_colors = {
        'symbol_index': 'blue',
        'file_index': 'green',
        'module_summary': 'orange',
        'repo_summary': 'red'
    }

    for doc_type, color in type_colors.items():
        mask = df_all['type'] == doc_type
        ax.scatter(df_all.loc[mask, 'pca_x'], df_all.loc[mask, 'pca_y'],
                   c=color, label=doc_type, alpha=0.5, s=20)

    ax.set_xlabel(f'PC1 ({pca_2d.explained_variance_ratio_[0]:.1%})')
    ax.set_ylabel(f'PC2 ({pca_2d.explained_variance_ratio_[1]:.1%})')
    ax.set_title('Document Type Separation (PCA)')
    ax.legend()
    plt.savefig(OUTPUT_DIR / '2_doc_type_separation.png', dpi=150)
    plt.close()
    print(f"   Saved: 2_doc_type_separation.png")

    # 4. Language Clustering
    print("\n🌐 Language Clustering...")
    df_code = df_all[df_all['type'].isin(['file_index', 'symbol_index'])].copy()
    top_langs = df_code['language'].value_counts().head(8).index.tolist()
    df_code_top = df_code[df_code['language'].isin(top_langs)]

    fig, ax = plt.subplots(figsize=(12, 8))
    lang_colors = plt.cm.tab10(np.linspace(0, 1, len(top_langs)))

    for i, lang in enumerate(top_langs):
        mask = df_code_top['language'] == lang
        ax.scatter(df_code_top.loc[mask, 'pca_x'], df_code_top.loc[mask, 'pca_y'],
                   c=[lang_colors[i]], label=lang, alpha=0.5, s=20)

    ax.set_xlabel('PC1')
    ax.set_ylabel('PC2')
    ax.set_title('Language Clustering (PCA)')
    ax.legend()
    plt.savefig(OUTPUT_DIR / '3_language_clustering.png', dpi=150)
    plt.close()
    print(f"   Saved: 3_language_clustering.png")
    print(f"   Top languages: {top_langs}")

    # 5. Intra vs Inter-Repo Similarity
    print("\n🔗 Intra vs Inter-Repo Similarity...")
    repo_counts = df_file['repo_id'].value_counts()
    sample_repos = repo_counts[repo_counts >= 20].head(10).index.tolist()

    intra_sims = []
    inter_sims = []

    for repo in sample_repos:
        repo_mask = df_file['repo_id'] == repo
        repo_embeds = np.vstack(df_file.loc[repo_mask, 'embedding'].values)

        if len(repo_embeds) > 50:
            idx = np.random.choice(len(repo_embeds), 50, replace=False)
            repo_embeds_sample = repo_embeds[idx]
        else:
            repo_embeds_sample = repo_embeds

        sim_matrix = cosine_similarity(repo_embeds_sample)
        triu_idx = np.triu_indices(len(sim_matrix), k=1)
        intra_sims.extend(sim_matrix[triu_idx].tolist())

    for i, repo1 in enumerate(sample_repos[:5]):
        for repo2 in sample_repos[i+1:6]:
            embeds1 = np.vstack(df_file.loc[df_file['repo_id'] == repo1, 'embedding'].values[:20])
            embeds2 = np.vstack(df_file.loc[df_file['repo_id'] == repo2, 'embedding'].values[:20])
            cross_sim = cosine_similarity(embeds1, embeds2)
            inter_sims.extend(cross_sim.flatten().tolist())

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(intra_sims, bins=50, alpha=0.7, label=f'Intra-repo (μ={np.mean(intra_sims):.3f})', density=True)
    ax.hist(inter_sims, bins=50, alpha=0.7, label=f'Inter-repo (μ={np.mean(inter_sims):.3f})', density=True)
    ax.set_xlabel('Cosine Similarity')
    ax.set_ylabel('Density')
    ax.set_title('File Similarity: Within vs Across Repos')
    ax.legend()
    plt.savefig(OUTPUT_DIR / '4_intra_inter_repo.png', dpi=150)
    plt.close()
    print(f"   Saved: 4_intra_inter_repo.png")
    print(f"   Intra-repo: {np.mean(intra_sims):.3f} ± {np.std(intra_sims):.3f}")
    print(f"   Inter-repo: {np.mean(inter_sims):.3f} ± {np.std(inter_sims):.3f}")

    # 6. Hierarchy Validation
    print("\n🏗️ Hierarchy Validation (Symbol → File)...")
    file_embeds = {row['path']: row['embedding'] for _, row in df_file.iterrows()}

    symbol_to_own = []
    symbol_to_rand = []
    random_files = list(file_embeds.values())

    sample_symbols = df_symbol.sample(min(500, len(df_symbol)))

    for _, sym in sample_symbols.iterrows():
        sym_embed = sym['embedding'].reshape(1, -1)
        sym_file = sym['path']

        if sym_file in file_embeds:
            own_sim = cosine_similarity(sym_embed, file_embeds[sym_file].reshape(1, -1))[0, 0]
            symbol_to_own.append(own_sim)

            rand_embed = random_files[np.random.randint(len(random_files))].reshape(1, -1)
            rand_sim = cosine_similarity(sym_embed, rand_embed)[0, 0]
            symbol_to_rand.append(rand_sim)

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(symbol_to_own, bins=50, alpha=0.7, label=f'Own file (μ={np.mean(symbol_to_own):.3f})', density=True)
    ax.hist(symbol_to_rand, bins=50, alpha=0.7, label=f'Random file (μ={np.mean(symbol_to_rand):.3f})', density=True)
    ax.set_xlabel('Cosine Similarity')
    ax.set_ylabel('Density')
    ax.set_title('Symbol → File Similarity')
    ax.legend()
    plt.savefig(OUTPUT_DIR / '5_hierarchy_validation.png', dpi=150)
    plt.close()
    print(f"   Saved: 5_hierarchy_validation.png")
    print(f"   Symbol → Own file: {np.mean(symbol_to_own):.3f}")
    print(f"   Symbol → Random: {np.mean(symbol_to_rand):.3f}")

    # 7. Cross-Repo Similarity Matrix
    print("\n📊 Cross-Repo Similarity Matrix...")
    repo_centroids = {}
    for repo in df_file['repo_id'].unique():
        repo_emb = np.vstack(df_file.loc[df_file['repo_id'] == repo, 'embedding'].values)
        repo_centroids[repo] = repo_emb.mean(axis=0)

    top_repos = repo_counts.head(12).index.tolist()
    centroid_matrix = np.vstack([repo_centroids[r] for r in top_repos])
    repo_sim_matrix = cosine_similarity(centroid_matrix)

    short_names = [r.split('/')[-1][:12] for r in top_repos]

    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(repo_sim_matrix, xticklabels=short_names, yticklabels=short_names,
                cmap='RdYlBu_r', center=0.5, annot=True, fmt='.2f', ax=ax)
    ax.set_title('Repository Similarity (Centroids)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / '6_repo_similarity_matrix.png', dpi=150)
    plt.close()
    print(f"   Saved: 6_repo_similarity_matrix.png")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total documents: {len(df_all):,}")
    print(f"Embedding dim: {embeddings.shape[1]}")
    print(f"Unique repos: {df_all['repo_id'].nunique()}")
    print(f"PCA 90% variance: {n_90} components")
    print(f"PCA 95% variance: {n_95} components")
    print(f"Intra-repo similarity: {np.mean(intra_sims):.3f}")
    print(f"Inter-repo similarity: {np.mean(inter_sims):.3f}")
    print(f"Repo coherence: {np.mean(intra_sims) - np.mean(inter_sims):.3f}")
    print(f"Hierarchy coherence: {np.mean(symbol_to_own) - np.mean(symbol_to_rand):.3f}")
    print(f"\nPlots saved to: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
