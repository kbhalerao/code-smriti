/**
 * DELETE /api/repos/[repo_id]
 *
 * Remove a repository and delete all associated chunks.
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getUsersCollection, getCluster } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import type { UserDocument } from '$lib/server/types';

interface DeleteRepoResponse {
  success: boolean;
  message?: string;
  deleted_chunks?: number;
  error?: string;
}

export const DELETE: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Get repo_id from URL params (need to decode it)
    const repoId = decodeURIComponent(event.params.repo_id || '');

    if (!repoId) {
      return errorResponse('Repository ID is required', 400);
    }

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Fetch user document
    const userResult = await usersCollection.get(authUser.user_id);
    const userDoc = userResult.content as UserDocument;

    // Check if repo exists
    const repoIndex = userDoc.repos.findIndex((r) => r.repo_id === repoId);
    if (repoIndex === -1) {
      return errorResponse('Repository not found', 404);
    }

    // Remove repo from user's repos array
    const updatedRepos = userDoc.repos.filter((r) => r.repo_id !== repoId);

    await usersCollection.mutateIn(authUser.user_id, [
      {
        type: 'replace',
        path: 'repos',
        value: updatedRepos,
      },
      {
        type: 'replace',
        path: 'updated_at',
        value: new Date().toISOString(),
      },
    ]);

    // Delete all chunks for this repo
    const cluster = await getCluster();

    const deleteResult = await cluster.query(
      `DELETE FROM code_kosha
       WHERE user_id = $1 AND repo_id = $2`,
      { parameters: [authUser.user_id, repoId] }
    );

    const metadata = deleteResult.metadata();
    const deletedCount = metadata?.metrics()?.mutationCount || 0;

    return successResponse<DeleteRepoResponse>({
      success: true,
      message: 'Repository removed successfully',
      deleted_chunks: deletedCount,
    });
  } catch (error: any) {
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('User not found', 404);
    }

    console.error('Delete repo error:', error);
    return errorResponse('Internal server error', 500);
  }
};
