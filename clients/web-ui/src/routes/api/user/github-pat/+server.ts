/**
 * PATCH /api/user/github-pat
 *
 * Update user's GitHub Personal Access Token (encrypted).
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getUsersCollection } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import { encrypt } from '$lib/server/crypto';

interface UpdatePATRequest {
  github_pat: string;
}

interface UpdatePATResponse {
  success: boolean;
  message?: string;
  error?: string;
}

export const PATCH: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Parse request body
    const body: UpdatePATRequest = await event.request.json();
    const { github_pat } = body;

    // Validate input
    if (!github_pat || typeof github_pat !== 'string') {
      return errorResponse('GitHub PAT is required', 400);
    }

    // Basic validation: GitHub PATs start with 'ghp_' or 'github_pat_'
    if (!github_pat.startsWith('ghp_') && !github_pat.startsWith('github_pat_')) {
      return errorResponse('Invalid GitHub PAT format', 400);
    }

    // Encrypt the PAT
    const encryptedPAT = encrypt(github_pat);

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Update user document
    await usersCollection.mutateIn(authUser.user_id, [
      {
        type: 'replace',
        path: 'github_pat_encrypted',
        value: encryptedPAT,
      },
      {
        type: 'replace',
        path: 'updated_at',
        value: new Date().toISOString(),
      },
    ]);

    return successResponse<UpdatePATResponse>({
      success: true,
      message: 'GitHub PAT updated successfully',
    });
  } catch (error: any) {
    // Handle document not found
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('User not found', 404);
    }

    console.error('Update GitHub PAT error:', error);
    return errorResponse('Internal server error', 500);
  }
};

/**
 * DELETE /api/user/github-pat
 *
 * Remove user's GitHub Personal Access Token.
 */
export const DELETE: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Remove PAT from user document
    await usersCollection.mutateIn(authUser.user_id, [
      {
        type: 'replace',
        path: 'github_pat_encrypted',
        value: null,
      },
      {
        type: 'replace',
        path: 'updated_at',
        value: new Date().toISOString(),
      },
    ]);

    return successResponse<UpdatePATResponse>({
      success: true,
      message: 'GitHub PAT removed successfully',
    });
  } catch (error: any) {
    // Handle document not found
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('User not found', 404);
    }

    console.error('Delete GitHub PAT error:', error);
    return errorResponse('Internal server error', 500);
  }
};
