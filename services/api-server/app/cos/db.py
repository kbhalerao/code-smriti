"""Couchbase database operations for Chief of Staff.

Uses the existing CodeSmriti couchbase_client with scope-per-user multi-tenancy.
Each user gets their own scope within the chief_of_staff bucket.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from couchbase.exceptions import DocumentNotFoundException
from couchbase.management.collections import CollectionSpec
from couchbase.options import QueryOptions
from loguru import logger

from ..config import settings
from ..database import get_cluster
from .models import (
    CreateDocRequest,
    DocResponse,
    DocType,
    DocsListResponse,
    Priority,
    SaveContextRequest,
    SourceInfo,
    Status,
    UpdateDocRequest,
)


class CosDatabase:
    """Chief of Staff database operations with scope-per-user multi-tenancy."""

    def __init__(self):
        self._validated_users: set[str] = set()  # Cache of validated user_ids

    @property
    def cluster(self):
        """Get the shared Couchbase cluster."""
        return get_cluster()

    @property
    def bucket(self):
        """Get the chief_of_staff bucket."""
        return self.cluster.bucket(settings.couchbase_bucket_cos)

    @property
    def users_bucket(self):
        """Get the users bucket (for validation)."""
        return self.cluster.bucket(settings.couchbase_bucket_users)

    def _get_user_by_email(self, email: str) -> Optional[dict]:
        """Look up user by email from users bucket."""
        query = """
            SELECT META().id, u.*
            FROM `users`._default._default u
            WHERE u.email = $email AND u.type = "user"
            LIMIT 1
        """
        result = list(self.cluster.query(query, QueryOptions(named_parameters={"email": email})))
        if result:
            return result[0]
        return None

    def _ensure_user_scope(self, user_id: str) -> None:
        """Ensure scope and collection exist for user, create if not."""
        import time

        scope_name = self._get_scope_name(user_id)
        collection_mgr = self.bucket.collections()

        # Check existing scopes
        existing_scopes = [s.name for s in collection_mgr.get_all_scopes()]

        if scope_name not in existing_scopes:
            # Create scope
            logger.info(f"Creating scope {scope_name} for user {user_id}")
            collection_mgr.create_scope(scope_name)
            # Wait for scope to be ready
            time.sleep(1)

        # Check if collection exists in scope
        scope_spec = next((s for s in collection_mgr.get_all_scopes() if s.name == scope_name), None)
        existing_collections = [c.name for c in scope_spec.collections] if scope_spec else []

        if "documents" not in existing_collections:
            # Create collection
            logger.info(f"Creating documents collection in scope {scope_name}")
            collection_mgr.create_collection(CollectionSpec(scope_name=scope_name, collection_name="documents"))
            # Wait for collection to be ready
            time.sleep(1)
            # Create basic indexes
            self._create_indexes_for_user(user_id)

    def _create_indexes_for_user(self, user_id: str) -> None:
        """Create indexes for a user's documents collection."""
        fqn = self._get_fqn(user_id)
        indexes = [
            f"CREATE INDEX IF NOT EXISTS idx_doc_type ON {fqn}(doc_type)",
            f"CREATE INDEX IF NOT EXISTS idx_status ON {fqn}(status)",
            f"CREATE INDEX IF NOT EXISTS idx_updated ON {fqn}(updated_at)",
        ]
        for idx_query in indexes:
            try:
                self.cluster.query(idx_query)
            except Exception as e:
                logger.warning(f"Index creation warning: {e}")

    def _get_collection(self, user_id: str):
        """Get the documents collection for a user scope."""
        scope_name = self._get_scope_name(user_id)
        scope = self.bucket.scope(scope_name)
        return scope.collection("documents")

    def _get_scope_name(self, user_id: str) -> str:
        """Get scope name for user (sanitized email)."""
        return f"user_{user_id.replace('@', '_at_').replace('.', '_')}"

    def _get_fqn(self, user_id: str) -> str:
        """Get fully qualified name for N1QL queries."""
        scope_name = self._get_scope_name(user_id)
        return f"`{settings.couchbase_bucket_cos}`.`{scope_name}`.`documents`"

    def validate_user(self, user_id: str) -> bool:
        """Validate user exists in users bucket and ensure their scope exists."""
        if user_id in self._validated_users:
            return True

        user = self._get_user_by_email(user_id)
        if user:
            self._validated_users.add(user_id)
            self._ensure_user_scope(user_id)
            return True
        return False

    def is_connected(self) -> bool:
        """Check if connected to Couchbase."""
        try:
            self.cluster.ping()
            return True
        except Exception:
            return False

    # --- Document CRUD ---

    async def create_document(
        self, user_id: str, request: CreateDocRequest
    ) -> DocResponse:
        """Create a new document."""
        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        doc = {
            "doc_type": request.doc_type.value,
            "user_id": user_id,
            "content": request.content,
            "title": request.title,
            "tags": request.tags,
            "priority": request.priority.value if request.priority else None,
            "status": request.status.value,
            "due_date": request.due_date,
            "project_id": request.project_id,
            "parent_id": request.parent_id,
            "linked_ids": [],
            "source": request.source.model_dump() if request.source else None,
            "metadata": request.metadata,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        collection = self._get_collection(user_id)
        collection.insert(doc_id, doc)

        return self._doc_to_response(doc_id, doc)

    async def resolve_doc_id(self, user_id: str, partial_id: str) -> Optional[str]:
        """Resolve a partial ID to full document ID.

        If partial_id is a full UUID, returns it directly.
        Otherwise, searches for documents whose ID starts with partial_id.
        Returns None if no match or multiple matches found.
        """
        # If it looks like a full UUID, return as-is
        if len(partial_id) == 36 and partial_id.count("-") == 4:
            return partial_id

        # Search for documents with matching ID prefix
        fqn = self._get_fqn(user_id)
        query = f"""
            SELECT META(d).id
            FROM {fqn} d
            WHERE META(d).id LIKE $pattern
            LIMIT 2
        """
        pattern = f"{partial_id}%"
        result = list(self.cluster.query(query, QueryOptions(named_parameters={"pattern": pattern})))

        if len(result) == 1:
            return result[0]["id"]
        return None  # No match or ambiguous

    async def get_document(self, user_id: str, doc_id: str) -> Optional[DocResponse]:
        """Get a single document by ID (supports partial IDs)."""
        # Resolve partial ID
        full_id = await self.resolve_doc_id(user_id, doc_id)
        if not full_id:
            return None

        collection = self._get_collection(user_id)
        try:
            result = collection.get(full_id)
            return self._doc_to_response(full_id, result.content_as[dict])
        except DocumentNotFoundException:
            return None

    async def update_document(
        self, user_id: str, doc_id: str, request: UpdateDocRequest
    ) -> Optional[DocResponse]:
        """Update an existing document (supports partial IDs)."""
        # Resolve partial ID
        full_id = await self.resolve_doc_id(user_id, doc_id)
        if not full_id:
            return None

        collection = self._get_collection(user_id)

        try:
            result = collection.get(full_id)
            doc = result.content_as[dict]
        except DocumentNotFoundException:
            return None

        # Update fields that are provided
        if request.content is not None:
            doc["content"] = request.content
        if request.title is not None:
            doc["title"] = request.title
        if request.tags is not None:
            doc["tags"] = request.tags
        if request.priority is not None:
            doc["priority"] = request.priority.value
        if request.status is not None:
            doc["status"] = request.status.value
        if request.due_date is not None:
            doc["due_date"] = request.due_date
        if request.metadata is not None:
            doc["metadata"] = request.metadata

        doc["updated_at"] = datetime.now(timezone.utc).isoformat()

        collection.replace(full_id, doc)
        return self._doc_to_response(full_id, doc)

    async def delete_document(self, user_id: str, doc_id: str, hard: bool = False) -> bool:
        """Delete a document (soft by default - sets status to archived). Supports partial IDs."""
        # Resolve partial ID
        full_id = await self.resolve_doc_id(user_id, doc_id)
        if not full_id:
            return False

        collection = self._get_collection(user_id)

        try:
            if hard:
                collection.remove(full_id)
            else:
                result = collection.get(full_id)
                doc = result.content_as[dict]
                doc["status"] = Status.archived.value
                doc["updated_at"] = datetime.now(timezone.utc).isoformat()
                collection.replace(full_id, doc)
            return True
        except DocumentNotFoundException:
            return False

    # --- Query Methods ---

    async def list_documents(
        self,
        user_id: str,
        doc_type: Optional[DocType] = None,
        status: Optional[Status] = None,
        priority: Optional[Priority] = None,
        tags: Optional[list[str]] = None,
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "updated_at:desc",
    ) -> DocsListResponse:
        """List documents with filters."""
        fqn = self._get_fqn(user_id)

        # Build WHERE clause
        conditions = []
        params: dict[str, Any] = {}

        if doc_type:
            conditions.append("d.doc_type = $doc_type")
            params["doc_type"] = doc_type.value

        if status:
            conditions.append("d.status = $status")
            params["status"] = status.value

        if priority:
            conditions.append("d.priority = $priority")
            params["priority"] = priority.value

        if tags:
            # All tags must be present
            for i, tag in enumerate(tags):
                conditions.append(f"${f'tag{i}'} IN d.tags")
                params[f"tag{i}"] = tag

        if project:
            conditions.append("d.source.project = $project")
            params["project"] = project

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Parse sort
        sort_field, sort_dir = sort.split(":")
        sort_clause = f"d.{sort_field} {'DESC' if sort_dir == 'desc' else 'ASC'}"

        # Count query
        count_query = f"SELECT COUNT(*) as total FROM {fqn} d WHERE {where_clause}"
        count_result = self.cluster.query(count_query, QueryOptions(named_parameters=params))
        total = list(count_result)[0]["total"]

        # Data query
        query = f"""
            SELECT META(d).id, d.*
            FROM {fqn} d
            WHERE {where_clause}
            ORDER BY {sort_clause}
            LIMIT $limit OFFSET $offset
        """
        params["limit"] = limit
        params["offset"] = offset

        result = self.cluster.query(query, QueryOptions(named_parameters=params))
        items = [self._doc_to_response(row["id"], row) for row in result]

        return DocsListResponse(items=items, total=total, limit=limit, offset=offset)

    async def get_next_actions(
        self, user_id: str, limit: int = 10
    ) -> DocsListResponse:
        """Get priority queue - high priority first, then by due date."""
        fqn = self._get_fqn(user_id)

        query = f"""
            SELECT META(d).id, d.*
            FROM {fqn} d
            WHERE d.doc_type IN ["idea", "task"]
              AND d.status IN ["inbox", "todo"]
            ORDER BY
              CASE d.priority
                WHEN "high" THEN 1
                WHEN "medium" THEN 2
                WHEN "low" THEN 3
                ELSE 4
              END,
              d.due_date NULLS LAST,
              d.updated_at DESC
            LIMIT $limit
        """

        result = self.cluster.query(query, QueryOptions(named_parameters={"limit": limit}))
        items = [self._doc_to_response(row["id"], row) for row in result]

        return DocsListResponse(items=items, total=len(items), limit=limit, offset=0)

    async def get_inbox(self, user_id: str, limit: int = 50) -> DocsListResponse:
        """Get inbox items."""
        return await self.list_documents(user_id, status=Status.inbox, limit=limit)

    async def get_due_soon(
        self, user_id: str, days: int = 7, limit: int = 20
    ) -> DocsListResponse:
        """Get tasks with approaching due dates."""
        fqn = self._get_fqn(user_id)

        query = f"""
            SELECT META(d).id, d.*
            FROM {fqn} d
            WHERE d.doc_type = "task"
              AND d.status NOT IN ["done", "archived"]
              AND d.due_date IS NOT NULL
              AND d.due_date <= DATE_ADD_STR(NOW_STR(), $days, "day")
            ORDER BY d.due_date ASC
            LIMIT $limit
        """

        result = self.cluster.query(
            query, QueryOptions(named_parameters={"days": days, "limit": limit})
        )
        items = [self._doc_to_response(row["id"], row) for row in result]

        return DocsListResponse(items=items, total=len(items), limit=limit, offset=0)

    async def get_project_docs(
        self, user_id: str, project_name: str, limit: int = 50
    ) -> DocsListResponse:
        """Get all docs for a project."""
        return await self.list_documents(user_id, project=project_name, limit=limit)

    async def get_project_recent(
        self, user_id: str, project_name: str, limit: int = 10
    ) -> DocsListResponse:
        """Get recent activity for a project."""
        return await self.list_documents(
            user_id, project=project_name, limit=limit, sort="updated_at:desc"
        )

    # --- Tags ---

    async def get_tags(self, user_id: str) -> list[dict]:
        """Get all tags with counts."""
        fqn = self._get_fqn(user_id)

        query = f"""
            SELECT t AS tag, COUNT(*) AS count
            FROM {fqn} d
            UNNEST d.tags t
            WHERE d.status NOT IN ["archived"]
            GROUP BY t
            ORDER BY count DESC
        """

        result = self.cluster.query(query)
        return [{"tag": row["tag"], "count": row["count"]} for row in result]

    # --- Stats ---

    async def get_stats(self, user_id: str) -> dict:
        """Get statistics."""
        fqn = self._get_fqn(user_id)

        # Total count
        total_query = f"SELECT COUNT(*) as total FROM {fqn}"
        total = list(self.cluster.query(total_query))[0]["total"]

        # By doc_type
        type_query = f"""
            SELECT d.doc_type, COUNT(*) as count
            FROM {fqn} d
            GROUP BY d.doc_type
        """
        by_type = {row["doc_type"]: row["count"] for row in self.cluster.query(type_query)}

        # By status
        status_query = f"""
            SELECT d.status, COUNT(*) as count
            FROM {fqn} d
            GROUP BY d.status
        """
        by_status = {row["status"]: row["count"] for row in self.cluster.query(status_query)}

        # By priority
        priority_query = f"""
            SELECT d.priority, COUNT(*) as count
            FROM {fqn} d
            WHERE d.priority IS NOT NULL
            GROUP BY d.priority
        """
        by_priority = {row["priority"]: row["count"] for row in self.cluster.query(priority_query)}

        # Recent activity (last 24h)
        recent_query = f"""
            SELECT COUNT(*) as count
            FROM {fqn} d
            WHERE d.updated_at >= DATE_ADD_STR(NOW_STR(), -1, "day")
        """
        recent = list(self.cluster.query(recent_query))[0]["count"]

        return {
            "total_docs": total,
            "by_doc_type": by_type,
            "by_status": by_status,
            "by_priority": by_priority,
            "recent_activity": recent,
        }

    # --- Context ---

    async def get_latest_context(
        self, user_id: str, project: Optional[str] = None
    ) -> Optional[DocResponse]:
        """Get most recent context snapshot."""
        fqn = self._get_fqn(user_id)

        conditions = ['d.doc_type = "context"']
        params: dict[str, Any] = {"limit": 1}

        if project:
            conditions.append("d.source.project = $project")
            params["project"] = project

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT META(d).id, d.*
            FROM {fqn} d
            WHERE {where_clause}
            ORDER BY d.created_at DESC
            LIMIT $limit
        """

        result = list(self.cluster.query(query, QueryOptions(named_parameters=params)))
        if result:
            return self._doc_to_response(result[0]["id"], result[0])
        return None

    async def save_context(
        self, user_id: str, request: SaveContextRequest
    ) -> DocResponse:
        """Save a new context snapshot."""
        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        doc = {
            "doc_type": DocType.context.value,
            "user_id": user_id,
            "content": request.summary,
            "title": f"Context: {request.project or 'General'}",
            "tags": request.key_topics,
            "priority": None,
            "status": Status.done.value,
            "due_date": None,
            "project_id": None,
            "parent_id": None,
            "linked_ids": [],
            "source": {
                "client": "claude-code",
                "project": request.project,
                "files": request.files_modified,
            },
            "metadata": {
                "key_topics": request.key_topics,
                "files_modified": request.files_modified,
                "open_questions": request.open_questions,
                **request.metadata,
            },
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        collection = self._get_collection(user_id)
        collection.insert(doc_id, doc)

        return self._doc_to_response(doc_id, doc)

    # --- Helpers ---

    def _doc_to_response(self, doc_id: str, doc: dict) -> DocResponse:
        """Convert a raw document to DocResponse."""
        return DocResponse(
            id=doc_id,
            doc_type=DocType(doc["doc_type"]),
            user_id=doc["user_id"],
            content=doc["content"],
            title=doc.get("title"),
            tags=doc.get("tags", []),
            priority=Priority(doc["priority"]) if doc.get("priority") else None,
            status=Status(doc["status"]),
            due_date=doc.get("due_date"),
            project_id=doc.get("project_id"),
            parent_id=doc.get("parent_id"),
            linked_ids=doc.get("linked_ids", []),
            source=SourceInfo(**doc["source"]) if doc.get("source") else None,
            metadata=doc.get("metadata", {}),
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=datetime.fromisoformat(doc["updated_at"]),
        )


# Global instance
cos_db = CosDatabase()


def get_cos_db() -> CosDatabase:
    """Get CoS database dependency."""
    return cos_db
