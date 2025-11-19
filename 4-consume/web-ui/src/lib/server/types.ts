/**
 * TypeScript types for database documents
 */

/**
 * User document stored in users bucket
 */
export interface UserDocument {
  type: 'user';
  user_id: string;
  email: string;
  password_hash: string;
  github_pat_encrypted: string | null;
  repos: RepoInfo[];
  quota_max_repos: number;
  quota_max_chunks: number;
  created_at: string;
  updated_at: string;
  last_login: string | null;
}

/**
 * Repository information in user document
 */
export interface RepoInfo {
  repo_id: string;
  added_at: string;
  last_synced: string | null;
  chunk_count: number;
  status: 'pending' | 'syncing' | 'synced' | 'error';
  sync_error: string | null;
}

/**
 * Ingestion job document stored in ingestion_jobs bucket
 */
export interface IngestionJobDocument {
  type: 'ingestion_job';
  job_id: string;
  user_id: string;
  repo_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  progress: JobProgress;
  error: string | null;
}

/**
 * Job progress information
 */
export interface JobProgress {
  total_files: number;
  processed_files: number;
  total_chunks: number;
  current_file: string | null;
}

/**
 * Code chunk document stored in code_kosha bucket
 */
export interface CodeChunkDocument {
  type: 'chunk';
  user_id: string;
  repo_id: string;
  file_path: string;
  chunk_index: number;
  content: string;
  language: string;
  start_line: number;
  end_line: number;
  embedding: number[];
  metadata: ChunkMetadata;
}

/**
 * Chunk metadata
 */
export interface ChunkMetadata {
  repo_name: string;
  file_type: string;
  file_size: number;
  last_modified: string;
  commit_sha: string | null;
}

/**
 * Vector search result
 */
export interface SearchResult {
  chunk: CodeChunkDocument;
  score: number;
  highlights?: string[];
}

/**
 * Safe user info (no sensitive data) for API responses
 */
export interface SafeUserInfo {
  user_id: string;
  email: string;
  repos: RepoInfo[];
  quota_max_repos: number;
  quota_max_chunks: number;
  created_at: string;
  last_login: string | null;
}

/**
 * JWT payload
 */
export interface JWTPayload {
  user_id: string;
  email: string;
  iat: number;
  exp: number;
}
