/**
 * Authentication Utilities
 *
 * Handles password hashing, JWT generation/verification, and session management.
 */

import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { JWT_SECRET } from '$env/static/private';
import type { JWTPayload, UserDocument, SafeUserInfo } from './types';

const JWT_EXPIRY = '24h'; // 24 hour token expiry
const BCRYPT_ROUNDS = 12; // bcrypt cost factor

/**
 * Hash a password using bcrypt
 */
export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, BCRYPT_ROUNDS);
}

/**
 * Verify a password against a hash
 */
export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  return bcrypt.compare(password, hash);
}

/**
 * Generate a JWT token for a user
 */
export function generateToken(userId: string, email: string): string {
  const payload: Omit<JWTPayload, 'iat' | 'exp'> = {
    user_id: userId,
    email,
  };

  return jwt.sign(payload, JWT_SECRET, {
    expiresIn: JWT_EXPIRY,
  });
}

/**
 * Verify and decode a JWT token
 * Returns the payload if valid, null if invalid/expired
 */
export function verifyToken(token: string): JWTPayload | null {
  try {
    const payload = jwt.verify(token, JWT_SECRET) as JWTPayload;
    return payload;
  } catch (error) {
    // Token invalid or expired
    return null;
  }
}

/**
 * Convert UserDocument to SafeUserInfo (removes sensitive data)
 */
export function toSafeUserInfo(user: UserDocument): SafeUserInfo {
  return {
    user_id: user.user_id,
    email: user.email,
    repos: user.repos,
    quota_max_repos: user.quota_max_repos,
    quota_max_chunks: user.quota_max_chunks,
    created_at: user.created_at,
    last_login: user.last_login,
  };
}

/**
 * Extract JWT token from Authorization header
 * Supports: "Bearer <token>" format
 */
export function extractTokenFromHeader(authHeader: string | null): string | null {
  if (!authHeader) return null;

  const parts = authHeader.split(' ');
  if (parts.length !== 2 || parts[0] !== 'Bearer') {
    return null;
  }

  return parts[1];
}

/**
 * Extract and verify JWT from Authorization header
 * Returns payload if valid, null otherwise
 */
export function verifyAuthHeader(authHeader: string | null): JWTPayload | null {
  const token = extractTokenFromHeader(authHeader);
  if (!token) return null;

  return verifyToken(token);
}

/**
 * Validate email format
 */
export function isValidEmail(email: string): boolean {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailRegex.test(email);
}

/**
 * Validate password strength
 * Requirements: At least 8 characters
 */
export function isValidPassword(password: string): boolean {
  return password.length >= 8;
}

/**
 * Generate a unique user ID (UUID v4)
 */
export function generateUserId(): string {
  // Use crypto.randomUUID() if available (Node 16+)
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }

  // Fallback UUID v4 generation
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
