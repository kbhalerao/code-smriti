/**
 * POST /api/auth/login
 *
 * Authenticate user with email/password and return JWT token.
 */

import { json, type RequestHandler } from '@sveltejs/kit';
import { getUsersCollection } from '$lib/server/db';
import { verifyPassword, generateToken, toSafeUserInfo } from '$lib/server/auth';
import type { UserDocument } from '$lib/server/types';

interface LoginRequest {
  email: string;
  password: string;
}

interface LoginResponse {
  success: boolean;
  token?: string;
  user?: ReturnType<typeof toSafeUserInfo>;
  error?: string;
}

export const POST: RequestHandler = async ({ request }) => {
  try {
    // Parse request body
    const body: LoginRequest = await request.json();
    const { email, password } = body;

    // Validate input
    if (!email || !password) {
      return json<LoginResponse>(
        {
          success: false,
          error: 'Email and password are required',
        },
        { status: 400 }
      );
    }

    // Get users collection
    const usersCollection = await getUsersCollection();

    // Query user by email
    const cluster = (await import('$lib/server/db')).getCluster;
    const cl = await cluster();

    const queryResult = await cl.query(
      `SELECT META().id as doc_id, users.*
       FROM users
       WHERE email = $1 AND type = 'user'
       LIMIT 1`,
      { parameters: [email] }
    );

    const rows = queryResult.rows;

    if (rows.length === 0) {
      return json<LoginResponse>(
        {
          success: false,
          error: 'Invalid email or password',
        },
        { status: 401 }
      );
    }

    const userDoc = rows[0] as UserDocument;

    // Verify password
    const isValid = await verifyPassword(password, userDoc.password_hash);

    if (!isValid) {
      return json<LoginResponse>(
        {
          success: false,
          error: 'Invalid email or password',
        },
        { status: 401 }
      );
    }

    // Update last_login timestamp
    try {
      const docId = (rows[0] as any).doc_id;
      await usersCollection.mutateIn(docId, [
        {
          type: 'replace',
          path: 'last_login',
          value: new Date().toISOString(),
        },
      ]);
    } catch (error) {
      // Log error but don't fail login
      console.error('Failed to update last_login:', error);
    }

    // Generate JWT token
    const token = generateToken(userDoc.user_id, userDoc.email);

    // Return success response
    return json<LoginResponse>(
      {
        success: true,
        token,
        user: toSafeUserInfo(userDoc),
      },
      { status: 200 }
    );
  } catch (error) {
    console.error('Login error:', error);
    return json<LoginResponse>(
      {
        success: false,
        error: 'Internal server error',
      },
      { status: 500 }
    );
  }
};
