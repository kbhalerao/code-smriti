/**
 * POST /api/search
 *
 * Perform semantic search across user's code repositories.
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getCluster } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import { OLLAMA_HOST, OLLAMA_MODEL } from '$env/static/private';
import type { SearchResult, CodeChunkDocument } from '$lib/server/types';

interface SearchRequest {
  query: string;
  limit?: number;
  repo_id?: string; // Optional: filter by specific repo
}

interface SearchResponse {
  success: boolean;
  results?: SearchResult[];
  query?: string;
  total_results?: number;
  error?: string;
}

/**
 * Generate embedding for a query using Ollama
 */
async function generateEmbedding(text: string): Promise<number[]> {
  const response = await fetch(`${OLLAMA_HOST}/api/embeddings`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: OLLAMA_MODEL,
      prompt: text,
    }),
  });

  if (!response.ok) {
    throw new Error(`Ollama API error: ${response.statusText}`);
  }

  const data = await response.json();
  return data.embedding;
}

export const POST: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Parse request body
    const body: SearchRequest = await event.request.json();
    const { query, limit = 10, repo_id } = body;

    // Validate input
    if (!query || typeof query !== 'string' || query.trim().length === 0) {
      return errorResponse('Search query is required', 400);
    }

    if (limit < 1 || limit > 50) {
      return errorResponse('Limit must be between 1 and 50', 400);
    }

    // Generate embedding for query
    let queryEmbedding: number[];
    try {
      queryEmbedding = await generateEmbedding(query);
    } catch (error) {
      console.error('Embedding generation error:', error);
      return errorResponse('Failed to generate query embedding', 500);
    }

    // Perform vector search using Couchbase FTS
    const cluster = await getCluster();

    // Build FTS search request
    // Note: This assumes the vector index is named "code_vector_index"
    // and includes user_id as an indexed field for filtering
    const searchRequest: any = {
      query: {
        match_none: {}, // Placeholder, actual search is done via knn
      },
      knn: [
        {
          field: 'embedding',
          vector: queryEmbedding,
          k: limit,
        },
      ],
      // Filter by user_id
      conjuncts: [
        {
          field: 'user_id',
          term: authUser.user_id,
        },
      ],
    };

    // Add repo_id filter if specified
    if (repo_id) {
      searchRequest.conjuncts.push({
        field: 'repo_id',
        term: repo_id,
      });
    }

    // Execute FTS search
    const scope = cluster.bucket('code_kosha').scope('_default');
    const searchResult = await scope.search(
      'code_vector_index',
      searchRequest,
      {
        limit,
        fields: ['*'],
      }
    );

    // Process search results
    const results: SearchResult[] = [];

    for await (const row of searchResult.rows()) {
      const chunk: CodeChunkDocument = row.fields as any;
      const score = row.score || 0;

      results.push({
        chunk,
        score,
      });
    }

    return successResponse<SearchResponse>({
      success: true,
      results,
      query,
      total_results: results.length,
    });
  } catch (error: any) {
    console.error('Search error:', error);

    // Handle specific FTS errors
    if (error?.message?.includes('index not found')) {
      return errorResponse(
        'Search index not configured. Please contact administrator.',
        503
      );
    }

    return errorResponse('Internal server error', 500);
  }
};
