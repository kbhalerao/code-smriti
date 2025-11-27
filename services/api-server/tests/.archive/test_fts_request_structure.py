#!/usr/bin/env python3
"""
Test the exact FTS request structure we're sending
"""
import asyncio
import json

# Simulate what our code does
def build_fts_request(query, text_query, doc_type="code_chunk", code_limit=20):
    fts_request = {
        "size": code_limit,
        "fields": ["*"]
    }

    # Build filter conjuncts
    filter_conjuncts = []

    # ALWAYS filter by doc_type
    filter_conjuncts.append({
        "term": doc_type,
        "field": "type"
    })

    # Add text search if provided
    if text_query:
        filter_conjuncts.append({
            "match": text_query,
            "field": "content"
        })

    # Build the filter object
    if len(filter_conjuncts) == 1:
        knn_filter = filter_conjuncts[0]
    else:
        knn_filter = {"conjuncts": filter_conjuncts}

    # Add vector search with pre-filtering
    if query:
        # Fake embedding
        query_embedding = [0.1] * 768

        fts_request["knn"] = [{
            "field": "embedding",
            "vector": query_embedding,
            "k": code_limit,
            "filter": knn_filter
        }]
    else:
        # Text-only search
        fts_request["query"] = knn_filter

    return fts_request

# Test hybrid search (both vector + text)
print("=" * 80)
print("HYBRID SEARCH REQUEST (vector + text + type filter)")
print("=" * 80)
request = build_fts_request("Django worker", "job_counter", "code_chunk", 20)
# Remove embedding for display
request["knn"][0]["vector"] = "[... 768 dims ...]"
print(json.dumps(request, indent=2))

print("\n" + "=" * 80)
print("TEXT-ONLY SEARCH REQUEST (text + type filter)")
print("=" * 80)
request2 = build_fts_request(None, "job_counter", "code_chunk", 20)
print(json.dumps(request2, indent=2))

print("\n" + "=" * 80)
print("VECTOR-ONLY SEARCH REQUEST (vector + type filter)")
print("=" * 80)
request3 = build_fts_request("Django worker", None, "code_chunk", 20)
request3["knn"][0]["vector"] = "[... 768 dims ...]"
print(json.dumps(request3, indent=2))
