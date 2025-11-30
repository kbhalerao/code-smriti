/**
 * POST /api/auth/register
 *
 * Registration is disabled. Contact administrator for account creation.
 */

import { json, type RequestHandler } from '@sveltejs/kit';

interface RegisterResponse {
  success: boolean;
  error?: string;
}

export const POST: RequestHandler = async () => {
  return json<RegisterResponse>(
    {
      success: false,
      error: 'Registration is disabled. Contact administrator for account access.',
    },
    { status: 403 }
  );
};
