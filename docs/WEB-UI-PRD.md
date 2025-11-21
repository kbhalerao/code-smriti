# CodeSmriti Web UI - Product Requirements Document

## Overview

**Product**: Multi-tenant web interface for CodeSmriti
**Purpose**: Enable internal team members to connect their GitHub repositories, ingest code, and search using natural language
**Scope**: Internal team use (MVP)
**Tech Stack**: SvelteKit + TypeScript + Couchbase + Ollama

---

## Database Architecture

### 3-Bucket Design

CodeSmriti uses **three Couchbase buckets** with different access patterns and security models:

1. **`code_kosha`** - Multi-tenant code chunks (existing, modified to add user_id)
2. **`users`** - User credentials and configuration (new, privileged)
3. **`ingestion_jobs`** - Job queue and progress tracking (new, used as queue)

---

### Bucket 1: `code_kosha` (Code & Document Chunks)

**Purpose**: Stores all code chunks, document chunks, and commit metadata with embeddings
**Access**: Server-side only, always filtered by `user_id`
**Size**: ~50MB per 50,000 chunks

#### Document Schema

**Code Chunk:**
```json
{
  "type": "code_chunk",
  "user_id": "uuid-abc123",              // NEW FIELD - foreign key to users
  "chunk_id": "sha256...",
  "repo_id": "owner/repo-name",
  "file_path": "src/main.py",
  "chunk_type": "function",              // function, class, import, etc.
  "code_text": "def foo():\n    return 42",
  "language": "python",
  "embedding": [0.123, 0.456, ...],      // 768 floats from nomic-embed-text
  "metadata": {
    "commit_hash": "abc123def",
    "start_line": 10,
    "end_line": 15,
    "name": "foo"
  },
  "created_at": "2025-11-19T10:00:00Z"
}
```

**Document Chunk:**
```json
{
  "type": "document",
  "user_id": "uuid-abc123",
  "chunk_id": "sha256...",
  "repo_id": "owner/repo-name",
  "file_path": "README.md",
  "doc_type": "markdown",
  "content": "# Project Title\n\nDescription...",
  "embedding": [0.123, 0.456, ...],
  "metadata": {
    "commit_hash": "abc123def"
  },
  "created_at": "2025-11-19T10:00:00Z"
}
```

#### Indexes

```sql
-- Primary index (existing)
CREATE PRIMARY INDEX ON code_kosha;

-- Composite index for user queries
CREATE INDEX idx_user_repo ON code_kosha(user_id, repo_id, type);

-- Full-Text Search + Vector Index (existing, modified)
-- Name: code_vector_index
-- Indexed fields: user_id, repo_id, language, embedding (768-dim HNSW)
```

**Vector Index Configuration:**
```json
{
  "name": "code_vector_index",
  "type": "fulltext-index",
  "sourceName": "code_kosha",
  "params": {
    "mapping": {
      "types": {
        "code_chunk": {
          "properties": {
            "user_id": {
              "fields": [{"name": "user_id", "type": "text", "index": true}]
            },
            "repo_id": {
              "fields": [{"name": "repo_id", "type": "text", "index": true}]
            },
            "language": {
              "fields": [{"name": "language", "type": "text", "index": true}]
            },
            "embedding": {
              "fields": [{
                "name": "embedding",
                "type": "vector",
                "dims": 768,
                "similarity": "dot_product",
                "vector_index_optimized_for": "recall"
              }]
            }
          }
        },
        "document": {
          // Same structure as code_chunk
        }
      }
    }
  }
}
```

---

### Bucket 2: `users` (User Credentials & Config)

**Purpose**: Store user authentication, GitHub integration, and repo configuration
**Access**: Server-side ONLY - never exposed to client
**Security**: Privileged bucket, contains encrypted secrets
**Size**: ~256MB allocation

#### Document Schema

```json
{
  "type": "user",
  "user_id": "uuid-abc123",

  // Authentication
  "email": "teammate@company.com",
  "password_hash": "$2b$12$...",         // bcrypt with 12 rounds
  "email_verified": true,

  // GitHub Integration
  "github_pat_encrypted": "U2FsdGVk...",  // AES-256-CBC encrypted
  "github_username": "octocat",            // Cached from GitHub API
  "github_user_id": 12345,                 // GitHub numeric ID

  // Repositories (Rich Objects with Metadata)
  "repos": [
    {
      "repo_id": "owner/repo1",
      "added_at": "2025-11-19T10:00:00Z",
      "last_synced": "2025-11-19T15:30:00Z",
      "chunk_count": 1234,
      "status": "synced",                  // synced, syncing, error, pending
      "sync_error": null
    },
    {
      "repo_id": "owner/repo2",
      "added_at": "2025-11-19T10:05:00Z",
      "last_synced": null,
      "chunk_count": 0,
      "status": "pending",
      "sync_error": null
    }
  ],

  // Quotas (adjustable per user)
  "quota_max_repos": 50,
  "quota_max_chunks": 500000,

  // Metadata
  "created_at": "2025-11-18T09:00:00Z",
  "updated_at": "2025-11-19T15:30:00Z",
  "last_login": "2025-11-19T14:00:00Z"
}
```

#### Indexes

```sql
CREATE PRIMARY INDEX ON users;
CREATE INDEX idx_email ON users(email) WHERE type = "user";
```

#### Security Notes

- This bucket NEVER accessible from client-side code
- All access via SvelteKit server endpoints using admin Couchbase credentials
- GitHub PAT encryption key stored in environment variable: `AES_ENCRYPTION_KEY`
- PATs decrypted only when needed for GitHub API calls or ingestion

---

### Bucket 3: `ingestion_jobs` (Job Queue & Progress)

**Purpose**: Acts as job queue + progress tracker for async ingestion
**Access**: Server creates jobs, worker processes them, client polls for progress
**Pattern**: Cron-based worker polls for `status="queued"` documents
**Size**: ~128MB allocation

#### Document Schema

```json
{
  "type": "ingestion_job",
  "job_id": "uuid-job-456",
  "user_id": "uuid-abc123",              // FK to users bucket

  // Job Configuration
  "repos": ["owner/repo1", "owner/repo2"],
  "force_full_sync": false,              // true = re-ingest all, false = incremental

  // Status
  "status": "queued",                    // queued → running → completed | failed

  // Progress (updated by worker in real-time)
  "progress": {
    "current_repo": "owner/repo2",
    "current_repo_index": 1,             // 0-based
    "total_repos": 2,

    "files_processed": 45,
    "files_total": 234,

    "chunks_created": 892,
    "chunks_updated": 12,
    "chunks_deleted": 5,

    "current_file": "src/utils/auth.py",
    "current_operation": "generating embeddings"  // cloning, parsing, embedding, storing
  },

  // Timing
  "created_at": "2025-11-19T10:00:00Z",
  "started_at": "2025-11-19T10:00:15Z",
  "completed_at": null,

  // Results (filled on completion)
  "result": {
    "total_chunks_created": null,
    "total_chunks_updated": null,
    "total_chunks_deleted": null,
    "repos_succeeded": null,
    "repos_failed": null
  },

  // Error Tracking
  "error": null,                          // Error message if failed
  "error_repo": null,                     // Which repo caused failure
  "retry_count": 0
}
```

#### Indexes

```sql
CREATE PRIMARY INDEX ON ingestion_jobs;
CREATE INDEX idx_user_jobs ON ingestion_jobs(user_id, created_at DESC) WHERE type = "ingestion_job";
CREATE INDEX idx_queued_jobs ON ingestion_jobs(status, created_at ASC) WHERE type = "ingestion_job";
```

#### Queue Mechanism

**Worker Pattern:**
1. Cron job runs every 1 minute: `*/1 * * * *`
2. Query for oldest queued job:
   ```sql
   SELECT * FROM ingestion_jobs
   WHERE status = 'queued'
   ORDER BY created_at ASC
   LIMIT 1
   ```
3. If job found:
   - Update `status = 'running'`, `started_at = now()`
   - Process each repo in `repos[]` array
   - Update `progress` object after each file
   - On completion: `status = 'completed'`, fill `result` object
   - On error: `status = 'failed'`, set `error` message

**Frontend Polling:**
- Client polls `GET /api/jobs/{job_id}` every 2 seconds
- Displays progress bar: "Processing repo 2/2, 892 chunks created"
- Stops polling when `status = 'completed' | 'failed'`

---

## API Endpoints

All endpoints are SvelteKit server routes in `src/routes/api/`

### Authentication

#### `POST /api/auth/signup`

Create new user account.

**Request:**
```json
{
  "email": "teammate@company.com",
  "password": "secure-password-123"
}
```

**Response (200):**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "user_id": "uuid-abc123",
    "email": "teammate@company.com",
    "repos": [],
    "quota_max_repos": 50,
    "quota_max_chunks": 500000
  }
}
```

**Logic:**
1. Validate email format
2. Hash password with bcrypt (12 rounds)
3. Create user document in `users` bucket
4. Generate JWT (payload: `{user_id}`, expiry: 24 hours)
5. Return token + user data (without sensitive fields)

---

#### `POST /api/auth/login`

Authenticate existing user.

**Request:**
```json
{
  "email": "teammate@company.com",
  "password": "secure-password-123"
}
```

**Response (200):**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "user_id": "uuid-abc123",
    "email": "teammate@company.com",
    "repos": [{...}, {...}],
    "quota_max_repos": 50,
    "quota_max_chunks": 500000
  }
}
```

**Logic:**
1. Query `users` bucket by email
2. Compare password with bcrypt
3. Update `last_login` timestamp
4. Generate JWT
5. Return token + user data

---

#### `POST /api/auth/github-pat`

Store encrypted GitHub Personal Access Token.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Request:**
```json
{
  "github_pat": "ghp_abc123..."
}
```

**Response (200):**
```json
{
  "success": true,
  "github_username": "octocat"
}
```

**Logic:**
1. Verify JWT → extract `user_id`
2. Test PAT by calling `GET https://api.github.com/user`
3. Encrypt PAT with AES-256-CBC using `AES_ENCRYPTION_KEY`
4. Store encrypted PAT in user document
5. Cache `github_username` and `github_user_id`
6. Return success + username

---

### Repository Management

#### `GET /api/repos/discover`

Fetch user's GitHub repositories via GitHub API.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200):**
```json
{
  "repos": [
    {
      "repo_id": "owner/repo1",
      "name": "repo1",
      "full_name": "owner/repo1",
      "description": "A cool project",
      "language": "Python",
      "stars": 123,
      "private": false,
      "url": "https://github.com/owner/repo1"
    },
    // ... more repos
  ]
}
```

**Logic:**
1. Verify JWT → extract `user_id`
2. Get user from `users` bucket
3. Decrypt `github_pat_encrypted`
4. Call GitHub API: `GET /user/repos?per_page=100`
5. Transform and return repo list

---

#### `POST /api/repos/add`

Add repositories to user's account and trigger ingestion.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Request:**
```json
{
  "repos": ["owner/repo1", "owner/repo2"]
}
```

**Response (200):**
```json
{
  "job_id": "uuid-job-456",
  "message": "Ingestion job created"
}
```

**Logic:**
1. Verify JWT → extract `user_id`
2. Get user from `users` bucket
3. Check quota: `current_repos + new_repos <= quota_max_repos`
4. Add repos to `user.repos[]` with `status="pending"`
5. Create ingestion job in `ingestion_jobs` bucket
6. Return job_id for progress tracking

---

### Search

#### `POST /api/search`

Vector search for code across user's repositories.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Request:**
```json
{
  "query": "authentication middleware",
  "repo_filter": "owner/repo1",          // Optional
  "language_filter": "python",           // Optional
  "limit": 10
}
```

**Response (200):**
```json
{
  "results": [
    {
      "chunk_id": "sha256...",
      "score": 0.94,
      "repo_id": "owner/repo1",
      "file_path": "src/auth/middleware.py",
      "code_text": "def authenticate_user(token: str):\n    ...",
      "language": "python",
      "chunk_type": "function",
      "metadata": {
        "start_line": 15,
        "end_line": 30,
        "name": "authenticate_user"
      }
    },
    // ... more results
  ]
}
```

**Logic:**
1. Verify JWT → extract `user_id`
2. Generate query embedding via Ollama:
   ```javascript
   POST http://localhost:11434/api/embeddings
   {
     "model": "nomic-embed-text",
     "prompt": "search_query: authentication middleware"
   }
   ```
3. Build FTS query with filters:
   ```javascript
   {
     "knn": [{"field": "embedding", "vector": [...], "k": 10}],
     "query": {
       "conjuncts": [
         {"field": "user_id", "match": user_id},  // ALWAYS REQUIRED
         {"field": "repo_id", "match": "owner/repo1"},  // If filtered
         {"field": "language", "match": "python"}       // If filtered
       ]
     },
     "size": 10
   }
   ```
4. Query Couchbase FTS: `POST http://localhost:8094/api/index/code_vector_index/query`
5. Parse results and return (embeddings removed to reduce size)

**Security:** The `user_id` filter is CRITICAL - prevents cross-user data leakage.

---

### Jobs

#### `GET /api/jobs/{job_id}`

Get ingestion job status and progress.

**Headers:**
```
Authorization: Bearer <jwt-token>
```

**Response (200):**
```json
{
  "job_id": "uuid-job-456",
  "user_id": "uuid-abc123",
  "status": "running",
  "repos": ["owner/repo1", "owner/repo2"],
  "progress": {
    "current_repo": "owner/repo2",
    "current_repo_index": 1,
    "total_repos": 2,
    "files_processed": 45,
    "files_total": 234,
    "chunks_created": 892,
    "current_file": "src/utils/auth.py",
    "current_operation": "generating embeddings"
  },
  "created_at": "2025-11-19T10:00:00Z",
  "started_at": "2025-11-19T10:00:15Z",
  "completed_at": null
}
```

**Logic:**
1. Verify JWT → extract `user_id`
2. Query `ingestion_jobs` bucket:
   ```sql
   SELECT * FROM ingestion_jobs
   WHERE job_id = $job_id AND user_id = $user_id
   ```
3. Verify ownership (if not found, return 404)
4. Return job document

**Usage:** Frontend polls this endpoint every 2 seconds to update progress UI.

---

## User Flows

### Flow 1: Onboarding

```
1. User visits landing page (/)
   → See hero: "Chat with Your Code"
   → Click "Get Started"

2. Sign up page (/signup)
   → Enter email + password
   → POST /api/auth/signup
   → Receive JWT, redirect to /dashboard

3. Dashboard (/dashboard)
   → See message: "Add your GitHub token to get started"
   → Click "Connect GitHub"

4. GitHub PAT page (/github)
   → Enter GitHub Personal Access Token
   → POST /api/auth/github-pat
   → Success → redirect to /repos/connect
```

---

### Flow 2: Add Repositories

```
1. Connect repos page (/repos/connect)
   → Click "Discover My Repos"
   → GET /api/repos/discover
   → Display list of user's GitHub repos

2. User selects repos
   → Checkboxes next to each repo
   → Shows estimated chunks: "~5,000 chunks"
   → Select 3 repos

3. Click "Start Ingestion"
   → POST /api/repos/add {repos: [...]}
   → Receive job_id
   → Redirect to /jobs/{job_id}

4. Job progress page (/jobs/{job_id})
   → Poll GET /api/jobs/{job_id} every 2 seconds
   → Display:
     - Progress bar: "67% complete"
     - Current repo: "owner/repo2"
     - Files processed: "45/234"
     - Chunks created: "892"
   → On completion:
     - Show success message
     - Redirect to /search
```

---

### Flow 3: Search Code

```
1. Search page (/search)
   → Search bar: "How do we handle authentication?"
   → Optional filters:
     - Repos: [All] or select specific repos
     - Languages: [All] or [Python, JS, etc.]

2. User clicks "Search"
   → POST /api/search {query, repo_filter, language_filter}
   → Display results:

   Result 1: ★★★★☆ 94% match
   src/auth/middleware.py:15 (owner/repo1)
   ```python
   def authenticate_user(token: str):
       # Verify JWT token
       ...
   ```

   Result 2: ★★★★☆ 87% match
   src/utils/jwt.py:42 (owner/repo1)
   ```python
   class JWTValidator:
       ...
   ```

3. User clicks result
   → Expand to show full function
   → "Open in GitHub" link
```

---

### Flow 4: Daily Sync

```
Background process (cron):
1. Daily at 3 AM, create sync jobs for all users
2. Worker picks up jobs from queue
3. For each user repo:
   - Check git commits since last_synced
   - Identify changed files
   - Delete old chunks for changed files
   - Re-parse and store new chunks
   - Update user.repos[].last_synced
4. User sees updated data on next search
```

---

## Security Model

### Authentication

**JWT Tokens:**
- Algorithm: HS256 (HMAC-SHA256)
- Payload: `{user_id: "uuid-abc123", iat: 1700000000}`
- Secret: Stored in environment variable `JWT_SECRET`
- Expiry: 24 hours (sufficient for internal use)
- Storage: httpOnly cookie (XSS protection)

**Password Hashing:**
- Algorithm: bcrypt
- Rounds: 12 (2^12 iterations)
- Salt: Auto-generated per password

---

### Data Isolation

**Critical Rule:** ALL queries to `code_kosha` MUST filter by `user_id`

**Example (CORRECT):**
```sql
SELECT * FROM code_kosha
WHERE user_id = $user_id AND repo_id = $repo_id
```

**Example (WRONG - SECURITY BREACH):**
```sql
SELECT * FROM code_kosha
WHERE repo_id = $repo_id
-- Missing user_id filter! Would return ALL users' data
```

**Enforcement:**
- SvelteKit middleware (`hooks.server.js`) extracts `user_id` from JWT
- All API endpoints access `user_id` from `event.locals`
- Unit tests verify every query includes `user_id` filter

---

### GitHub PAT Encryption

**Algorithm:** AES-256-CBC
**Key:** 256-bit key stored in environment variable `AES_ENCRYPTION_KEY`
**IV:** Random 16-byte IV generated per encryption, prepended to ciphertext

**Encryption Flow:**
```javascript
import crypto from 'crypto';

function encryptPAT(pat: string): string {
  const key = Buffer.from(process.env.AES_ENCRYPTION_KEY, 'hex');
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv('aes-256-cbc', key, iv);

  let encrypted = cipher.update(pat, 'utf8', 'hex');
  encrypted += cipher.final('hex');

  // Prepend IV to ciphertext
  return iv.toString('hex') + ':' + encrypted;
}

function decryptPAT(encrypted: string): string {
  const key = Buffer.from(process.env.AES_ENCRYPTION_KEY, 'hex');
  const parts = encrypted.split(':');
  const iv = Buffer.from(parts[0], 'hex');
  const ciphertext = parts[1];

  const decipher = crypto.createDecipheriv('aes-256-cbc', key, iv);
  let decrypted = decipher.update(ciphertext, 'hex', 'utf8');
  decrypted += decipher.final('utf8');

  return decrypted;
}
```

**Security Notes:**
- PATs never sent to client
- PATs only decrypted when needed (GitHub API calls, ingestion)
- Decryption happens server-side only

---

### Access Control Matrix

| Resource | Client Access | Server Access | Filters Required |
|----------|--------------|---------------|------------------|
| `code_kosha` | ❌ Never | ✅ Yes | `WHERE user_id = $user_id` |
| `users` | ❌ Never | ✅ Yes | Email for login, user_id for updates |
| `ingestion_jobs` | ❌ Never | ✅ Yes | `WHERE user_id = $user_id` |

**Client-side code:**
- Cannot connect to Couchbase directly
- All data access via `/api/*` endpoints
- JWT required for all endpoints except `/api/auth/signup` and `/api/auth/login`

---

## Implementation Plan

### Phase 1: Database Setup

**Tasks:**
1. Create buckets via Couchbase Web UI:
   - `users` (256MB RAM quota)
   - `ingestion_jobs` (128MB RAM quota)
2. Create indexes (see SQL above)
3. Update `code_vector_index` to include `user_id` field
4. Run migration script to add `user_id` to existing chunks

**Files:**
- `2-initialize/create-user-buckets.sh`
- `2-initialize/migrate-add-user-id.js`

---

### Phase 2: SvelteKit Project Setup

**Tasks:**
1. Install dependencies:
   ```bash
   cd 4-consume/web-ui
   npm install couchbase bcrypt jsonwebtoken crypto-js
   ```
2. Create directory structure:
   ```
   src/
   ├── lib/
   │   ├── server/
   │   │   ├── db.ts           # Couchbase connection
   │   │   ├── auth.ts         # JWT + bcrypt helpers
   │   │   └── crypto.ts       # PAT encryption
   │   └── components/
   │       ├── RepoList.svelte
   │       ├── JobProgress.svelte
   │       └── SearchResults.svelte
   └── routes/
       ├── +page.svelte        # Landing
       ├── login/+page.svelte
       ├── signup/+page.svelte
       ├── dashboard/+page.svelte
       ├── repos/
       │   └── connect/+page.svelte
       ├── jobs/[id]/+page.svelte
       ├── search/+page.svelte
       └── api/
           ├── auth/
           │   ├── login/+server.ts
           │   ├── signup/+server.ts
           │   └── github-pat/+server.ts
           ├── repos/
           │   ├── discover/+server.ts
           │   └── add/+server.ts
           ├── search/+server.ts
           └── jobs/[id]/+server.ts
   ```
3. Configure environment variables in `.env`
4. Implement middleware in `src/hooks.server.ts`

---

### Phase 3: Update Ingestion Worker

**Tasks:**
1. Modify data models:
   - `lib/ingestion-worker/parsers/code_parser.py` → Add `user_id` parameter
   - `lib/ingestion-worker/parsers/document_parser.py` → Add `user_id` parameter
2. Update `CouchbaseClient`:
   - `vector_search()` → Require `user_id` parameter
   - All queries → Add `WHERE user_id = $user_id`
3. Create queue worker:
   - `lib/ingestion-worker/queue_worker.py`
   - Polls `ingestion_jobs` bucket
   - Processes jobs sequentially
4. Setup cron:
   - `3-maintain/worker-cron` → Run every 1 minute

**Files Modified:**
- `lib/ingestion-worker/worker.py`
- `lib/ingestion-worker/storage/couchbase_client.py`
- `lib/ingestion-worker/parsers/code_parser.py`
- `lib/ingestion-worker/parsers/document_parser.py`

**Files Created:**
- `lib/ingestion-worker/queue_worker.py`

---

### Phase 4: Frontend Implementation

**Tasks:**
1. Implement auth pages (signup, login)
2. Implement GitHub PAT connection
3. Implement repo discovery and selection
4. Implement job progress page with polling
5. Implement search interface with results

**Styling:**
- Use Tailwind CSS (or minimal custom CSS)
- Focus on functionality over aesthetics (internal tool)

---

## Environment Variables

Create `.env` in `4-consume/web-ui/`:

```bash
# Couchbase
COUCHBASE_HOST=localhost
COUCHBASE_PORT=8091
COUCHBASE_USER=Administrator
COUCHBASE_PASSWORD=password123

# Buckets
COUCHBASE_BUCKET_CODE=code_kosha
COUCHBASE_BUCKET_USERS=users
COUCHBASE_BUCKET_JOBS=ingestion_jobs

# Authentication
JWT_SECRET=your-random-secret-key-change-in-production
AES_ENCRYPTION_KEY=your-256-bit-hex-key-for-pat-encryption

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=nomic-embed-text

# GitHub
GITHUB_API_URL=https://api.github.com
```

**Generate AES key:**
```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

---

## Migration Steps

### 1. Create Buckets

Via Couchbase Web UI (http://localhost:8091):
1. Go to Buckets tab
2. Click "ADD BUCKET"
3. Create `users`:
   - Name: users
   - RAM Quota: 256 MB
   - Bucket Type: Couchbase
   - Replicas: 0
4. Create `ingestion_jobs`:
   - Name: ingestion_jobs
   - RAM Quota: 128 MB
   - Bucket Type: Couchbase
   - Replicas: 0

### 2. Create Indexes

Run in Couchbase Query Workbench:

```sql
-- Users bucket
CREATE PRIMARY INDEX ON users;
CREATE INDEX idx_email ON users(email) WHERE type = "user";

-- Ingestion jobs bucket
CREATE PRIMARY INDEX ON ingestion_jobs;
CREATE INDEX idx_user_jobs ON ingestion_jobs(user_id, created_at DESC)
  WHERE type = "ingestion_job";
CREATE INDEX idx_queued_jobs ON ingestion_jobs(status, created_at ASC)
  WHERE type = "ingestion_job";

-- Code kosha bucket (modify existing)
CREATE INDEX idx_user_repo ON code_kosha(user_id, repo_id, type);
```

### 3. Migrate Existing Data

Create system user and assign all existing chunks:

```javascript
// 2-initialize/migrate-add-user-id.js
import { Cluster } from 'couchbase';

const SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000";

async function migrate() {
  // Connect
  const cluster = await Cluster.connect('couchbase://localhost', {
    username: 'Administrator',
    password: 'password123'
  });

  const usersCollection = cluster.bucket('users').defaultCollection();
  const codeCollection = cluster.bucket('code_kosha').defaultCollection();

  // Create system user
  await usersCollection.insert(SYSTEM_USER_ID, {
    type: "user",
    user_id: SYSTEM_USER_ID,
    email: "system@codesmriti.internal",
    password_hash: "disabled",
    github_pat_encrypted: null,
    repos: [],
    quota_max_repos: 999999,
    quota_max_chunks: 999999999,
    created_at: new Date().toISOString()
  });

  console.log("✓ Created system user");

  // Update all existing chunks
  const result = await cluster.query(`
    UPDATE code_kosha
    SET user_id = "${SYSTEM_USER_ID}"
    WHERE user_id IS MISSING
  `);

  console.log(`✓ Migrated ${result.meta.metrics.mutationCount} chunks to system user`);

  // Get unique repos from chunks
  const repos = await cluster.query(`
    SELECT DISTINCT repo_id
    FROM code_kosha
    WHERE user_id = "${SYSTEM_USER_ID}"
  `);

  // Update system user with discovered repos
  const repoList = repos.rows.map(r => ({
    repo_id: r.repo_id,
    added_at: new Date().toISOString(),
    last_synced: new Date().toISOString(),
    chunk_count: 0, // Will be computed
    status: "synced"
  }));

  await usersCollection.upsert(SYSTEM_USER_ID, {
    ...await (await usersCollection.get(SYSTEM_USER_ID)).content,
    repos: repoList
  });

  console.log(`✓ Added ${repoList.length} repos to system user`);
}

migrate().catch(console.error);
```

Run:
```bash
cd 2-initialize
node migrate-add-user-id.js
```

### 4. Update Vector Index

Via Couchbase FTS UI (http://localhost:8094/_p/fts/):
1. Click "code_vector_index"
2. Edit index definition
3. Add `user_id` to indexed fields (see JSON above)
4. Save and rebuild index (~1-2 minutes)

---

## Testing Plan

### Unit Tests

**Authentication:**
- ✓ Signup creates user with hashed password
- ✓ Login with correct password succeeds
- ✓ Login with wrong password fails
- ✓ JWT verification works

**Data Isolation:**
- ✓ User A cannot see User B's chunks
- ✓ Vector search filtered by user_id
- ✓ Job status requires ownership

**PAT Encryption:**
- ✓ Encrypt/decrypt roundtrip works
- ✓ Decrypted PAT matches original

### Integration Tests

**End-to-End Flow:**
1. Create test user
2. Add GitHub PAT
3. Discover repos (mock GitHub API)
4. Add 1 test repo
5. Wait for ingestion to complete
6. Search for known code snippet
7. Verify results returned
8. Verify results scoped to test user

### Security Tests

**Cross-User Data Leakage:**
1. Create User A, User B
2. User A ingests repo X
3. User B searches for content from repo X
4. Verify User B gets 0 results (not User A's data)

---

## Success Metrics

**MVP Success:**
- 5+ team members onboarded
- 10+ repositories indexed
- 100+ successful searches
- <50ms average query time
- Zero cross-user data leakage incidents

---

## Future Enhancements (Post-MVP)

1. **WebSocket for real-time progress** (instead of polling)
2. **GitHub OAuth** (instead of PAT)
3. **Webhook-based sync** (instead of daily cron)
4. **Advanced filters** (by file type, date range, commit author)
5. **Code highlighting** in search results
6. **"Ask AI" feature** using search results as context for LLM
7. **Team sharing** (multiple users share same repos)
8. **Usage analytics** (popular queries, search patterns)

---

## Appendix: File Structure

```
4-consume/web-ui/
├── src/
│   ├── lib/
│   │   ├── server/
│   │   │   ├── db.ts                # Couchbase connection (3 buckets)
│   │   │   ├── auth.ts              # JWT sign/verify, bcrypt hash/compare
│   │   │   └── crypto.ts            # AES-256 encrypt/decrypt for PAT
│   │   ├── components/
│   │   │   ├── RepoList.svelte      # Display repos with status
│   │   │   ├── JobProgress.svelte   # Progress bar + status
│   │   │   ├── SearchBar.svelte     # Query input + filters
│   │   │   └── SearchResults.svelte # Code results with syntax highlighting
│   │   └── stores/
│   │       └── user.ts              # Client-side user state
│   │
│   ├── routes/
│   │   ├── +layout.svelte           # Global layout (nav, auth check)
│   │   ├── +page.svelte             # Landing page
│   │   ├── login/+page.svelte
│   │   ├── signup/+page.svelte
│   │   ├── dashboard/+page.svelte
│   │   ├── github/+page.svelte      # Add GitHub PAT
│   │   ├── repos/
│   │   │   ├── +page.svelte         # List connected repos
│   │   │   └── connect/+page.svelte # Discover + add repos
│   │   ├── jobs/
│   │   │   └── [id]/+page.svelte    # Job progress page
│   │   ├── search/+page.svelte      # Search interface
│   │   │
│   │   └── api/
│   │       ├── auth/
│   │       │   ├── login/+server.ts
│   │       │   ├── signup/+server.ts
│   │       │   └── github-pat/+server.ts
│   │       ├── repos/
│   │       │   ├── discover/+server.ts
│   │       │   └── add/+server.ts
│   │       ├── search/+server.ts
│   │       └── jobs/
│   │           └── [id]/+server.ts
│   │
│   ├── hooks.server.ts              # JWT auth middleware
│   └── app.html
│
├── static/                          # Static assets
├── .env                             # Environment variables (GITIGNORED)
├── .env.example                     # Template for .env
├── package.json
├── svelte.config.js
├── tsconfig.json
├── vite.config.ts
├── PRD.md                           # This document
└── README.md
```

---

## Questions & Decisions

**Resolved:**
- ✓ Data isolation: Shared bucket with user_id filter
- ✓ PAT encryption: AES-256-CBC with env variable key
- ✓ Job queue: Couchbase bucket, cron-based worker
- ✓ Progress updates: Polling (simple, no WebSocket complexity)
- ✓ Repo discovery: GitHub API with cached username

**Open:**
- How should we handle repo deletion? (soft delete? hard delete?)
- Should we show commit history in search results?
- Rate limiting needed for internal use?

---

**Document Version:** 1.0
**Last Updated:** 2025-11-19
**Author:** Claude Code + Kaustubh
