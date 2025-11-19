/**
 * Encryption Utilities
 *
 * Handles encryption/decryption of sensitive data like GitHub Personal Access Tokens.
 * Uses AES-256-CBC encryption with PBKDF2 key derivation.
 */

import crypto from 'crypto';
import { AES_ENCRYPTION_KEY } from '$env/static/private';

const ALGORITHM = 'aes-256-cbc';
const IV_LENGTH = 16; // AES block size
const SALT_LENGTH = 32;
const KEY_LENGTH = 32; // 256 bits
const ITERATIONS = 100000; // PBKDF2 iterations

/**
 * Derive a key from the master key using PBKDF2
 */
function deriveKey(salt: Buffer): Buffer {
  return crypto.pbkdf2Sync(
    Buffer.from(AES_ENCRYPTION_KEY, 'hex'),
    salt,
    ITERATIONS,
    KEY_LENGTH,
    'sha256'
  );
}

/**
 * Encrypt a string (e.g., GitHub PAT)
 * Returns base64-encoded encrypted data with format: salt:iv:encrypted
 */
export function encrypt(plaintext: string): string {
  // Generate random salt and IV
  const salt = crypto.randomBytes(SALT_LENGTH);
  const iv = crypto.randomBytes(IV_LENGTH);

  // Derive key from master key and salt
  const key = deriveKey(salt);

  // Create cipher and encrypt
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);
  let encrypted = cipher.update(plaintext, 'utf8', 'base64');
  encrypted += cipher.final('base64');

  // Return format: salt:iv:encrypted (all base64)
  return `${salt.toString('base64')}:${iv.toString('base64')}:${encrypted}`;
}

/**
 * Decrypt a string encrypted with encrypt()
 * Expects format: salt:iv:encrypted (all base64)
 * Returns null if decryption fails
 */
export function decrypt(encryptedData: string): string | null {
  try {
    // Parse the encrypted data
    const parts = encryptedData.split(':');
    if (parts.length !== 3) {
      return null;
    }

    const salt = Buffer.from(parts[0], 'base64');
    const iv = Buffer.from(parts[1], 'base64');
    const encrypted = parts[2];

    // Derive key from master key and salt
    const key = deriveKey(salt);

    // Create decipher and decrypt
    const decipher = crypto.createDecipheriv(ALGORITHM, key, iv);
    let decrypted = decipher.update(encrypted, 'base64', 'utf8');
    decrypted += decipher.final('utf8');

    return decrypted;
  } catch (error) {
    // Decryption failed
    return null;
  }
}

/**
 * Validate that the AES_ENCRYPTION_KEY is properly configured
 * Should be a 64-character hex string (32 bytes)
 */
export function validateEncryptionKey(): boolean {
  if (!AES_ENCRYPTION_KEY) {
    return false;
  }

  // Check if it's a valid hex string of correct length
  const hexRegex = /^[0-9a-fA-F]{64}$/;
  return hexRegex.test(AES_ENCRYPTION_KEY);
}

/**
 * Generate a new random encryption key
 * Returns a 64-character hex string suitable for AES_ENCRYPTION_KEY
 * This should be run once during setup and stored in .env
 */
export function generateEncryptionKey(): string {
  return crypto.randomBytes(32).toString('hex');
}
