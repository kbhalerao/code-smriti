/**
 * GET /api/user/me
 *
 * Get current authenticated user's profile.
 */

import type { RequestHandler } from '@sveltejs/kit';
import { getUsersCollection } from '$lib/server/db';
import { requireAuth, errorResponse, successResponse } from '$lib/server/middleware';
import { toSafeUserInfo } from '$lib/server/auth';
import type { UserDocument, SafeUserInfo } from '$lib/server/types';

interface UserProfileResponse {
  success: boolean;
  user?: SafeUserInfo;
  error?: string;
}

export const GET: RequestHandler = async (event) => {
  try {
    // Authenticate request
    const authUser = requireAuth(event);

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Fetch user document
    const result = await usersCollection.get(authUser.user_id);
    const userDoc = result.content as UserDocument;

    // Return safe user info
    return successResponse<UserProfileResponse>({
      success: true,
      user: toSafeUserInfo(userDoc),
    });
  } catch (error: any) {
    // Handle document not found
    if (error?.name === 'DocumentNotFoundError') {
      return errorResponse('User not found', 404);
    }

    console.error('Get user profile error:', error);
    return errorResponse('Internal server error', 500);
  }
};
