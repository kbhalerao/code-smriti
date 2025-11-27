/**
 * POST /api/auth/logout
 *
 * Logout user (client-side should discard JWT token).
 * This endpoint is primarily for consistency and future stateful session management.
 */

import { json, type RequestHandler } from '@sveltejs/kit';

interface LogoutResponse {
  success: boolean;
  message?: string;
}

export const POST: RequestHandler = async () => {
  // Since we're using stateless JWT, logout is handled client-side
  // by discarding the token. This endpoint is here for:
  // 1. Consistency with typical auth APIs
  // 2. Future enhancement (token blacklist, session management)
  // 3. Logging/analytics

  return json<LogoutResponse>(
    {
      success: true,
      message: 'Logged out successfully',
    },
    { status: 200 }
  );
};
