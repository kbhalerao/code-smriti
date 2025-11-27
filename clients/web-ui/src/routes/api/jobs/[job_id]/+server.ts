/**
 * GET /api/jobs/[job_id]
 *
 * Get details for a specific ingestion job.
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getJobsCollection } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import type { IngestionJobDocument } from '$lib/server/types';

interface JobDetailsResponse {
  success: boolean;
  job?: IngestionJobDocument;
  error?: string;
}

export const GET: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Get job_id from URL params
    const jobId = event.params.job_id || '';

    if (!jobId) {
      return errorResponse('Job ID is required', 400);
    }

    // Get jobs collection
    const jobsCollection = await getJobsCollection();

    // Fetch job document
    const result = await jobsCollection.get(jobId);
    const jobDoc = result.content as IngestionJobDocument;

    // Verify job belongs to authenticated user
    if (jobDoc.user_id !== authUser.user_id) {
      return errorResponse('Access denied', 403);
    }

    return successResponse<JobDetailsResponse>({
      success: true,
      job: jobDoc,
    });
  } catch (error: any) {
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('Job not found', 404);
    }

    console.error('Get job details error:', error);
    return errorResponse('Internal server error', 500);
  }
};
