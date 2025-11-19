/**
 * Repository Management Endpoints
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getUsersCollection, getJobsCollection, getCluster } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import { decrypt } from '$lib/server/crypto';
import type { UserDocument, RepoInfo, IngestionJobDocument } from '$lib/server/types';

interface ListReposResponse {
  success: boolean;
  repos?: RepoInfo[];
  error?: string;
}

interface AddRepoRequest {
  repo_id: string; // Format: owner/repo
}

interface AddRepoResponse {
  success: boolean;
  job_id?: string;
  message?: string;
  error?: string;
}

/**
 * GET /api/repos
 *
 * List all repositories for the authenticated user.
 */
export const GET: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Fetch user document
    const result = await usersCollection.get(authUser.user_id);
    const userDoc = result.content as UserDocument;

    // Return user's repos
    return successResponse<ListReposResponse>({
      success: true,
      repos: userDoc.repos,
    });
  } catch (error: any) {
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('User not found', 404);
    }

    console.error('List repos error:', error);
    return errorResponse('Internal server error', 500);
  }
};

/**
 * POST /api/repos
 *
 * Add a new repository and create an ingestion job.
 */
export const POST: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Parse request body
    const body: AddRepoRequest = await event.request.json();
    const { repo_id } = body;

    // Validate input
    if (!repo_id || typeof repo_id !== 'string') {
      return errorResponse('Repository ID is required', 400);
    }

    // Validate repo_id format (owner/repo)
    const repoPattern = /^[\w.-]+\/[\w.-]+$/;
    if (!repoPattern.test(repo_id)) {
      return errorResponse('Invalid repository format. Expected: owner/repo', 400);
    }

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Fetch user document
    const userResult = await usersCollection.get(authUser.user_id);
    const userDoc = userResult.content as UserDocument;

    // Check if repo already exists
    const existingRepo = userDoc.repos.find((r) => r.repo_id === repo_id);
    if (existingRepo) {
      return errorResponse('Repository already added', 409);
    }

    // Check quota
    if (userDoc.repos.length >= userDoc.quota_max_repos) {
      return errorResponse(
        `Repository quota exceeded (max: ${userDoc.quota_max_repos})`,
        429
      );
    }

    // Check if user has GitHub PAT configured
    if (!userDoc.github_pat_encrypted) {
      return errorResponse('GitHub Personal Access Token not configured', 400);
    }

    // Create new repo entry
    const now = new Date().toISOString();
    const newRepo: RepoInfo = {
      repo_id,
      added_at: now,
      last_synced: null,
      chunk_count: 0,
      status: 'pending',
      sync_error: null,
    };

    // Update user document with new repo
    await usersCollection.mutateIn(authUser.user_id, [
      {
        type: 'arrayAppend',
        path: 'repos',
        value: newRepo,
      },
      {
        type: 'replace',
        path: 'updated_at',
        value: now,
      },
    ]);

    // Create ingestion job
    const jobId = `job_${Date.now()}_${Math.random().toString(36).substring(7)}`;
    const jobDoc: IngestionJobDocument = {
      type: 'ingestion_job',
      job_id: jobId,
      user_id: authUser.user_id,
      repo_id,
      status: 'queued',
      created_at: now,
      started_at: null,
      completed_at: null,
      progress: {
        total_files: 0,
        processed_files: 0,
        total_chunks: 0,
        current_file: null,
      },
      error: null,
    };

    const jobsCollection = await getJobsCollection();
    await jobsCollection.insert(jobId, jobDoc);

    return successResponse<AddRepoResponse>(
      {
        success: true,
        job_id: jobId,
        message: 'Repository added and ingestion job created',
      },
      201
    );
  } catch (error: any) {
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('User not found', 404);
    }

    console.error('Add repo error:', error);
    return errorResponse('Internal server error', 500);
  }
};
