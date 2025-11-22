#!/usr/bin/env python3
"""
Compare files between kbhalerao/labcore and ContinuumAgInc/topsoil2.0
to find exact matches and verify chunk counts/embeddings
"""
from app.database.couchbase_client import CouchbaseClient
from collections import defaultdict

def main():
    print("=" * 70)
    print("COMPARING FILES BETWEEN kbhalerao/labcore AND topsoil2.0")
    print("=" * 70)

    db = CouchbaseClient()

    # Find all files in both repos
    query = """
        SELECT repo_id, file_path, COUNT(*) as chunk_count
        FROM `code_kosha`
        WHERE type='code_chunk'
          AND repo_id IN ['kbhalerao/labcore', 'ContinuumAgInc/topsoil2.0']
        GROUP BY repo_id, file_path
        ORDER BY file_path, repo_id
    """

    result = db.cluster.query(query)
    rows = list(result)

    # Group by file_path
    files_by_path = defaultdict(list)
    for row in rows:
        files_by_path[row['file_path']].append({
            'repo': row['repo_id'],
            'chunks': row['chunk_count']
        })

    # Find files that exist in both repos
    print(f"\nFound {len(files_by_path)} unique file paths")

    matching_files = []
    for file_path, repos in files_by_path.items():
        if len(repos) == 2:
            # Check if chunk counts match
            labcore = next((r for r in repos if 'labcore' in r['repo']), None)
            topsoil = next((r for r in repos if 'topsoil' in r['repo']), None)

            if labcore and topsoil:
                matching_files.append({
                    'file': file_path,
                    'labcore_chunks': labcore['chunks'],
                    'topsoil_chunks': topsoil['chunks'],
                    'chunks_match': labcore['chunks'] == topsoil['chunks']
                })

    print(f"\nFiles existing in BOTH repos: {len(matching_files)}")

    # Show files with matching chunk counts
    exact_matches = [f for f in matching_files if f['chunks_match']]
    print(f"Files with SAME chunk count: {len(exact_matches)}")

    if exact_matches:
        print(f"\n" + "-" * 70)
        print("EXACT MATCHES (same chunk count):")
        print("-" * 70)
        for i, f in enumerate(exact_matches[:10], 1):
            print(f"{i}. {f['file']}")
            print(f"   Chunks: {f['labcore_chunks']} (labcore) = {f['topsoil_chunks']} (topsoil)")

    # Focus on backgroundconsumers.py
    print(f"\n" + "=" * 70)
    print("DETAILED ANALYSIS: clients/consumers/backgroundconsumers.py")
    print("=" * 70)

    chunk_query = """
        SELECT META().id, repo_id, file_path, start_line, end_line,
               LENGTH(content) as content_len, LENGTH(embedding) as emb_dims
        FROM `code_kosha`
        WHERE type='code_chunk'
          AND file_path = 'clients/consumers/backgroundconsumers.py'
          AND repo_id IN ['kbhalerao/labcore', 'ContinuumAgInc/topsoil2.0']
        ORDER BY repo_id, start_line
    """

    result = db.cluster.query(chunk_query)
    chunks = list(result)

    labcore_chunks = [c for c in chunks if 'labcore' in c['repo_id']]
    topsoil_chunks = [c for c in chunks if 'topsoil' in c['repo_id']]

    print(f"\nLabcore chunks: {len(labcore_chunks)}")
    print(f"Topsoil chunks: {len(topsoil_chunks)}")

    if labcore_chunks:
        print(f"\nLabcore chunks:")
        for i, c in enumerate(labcore_chunks[:5], 1):
            print(f"  {i}. ID: {c['id'][:16]}... Lines {c['start_line']}-{c['end_line']}, {c['content_len']} chars")

    if topsoil_chunks:
        print(f"\nTopsoil chunks:")
        for i, c in enumerate(topsoil_chunks[:5], 1):
            print(f"  {i}. ID: {c['id'][:16]}... Lines {c['start_line']}-{c['end_line']}, {c['content_len']} chars")

    # Check if chunks align by line numbers
    if len(labcore_chunks) > 0 and len(topsoil_chunks) > 0:
        print(f"\n" + "-" * 70)
        print("CHUNK ALIGNMENT CHECK:")
        print("-" * 70)

        # Group by line ranges
        labcore_by_lines = {(c['start_line'], c['end_line']): c for c in labcore_chunks}
        topsoil_by_lines = {(c['start_line'], c['end_line']): c for c in topsoil_chunks}

        aligned = []
        for lines, lc in labcore_by_lines.items():
            if lines in topsoil_by_lines:
                tc = topsoil_by_lines[lines]
                aligned.append({
                    'lines': lines,
                    'labcore_id': lc['id'],
                    'topsoil_id': tc['id'],
                    'labcore_len': lc['content_len'],
                    'topsoil_len': tc['content_len']
                })

        print(f"\nAligned chunks (same line ranges): {len(aligned)}")

        if aligned:
            print(f"\nFirst 3 aligned chunks:")
            for i, a in enumerate(aligned[:3], 1):
                print(f"{i}. Lines {a['lines'][0]}-{a['lines'][1]}")
                print(f"   Labcore: {a['labcore_id'][:16]}... ({a['labcore_len']} chars)")
                print(f"   Topsoil: {a['topsoil_id'][:16]}... ({a['topsoil_len']} chars)")
                print(f"   Content length match: {a['labcore_len'] == a['topsoil_len']}")

    print("=" * 70)

if __name__ == '__main__':
    main()
