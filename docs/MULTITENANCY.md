# Multitenancy Architecture Plan

## Current State (Single-Tenant)

**Data Model:**
```python
CodeChunk:
  - chunk_id: hash(repo:file:commit:content)
  - type: "code_chunk"
  - repo_id: str
  - file_path: str
  - code_text: str
  - embedding: [768 floats]
  # NO USER_ID ❌
```

**Current Limitations:**
- ❌ No user isolation - all data in one bucket
- ❌ No authentication - anyone can query everything
- ❌ No per-user repo configuration
- ❌ No GitHub PAT storage per user
- ❌ Single ingestion pipeline for one set of repos

## Multitenancy Requirements

### User Journey
1. **Sign up** → Create account (email/password or OAuth)
2. **Add GitHub PAT** → Store encrypted token
3. **Select repos** → Choose from GitHub repos accessible via PAT
4. **Trigger ingestion** → Queue job to clone + parse + embed repos
5. **Monitor progress** → Real-time status (repo 3/10, 1,234 chunks processed)
6. **Query knowledge** → Vector search filtered to user's repos only

### Key Features
- **Data Isolation:** Users only see their own code
- **Secure PAT Storage:** GitHub tokens encrypted at rest
- **Async Ingestion:** Background workers process user repos
- **Resource Limits:** Quota per user (max repos, max chunks)
- **Usage Tracking:** Monitor costs per user

## Architecture Design

### 1. Data Isolation Strategy

**Option A: Separate Buckets (NOT RECOMMENDED)**
- One Couchbase bucket per user
- Pros: Complete isolation, easy to delete user data
- Cons: Expensive (100 users = 100 buckets), complex management

**Option B: Shared Bucket with user_id Filter (RECOMMENDED)**
- One bucket, add `user_id` to every document
- Filter ALL queries by user_id
- Pros: Simple, cost-effective, scales to 1000s of users
- Cons: Requires careful query filtering (security risk if missed)

**Option C: Separate Vector Indexes (MIDDLE GROUND)**
- Shared bucket, one vector index per user
- Pros: Better isolation than B, faster than A
- Cons: Index creation overhead, complex management

**Decision: Option B (Shared Bucket + user_id)**
- Most cost-effective
- Proven pattern (multi-tenant SaaS)
- Add comprehensive test coverage for query filtering

### 2. Updated Data Model

```python
# Add to CodeChunk, DocumentChunk, CommitChunk
class CodeChunk:
    def __init__(self, user_id: str, ...):  # NEW FIELD
        self.user_id = user_id              # NEW
        self.chunk_id = hash(...)
        self.type = "code_chunk"
        self.repo_id = str
        # ... existing fields

    def to_dict(self):
        return {
            "user_id": self.user_id,        # NEW
            "chunk_id": self.chunk_id,
            # ... rest
        }

# New: User document
class User:
    user_id: str                    # UUID
    email: str
    password_hash: str              # bcrypt
    github_pat_encrypted: str       # AES-256 encrypted
    created_at: datetime
    quota_max_repos: int            # Default: 10
    quota_max_chunks: int           # Default: 50,000
    subscription_tier: str          # free, pro, enterprise

# New: Ingestion Job document
class IngestionJob:
    job_id: str                     # UUID
    user_id: str                    # FK to User
    status: str                     # queued, running, completed, failed
    repos: List[str]                # ["owner/repo1", "owner/repo2"]
    progress: dict                  # {current_repo: 3, total_repos: 10, chunks: 1234}
    started_at: datetime
    completed_at: datetime
    error: str                      # If failed
```

### 3. Updated Couchbase Queries

**Before (Single-Tenant):**
```sql
SELECT * FROM code_kosha
WHERE repo_id = $repo_id
```

**After (Multi-Tenant):**
```sql
SELECT * FROM code_kosha
WHERE user_id = $user_id              -- NEW: ALWAYS REQUIRED
  AND repo_id = $repo_id
```

**Vector Search Filter:**
```python
def vector_search(user_id: str, query_vector: List[float], ...):
    search_request = {
        "knn": [{"field": "embedding", "vector": query_vector, "k": k}],
        "query": {
            "conjuncts": [
                {"field": "user_id", "match": user_id},  # NEW: CRITICAL
                # ... other filters
            ]
        }
    }
```

### 4. Authentication & Authorization

**Tech Stack:**
- **Web Framework:** FastAPI (Python) - async, type-safe, auto docs
- **Auth:** JWT tokens (access + refresh)
- **Password:** bcrypt hashing
- **GitHub PAT:** AES-256 encryption with per-user salt

**Endpoints:**
```python
POST /auth/signup
  Body: {email, password}
  Returns: {user_id, access_token, refresh_token}

POST /auth/login
  Body: {email, password}
  Returns: {access_token, refresh_token}

POST /auth/github-pat
  Headers: {Authorization: Bearer <token>}
  Body: {github_pat}
  Returns: {success: true}

GET /auth/me
  Headers: {Authorization: Bearer <token>}
  Returns: {user_id, email, quota, subscription_tier}
```

**Security:**
- Rate limiting: 10 requests/min per IP for signup/login
- PAT encryption: Store AES key in environment variable
- JWT expiry: Access token 1 hour, refresh token 7 days

### 5. Ingestion Queue System

**Tech Stack:**
- **Queue:** Redis (in-memory, fast)
- **Workers:** Celery (Python async task queue)
- **Progress:** WebSocket or SSE (Server-Sent Events) for real-time updates

**Flow:**
```
User → Web UI (select repos)
     → API Gateway (create IngestionJob)
     → Redis Queue (add job)
     → Celery Worker (picks up job)
          1. Clone repos (user's GitHub PAT)
          2. Parse code (CodeParser)
          3. Generate embeddings (EmbeddingGenerator)
          4. Store chunks (CouchbaseClient.batch_upsert)
          5. Update job status (completed)
     → User sees completion (WebSocket notification)
```

**Job Schema:**
```python
{
  "job_id": "uuid-1234",
  "user_id": "user-5678",
  "status": "running",
  "repos": ["owner/repo1", "owner/repo2"],
  "progress": {
    "current_repo_index": 1,
    "current_repo_name": "owner/repo2",
    "total_repos": 2,
    "chunks_processed": 1234,
    "files_processed": 45
  },
  "started_at": "2025-11-19T10:00:00Z",
  "estimated_completion": "2025-11-19T10:15:00Z"
}
```

**Worker Code:**
```python
@celery.app.task
def process_ingestion_job(job_id: str):
    job = db.get_job(job_id)
    user = db.get_user(job.user_id)
    github_pat = decrypt_pat(user.github_pat_encrypted)

    for i, repo in enumerate(job.repos):
        # Update progress
        update_job_progress(job_id, current_repo=i, total=len(job.repos))

        # Run ingestion for this repo
        worker = IngestionWorker(
            user_id=job.user_id,  # NEW: Pass user_id
            repo_url=repo,
            github_token=github_pat
        )
        worker.process_repository()

    # Mark job complete
    update_job_status(job_id, "completed")
```

### 6. Web Interface Architecture

**Tech Stack:**
- **Frontend:** React + TypeScript + Tailwind CSS
- **State:** React Query (server state) + Zustand (client state)
- **Build:** Vite (fast dev server)

**Pages:**

**1. Landing Page** (`/`)
- Hero: "Chat with Your Code in Minutes"
- CTA: "Get Started Free"
- Features: Vector search, multi-repo, instant answers

**2. Sign Up** (`/signup`)
- Email + password
- OAuth option (GitHub, Google)

**3. Dashboard** (`/dashboard`)
- Quota usage: "2,345 / 50,000 chunks used"
- Recent queries
- Repos connected: 3/10

**4. Connect Repos** (`/repos/connect`)
```
Step 1: Add GitHub PAT
  [Input field: paste PAT]
  [Button: Verify & Save]

Step 2: Select Repos
  [ ] owner/repo1 (1,234 files, ~5K chunks)
  [x] owner/repo2 (456 files, ~2K chunks)
  [ ] owner/repo3 (789 files, ~3K chunks)

  [Button: Start Ingestion (2 repos selected)]
```

**5. Ingestion Progress** (`/repos/ingestion/:job_id`)
```
Processing 2 repositories...

✓ owner/repo1 (completed in 2m 34s)
  - 1,234 chunks created
  - Languages: Python (80%), JS (20%)

⏳ owner/repo2 (in progress...)
  - Processing file 45/456
  - 892 chunks created so far

Estimated completion: 3 minutes
```

**6. Query Interface** (`/query`)
```
[Search bar: "How do we handle authentication?"]
[Filters:
  Repos: [All] or [repo1, repo2]
  Languages: [All] or [Python, JS]
]

Results:
1. auth/middleware.py:15 (repo1)
   def authenticate_user(token: str):
       # Verify JWT token and extract user_id
       ...
   Similarity: 94%

2. utils/auth.py:42 (repo2)
   class JWTValidator:
       ...
   Similarity: 87%
```

### 7. Resource Limits & Quotas

**Free Tier:**
- 10 repos max
- 50,000 chunks max
- 100 queries/day

**Pro Tier ($20/month):**
- 100 repos max
- 500,000 chunks max
- Unlimited queries

**Enterprise:**
- Custom limits
- Dedicated infrastructure
- SLA

**Enforcement:**
```python
def check_quota(user_id: str, action: str):
    user = get_user(user_id)
    current_usage = get_usage(user_id)

    if action == "add_repo":
        if current_usage.repos >= user.quota_max_repos:
            raise QuotaExceededError("Max repos reached")

    if action == "ingest_chunk":
        if current_usage.chunks >= user.quota_max_chunks:
            raise QuotaExceededError("Max chunks reached")
```

## Migration Plan

### Phase 1: Add user_id to Data Model (No Breaking Changes)
1. Update CodeChunk, DocumentChunk to accept optional `user_id`
2. Default to "system" user if not provided
3. Update CouchbaseClient.batch_upsert to handle user_id
4. Deploy - existing code continues to work

### Phase 2: Create User Management
1. Create User table in Couchbase
2. Add auth endpoints (signup, login, JWT)
3. Add GitHub PAT storage (encrypted)
4. Deploy web UI for signup/login

### Phase 3: Add Ingestion Queue
1. Set up Redis + Celery workers
2. Create IngestionJob table
3. Add job endpoints (create, status, cancel)
4. Update ingestion worker to accept user_id
5. Deploy web UI for repo selection + progress

### Phase 4: Update Vector Search
1. Add user_id filter to all vector_search calls
2. Update MCP server to extract user from session
3. Update API Gateway to require auth
4. Deploy - now fully multi-tenant

### Phase 5: Migration Script
1. Migrate existing chunks to "system" user
2. Create admin user for existing data
3. Test queries still work

## Security Considerations

### Critical Safeguards
1. **NEVER return chunks without user_id filter** - could leak data
2. **Always validate JWT** - check expiry, signature
3. **Encrypt GitHub PATs** - AES-256, unique salt per user
4. **Rate limit auth endpoints** - prevent brute force
5. **Sanitize repo URLs** - prevent command injection
6. **Validate vector queries** - prevent injection attacks

### Testing Requirements
- Unit tests for user_id filtering in EVERY query function
- Integration tests for cross-user data isolation
- Security audit before production launch

## Cost Estimation

**Per User (Free Tier):**
- Storage: 50K chunks × 1KB = 50MB → $0.002/month
- Compute: 100 queries × 50ms = 5 CPU-seconds/day → $0.01/month
- **Total: ~$0.01/user/month**

**1000 Users:**
- Storage: 50GB → $2/month
- Compute: negligible (async workers)
- Redis: $10/month
- **Total: ~$12/month + server costs**

## Open Questions

1. **OAuth or just email/password?**
   - OAuth adds complexity but better UX
   - Start with email/password, add OAuth later?

2. **WebSocket or SSE for progress?**
   - WebSocket: bidirectional, more complex
   - SSE: simpler, one-way (sufficient for progress)

3. **Delete user data workflow?**
   - GDPR compliance requires full deletion
   - Cron job or immediate deletion?

4. **Shared embedding model or per-user?**
   - Shared: faster, cheaper (current approach)
   - Per-user: supports custom models (future)

5. **API rate limits?**
   - Free: 100 queries/day
   - Pro: unlimited or higher limit?

## Next Steps

1. Get user feedback on this plan
2. Decide on OAuth vs email/password
3. Decide on WebSocket vs SSE
4. Start Phase 1: Add user_id to data model
5. Prototype web UI mockups
