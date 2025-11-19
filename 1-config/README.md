# 1. Configuration

Tell CodeSmriti which repositories to index.

## Quick Start

```bash
# 1. Copy environment template
cp .env.template ../.env

# 2. Edit configuration
vim ../.env

# 3. Configure repositories
vim repos-to-ingest.txt
```

## Configuration Files

### .env (Environment Variables)

```bash
# GitHub token (required)
GITHUB_TOKEN=ghp_your_token_here

# Couchbase (defaults usually work)
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=password123
COUCHBASE_BUCKET=code_kosha

# Embedding backend (local recommended)
EMBEDDING_BACKEND=local

# Repository storage path
REPOS_PATH=/Users/yourusername/Documents/codesmriti-repos
```

### repos-to-ingest.txt

List repositories one per line:

```
owner/repo-name
kbhalerao/code-smriti
kbhalerao/labcore
organization/project
```

Comments start with `#`:
```
# Core repositories
kbhalerao/code-smriti    # Main project

# Mark completed ingestions
owner/done-repo          # [DONE]
```

## Getting Your GitHub Token

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scopes:
   - `repo` - Full control of private repositories
   - `read:org` - Read org data (if indexing org repos)
4. Generate and copy the token
5. Add to `.env`: `GITHUB_TOKEN=ghp_...`

## Examples

See `examples/` for common configurations:
- `public-repos.txt` - Public repositories only
- `org-repos.txt` - All repos from an organization
- `monorepo.txt` - Single large monorepo

## Recommendations

**Start small:** Begin with 2-3 small repositories to test the system.

**Storage path:** Use a path outside the project directory to avoid recursion:
```bash
# Good
REPOS_PATH=/Users/you/codesmriti-repos

# Bad (causes recursion if you index code-smriti itself)
REPOS_PATH=/Users/you/code-smriti/repos
```

## Next Step

→ **[2-initialize](../2-initialize/README.md)** - Run initial ingestion
