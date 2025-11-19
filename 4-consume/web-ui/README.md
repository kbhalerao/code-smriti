# CodeSmriti Web UI

Multi-tenant web interface for CodeSmriti - enables team members to connect GitHub repositories, ingest code, and search using natural language.

## Quick Start

```bash
# Install dependencies
npm install

# Set up environment
cp .env.example .env
# Edit .env with your Couchbase credentials

# Start development server
npm run dev
```

Visit http://localhost:5173

## Architecture

See [PRD.md](./PRD.md) for comprehensive documentation covering:
- Database schema (3 Couchbase buckets)
- API endpoints
- Security model
- User flows
- Implementation plan

## Key Features

- **Email/password authentication** with JWT
- **GitHub integration** - connect repos via Personal Access Token
- **Background ingestion** - async job processing with real-time progress
- **Vector search** - semantic code search scoped to user's repos
- **Multi-tenant** - complete data isolation between users

## Project Structure

```
src/
├── lib/
│   ├── server/         # Server-only code (Couchbase, auth, crypto)
│   └── components/     # Svelte components
└── routes/
    ├── api/            # API endpoints (+server.ts files)
    ├── login/          # Auth pages
    ├── dashboard/      # User dashboard
    ├── repos/          # Repo management
    ├── jobs/           # Job progress
    └── search/         # Search interface
```

## Environment Variables

Required in `.env`:

```bash
# Couchbase
COUCHBASE_HOST=localhost
COUCHBASE_USER=Administrator
COUCHBASE_PASSWORD=password123

# Buckets
COUCHBASE_BUCKET_CODE=code_kosha
COUCHBASE_BUCKET_USERS=users
COUCHBASE_BUCKET_JOBS=ingestion_jobs

# Security
JWT_SECRET=your-secret-key
AES_ENCRYPTION_KEY=your-256-bit-hex-key

# Ollama
OLLAMA_HOST=http://localhost:11434
```

Generate AES key:
```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

## Development

```bash
# Run dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## Security

- **Data isolation**: All queries filtered by user_id
- **PAT encryption**: GitHub tokens encrypted with AES-256-CBC
- **JWT auth**: 24-hour expiry, httpOnly cookies
- **Server-side only**: No direct Couchbase access from client

## Next Steps

1. Complete database setup (see PRD.md Phase 1)
2. Implement server endpoints (see PRD.md Phase 2)
3. Build frontend pages (see PRD.md Phase 4)
4. Test with internal team

## Documentation

- [PRD.md](./PRD.md) - Comprehensive product requirements
- [../../../docs/MULTITENANCY.md](../../../docs/MULTITENANCY.md) - Original architecture proposal
- [../../README.md](../../README.md) - Consumer layer overview
