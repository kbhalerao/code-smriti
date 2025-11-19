/**
 * GET /api/jobs
 *
 * List all ingestion jobs for the authenticated user.
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getCluster } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import type { IngestionJobDocument } from '$lib/server/types';

interface ListJobsResponse {
  success: boolean;
  jobs?: IngestionJobDocument[];
  error?: string;
}

export const GET: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Query user's jobs sorted by creation date (newest first)
    const cluster = await getCluster();

    const queryResult = await cluster.query(
      `SELECT ingestion_jobs.*
       FROM ingestion_jobs
       WHERE user_id = $1 AND type = 'ingestion_job'
       ORDER BY created_at DESC
       LIMIT 100`,
      { parameters: [authUser.user_id] }
    );

    const jobs = queryResult.rows as IngestionJobDocument[];

    return successResponse<ListJobsResponse>({
      success: true,
      jobs,
    });
  } catch (error) {
    console.error('List jobs error:', error);
    return errorResponse('Internal server error', 500);
  }
};
