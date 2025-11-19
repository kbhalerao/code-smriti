/**
 * Middleware Utilities
 *
 * Helper functions for request authentication and authorization.
 */

import { json } from '@sveltejs/kit';
import type { RequestEvent } from '@sveltejs/kit';
import { verifyAuthHeader } from './auth';
import type { JWTPayload } from './types';

/**
 * Authenticate a request and return the user payload
 * Returns null if authentication fails
 */
export function authenticateRequest(event: RequestEvent): JWTPayload | null {
  const authHeader = event.request.headers.get('Authorization');
  return verifyAuthHeader(authHeader);
}

/**
 * Require authentication for a request
 * Returns the user payload or throws a 401 response
 */
export function requireAuth(event: RequestEvent): JWTPayload {
  const user = authenticateRequest(event);

  if (!user) {
    throw json(
      {
        success: false,
        error: 'Unauthorized - Invalid or missing token',
      },
      { status: 401 }
    );
  }

  return user;
}

/**
 * Standard error response
 */
export interface ErrorResponse {
  success: false;
  error: string;
}

/**
 * Create a standard error response
 */
export function errorResponse(error: string, status: number = 500) {
  return json<ErrorResponse>(
    {
      success: false,
      error,
    },
    { status }
  );
}

/**
 * Create a standard success response
 */
export function successResponse<T>(data: T, status: number = 200) {
  return json(
    {
      success: true,
      ...data,
    },
    { status }
  );
}
