/**
 * POST /api/auth/register
 *
 * Register a new user account.
 */

import { json, type RequestHandler } from '@sveltejs/kit';
import { getUsersCollection } from '$lib/server/db';
import {
  hashPassword,
  generateToken,
  toSafeUserInfo,
  isValidEmail,
  isValidPassword,
  generateUserId,
} from '$lib/server/auth';
import type { UserDocument } from '$lib/server/types';

interface RegisterRequest {
  email: string;
  password: string;
}

interface RegisterResponse {
  success: boolean;
  token?: string;
  user?: ReturnType<typeof toSafeUserInfo>;
  error?: string;
}

export const POST: RequestHandler = async ({ request }) => {
  try {
    // Parse request body
    const body: RegisterRequest = await request.json();
    const { email, password } = body;

    // Validate input
    if (!email || !password) {
      return json<RegisterResponse>(
        {
          success: false,
          error: 'Email and password are required',
        },
        { status: 400 }
      );
    }

    // Validate email format
    if (!isValidEmail(email)) {
      return json<RegisterResponse>(
        {
          success: false,
          error: 'Invalid email format',
        },
        { status: 400 }
      );
    }

    // Validate password strength
    if (!isValidPassword(password)) {
      return json<RegisterResponse>(
        {
          success: false,
          error: 'Password must be at least 8 characters',
        },
        { status: 400 }
      );
    }

    // Check if email already exists
    const cluster = (await import('$lib/server/db')).getCluster;
    const cl = await cluster();

    const existingUserQuery = await cl.query(
      `SELECT META().id
       FROM users
       WHERE email = $1 AND type = 'user'
       LIMIT 1`,
      { parameters: [email] }
    );

    if (existingUserQuery.rows.length > 0) {
      return json<RegisterResponse>(
        {
          success: false,
          error: 'Email already registered',
        },
        { status: 409 }
      );
    }

    // Generate user ID and hash password
    const userId = generateUserId();
    const passwordHash = await hashPassword(password);

    // Create user document
    const now = new Date().toISOString();
    const newUser: UserDocument = {
      type: 'user',
      user_id: userId,
      email,
      password_hash: passwordHash,
      github_pat_encrypted: null,
      repos: [],
      quota_max_repos: 10, // Default quota for new users
      quota_max_chunks: 100000, // Default quota for new users
      created_at: now,
      updated_at: now,
      last_login: null,
    };

    // Insert user into database
    const usersCollection = await getUsersCollection();
    await usersCollection.insert(userId, newUser);

    // Generate JWT token
    const token = generateToken(userId, email);

    // Return success response
    return json<RegisterResponse>(
      {
        success: true,
        token,
        user: toSafeUserInfo(newUser),
      },
      { status: 201 }
    );
  } catch (error: any) {
    console.error('Registration error:', error);

    // Handle document already exists error
    if (error?.name === 'DocumentExistsError') {
      return json<RegisterResponse>(
        {
          success: false,
          error: 'User already exists',
        },
        { status: 409 }
      );
    }

    return json<RegisterResponse>(
      {
        success: false,
        error: 'Internal server error',
      },
      { status: 500 }
    );
  }
};
