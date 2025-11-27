#!/usr/bin/env python3
"""
Test embedding similarity between query and code chunks.
This helps diagnose why vector search returns docs instead of code.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

# Load the same model used in production
print("Loading embedding model...")
model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
print(f"✓ Model loaded on device: {model.device}\n")

# The query from our test
query = "Django Channels background worker with job counter decorator"

# Sample code chunks that SHOULD match
code_samples = {
    "Python class (actual code)": """class BackgroundOrderPDFGenerator(SyncConsumer):
    def process(self, message):
        print("received ", message)

        try:
            context = message['context']
            order_id = context['order_id']""",

    "Python class with metadata prefix (how it's indexed)": """Class: BackgroundOrderPDFGenerator
class BackgroundOrderPDFGenerator(SyncConsumer):
    def process(self, message):
        print("received ", message)""",

    "Just the class name": "BackgroundOrderPDFGenerator",

    "Markdown header (what's being returned)": "# In production (via Django Channels background worker)",

    "RST header (what's being returned)": "Testing Background Tasks\n-------------------------",

    "Natural language description": "This is a Django Channels consumer for background task processing"
}

# Generate embeddings with the same prefix used in production
print("Generating embeddings with 'search_document:' prefix...\n")

query_text = f"search_document: {query}"
query_embedding = model.encode(query_text, convert_to_tensor=False)

results = []

for name, code in code_samples.items():
    code_text = f"search_document: {code}"
    code_embedding = model.encode(code_text, convert_to_tensor=False)

    # Cosine similarity
    similarity = np.dot(query_embedding, code_embedding) / (
        np.linalg.norm(query_embedding) * np.linalg.norm(code_embedding)
    )

    results.append((name, similarity, code[:100]))

# Sort by similarity (highest first)
results.sort(key=lambda x: x[1], reverse=True)

# Print results
print("="*80)
print("SIMILARITY SCORES (Higher = Better Match)")
print("="*80)
print(f"\nQuery: '{query}'\n")

for i, (name, score, preview) in enumerate(results, 1):
    print(f"{i}. {name}")
    print(f"   Score: {score:.4f}")
    print(f"   Preview: {preview}...")
    print()

print("="*80)
print("\nINTERPRETATION:")
print("- Scores range from -1 to 1 (higher is better)")
print("- If markdown/RST headers score higher than Python code,")
print("  it explains why vector search returns docs instead of code")
print("="*80)
