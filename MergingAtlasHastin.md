# Resource Allocation & CoS Migration to Atlas Backend

## 1. The Problem Atlas Solves

Atlas PMP is the strategic layer for Project Managers and principals. It handles the full project lifecycle:

**Sprint Setup (Proactive Planning)**
- Requirements parsed by LLM → generates sprint plan
- Tasks created in Basecamp (with unique IDs) → linked to team members
- Anticipated artifacts (Figma, GitHub) marked as "pending" in metadata
- LLM auto-generates acceptance tests:
  - Internal (e.g., "PR merged, 2 reviewers") → for dev team
  - Client-facing (e.g., "Figma prototype approved") → for stakeholders

**Sprint Execution (Tracking Progress)**
- Devs attach artifacts (GitHub PR, Figma link) → system logs timestamp/metadata
- Incomplete todos flagged in real time → PM dashboard shows "pending" or "at risk"
- LLM monitors artifact links and updates task status

**Reconciliation & Validation (ATX Tracker)**
- At sprint end, LLM performs cross-referencing:
  - Does the PR match the task description?
  - Is the Figma file aligned with acceptance criteria?
  - Do timesheets match actual work?
- Conflicts flagged for PM review → PM manually resolves via UI

**Audit & Reporting**
- PM reviews LLM findings and manual overrides
- Finalized status → stored as audit record
- Reports: completion rates, time variance, delivery delays

**Retention & Traceability**
- All artifacts, LLM decisions, PM overrides, acceptance tests stored indefinitely
- Archived but searchable via task ID, user, date

### The ATX Tracking System

ATX (Artifact-Task-Timesheet) is the reconciliation backbone of Atlas. An **ATX ID** is generated at sprint planning time and serves as the correlation key across all work artifacts.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ATX ID: atx_2025q1_fw_0042                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PLANNING (Sprint Setup)                                            │
│  ├─ Task: "Implement OAuth flow"                                    │
│  ├─ Assignee: Akash                                                 │
│  ├─ Estimated hours: 16                                             │
│  ├─ Acceptance criteria (internal): PR merged, 2 reviewers          │
│  └─ Acceptance criteria (client): Login works with Microsoft        │
│                                                                     │
│  BASECAMP SYNC                                                      │
│  ├─ Todo ID: bc_9050406952                                          │
│  ├─ Todo List: "Sprint 3 - Auth"                                    │
│  └─ Status: in_progress → completed                                 │
│                                                                     │
│  ARTIFACTS DELIVERED                                                │
│  ├─ GitHub PR: #247 (merged 2025-01-15)                             │
│  ├─ Figma: design/oauth-flow-v2                                     │
│  └─ Documentation: docs/auth/oauth.md                               │
│                                                                     │
│  TIMESHEET ENTRIES                                                  │
│  ├─ 2025-01-10: 4h "OAuth research"                                 │
│  ├─ 2025-01-11: 6h "Implementation"                                 │
│  ├─ 2025-01-12: 4h "Testing + PR"                                   │
│  └─ Total: 14h (vs 16h estimated = -12.5% variance)                 │
│                                                                     │
│  LLM VALIDATION                                                     │
│  ├─ PR #247 matches task description? ✓                             │
│  ├─ Acceptance criteria met? ✓                                      │
│  ├─ Time variance acceptable? ✓ (within 15%)                        │
│  └─ Status: AUTO_APPROVED                                           │
│                                                                     │
│  PM OVERRIDE (if needed)                                            │
│  └─ None                                                            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**ATX ID Generation Rules:**
- Format: `atx_{period}_{project_code}_{sequence}`
- Generated when: Task created during sprint planning
- Immutable once created
- Links to: Basecamp todo, GitHub PRs/commits, Figma files, timesheet entries

**ATX Reconciliation Flow:**
```
Sprint Planning          Execution               Reconciliation
     │                       │                        │
     ▼                       ▼                        ▼
┌─────────┐            ┌──────────┐            ┌───────────┐
│ ATX ID  │───────────▶│ Basecamp │───────────▶│   ATX     │
│ Created │            │ Todo     │            │  Tracker  │
└─────────┘            └──────────┘            └───────────┘
     │                       │                        │
     │                       ▼                        │
     │                 ┌──────────┐                   │
     │                 │ Artifact │                   │
     │                 │ Links    │───────────────────┤
     │                 └──────────┘                   │
     │                       │                        │
     │                       ▼                        │
     │                 ┌──────────┐                   │
     └────────────────▶│Timesheet │───────────────────┘
                       │ Entries  │
                       └──────────┘
```

**Conflict Detection:**
- One artifact satisfies multiple tasks → Flag for PM
- Time logged exceeds allocation → Alert
- Task completed but no artifact linked → Warning
- Artifact linked but task not marked complete → Warning

---

## 2. What Chief of Staff Provides

Chief of Staff (CoS) is **Hastin** (Sanskrit: "the extra hand") - the tactical layer that keeps developers on task.

**Current Capabilities:**
- Task/idea/note management with flexible statuses and priorities
- Telegram integration for focused, mobile-friendly interaction
- Project and tag organization
- Assignment delegation with approval workflows
- Context snapshots for LLM memory continuity

**Agentic Integration:**
- CoS connects to **Code-Smriti** providing an agentic paired partner for developers
- LLM-powered chat interface with tool calling for task operations
- Natural language queries: "What's blocking the auth refactor?" → structured response

**Audience Distinction:**
| System | Audience | Purpose |
|--------|----------|---------|
| Atlas | PMs, principals | Strategic planning, reconciliation, billing, audit |
| CoS | Developers, clients | Tactical execution, daily focus, agentic assistance |

**The Convergence:**
- Both systems will share a PostgreSQL backend
- Atlas provides the structured PM views and Basecamp integration
- CoS provides the chat/Telegram interface and developer-friendly UX
- Same data, different lenses

---

## 3. The Capacity Planning Problem

Neither Atlas nor CoS currently solves **quarterly resource allocation** for retainer projects.

**The Question:** "Who is allocated to which projects, for how much time, in what role, for what period?"

**Current Gap:**
- Atlas has `ProjectTeamMember` (binary membership) but no allocation fields
- CoS has project membership but no effort tracking
- No system answers: "Is Akash overallocated?" or "Who has Django bandwidth?"

**The Data (from spreadsheet):**
```
| Project           | Personnel | Effort (d/wk) | Role      |
|-------------------|-----------|---------------|-----------|
| FarmWorth Backend | Akash     | 2             | SW Lead   |
| FarmWorth Backend | Emanuel   | 0.2           | DevOps    |
| Farmland Insights | Akash     | 1             | Support   |
| Farmland Insights | Chetan    | 2             | SW Lead   |
| Airscout          | Akash     | 2             | Support   |
```

**Key Characteristics:**
- Maintained by principals quarterly (or on roster change)
- Person can span multiple projects with different roles
- Effort is at project level (not task level) - the "budget" before tasks exist
- Skills are free-form tags on the person
- Feeds into: sprint planning, capacity queries, ATX reconciliation

**Views Needed:**
1. **By Project:** "Who's on FarmWorth Backend, for how much, doing what?"
2. **By Person:** "What's Akash's total load? Is he overallocated?"
3. **By Capacity:** "Who has 2+ uncommitted days this quarter?"
4. **By Skill:** "Who can do Django work and has bandwidth?"

---

## 4. Models Required for Capacity Planning

### 4.1 ProjectAllocation (NEW - the core model)

The M2M through model capturing who works on what, for how much, in what role.

```python
class ProjectAllocation(TimeStampedModel, SoftDeleteModel):
    """
    Resource allocation: who is assigned to which project,
    with what effort and role, for what period.
    """
    project = models.ForeignKey(
        'CompanyProject',
        on_delete=models.CASCADE,
        related_name='allocations'
    )
    team_member = models.ForeignKey(
        'users.TeamMember',
        on_delete=models.CASCADE,
        related_name='allocations'
    )

    # Allocation details
    role = models.CharField(
        max_length=100,
        help_text="Contextual role on this project: SW Lead, Support, Advisory, QA, etc."
    )
    effort_days_per_week = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        help_text="Allocated days per week (0.25 to 5.0)"
    )

    # Period
    period_start = models.DateField(
        help_text="Allocation start date (typically quarter start)"
    )
    period_end = models.DateField(
        null=True,
        blank=True,
        help_text="Allocation end date (null = ongoing)"
    )

    # Status
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('planned', 'Planned'),
        ('ended', 'Ended'),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )

    # Audit
    allocated_by = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.SET_NULL,
        null=True,
        related_name='allocations_made'
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ('project', 'team_member', 'period_start')
        ordering = ['-period_start', 'project__name']
```

### 4.2 TeamMember Extensions (EXISTS - minor additions)

```python
# Already exists: tech_stack (JSONField) for skills
# Add if not present:
class TeamMember(models.Model):
    # ... existing fields ...

    # Skills (already exists as tech_stack, but clarify usage)
    tech_stack = models.JSONField(
        blank=True,
        null=True,
        help_text="Skills/competencies as list: ['Django', 'React', 'AWS']"
    )

    # Capacity (optional, for default availability)
    default_hours_per_week = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=40.0,
        help_text="Standard available hours per week"
    )
```

### 4.3 TimeEntry (NEW - for actuals tracking)

```python
class TimeEntry(TimeStampedModel):
    """
    Actual time logged, synced from Basecamp timesheets.
    """
    team_member = models.ForeignKey(
        'users.TeamMember',
        on_delete=models.CASCADE,
        related_name='time_entries'
    )
    project = models.ForeignKey(
        'CompanyProject',
        on_delete=models.CASCADE,
        related_name='time_entries'
    )

    date = models.DateField()
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    description = models.TextField(blank=True)

    # Basecamp sync
    basecamp_id = models.BigIntegerField(null=True, blank=True, unique=True)
    INSERT_CHOICES = (
        ('basecamp', 'Basecamp'),
        ('manual', 'Manual'),
    )
    inserted_via = models.CharField(
        max_length=20,
        choices=INSERT_CHOICES,
        default='manual'
    )

    # ATX linkage (optional - for reconciliation)
    atx_record = models.ForeignKey(
        'ATXRecord',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='time_entries'
    )

    class Meta:
        unique_together = ('team_member', 'project', 'date', 'basecamp_id')
        ordering = ['-date']
```

### 4.4 ATXRecord (EXTENDS EXISTING - reconciliation backbone)

**What exists:** `ATXTodoMapping` in `integrations/github/models.py`:
```python
# EXISTING MODEL (minimal)
class ATXTodoMapping(models.Model):
    atx_id = models.CharField(max_length=64, null=True, blank=True)
    todo_list = models.ForeignKey(TodoList, on_delete=models.CASCADE)
    repository = models.ManyToManyField(GithubRepository, blank=True)
    created_by = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    # generate_atx_id() method: creates "ATX_PROJ/XXXXX" format
```

**Gap:** Current model only links TodoList → Repository. Missing:
- Task-level granularity (links to TodoList, not individual Todo items)
- Acceptance criteria (internal/client-facing)
- Estimated hours and variance tracking
- LLM validation results storage
- PM override tracking
- Basecamp timesheet linkage

**Recommendation:** Extend `ATXTodoMapping` OR create new `ATXRecord` model that supersedes it.

The ATX ID is the correlation key that ties together tasks, artifacts, and timesheets for LLM-assisted reconciliation.

```python
class ATXRecord(TimeStampedModel, SoftDeleteModel):
    # NOTE: Consider migrating data from ATXTodoMapping to this model
    """
    ATX (Artifact-Task-Timesheet) tracking record.
    Generated at sprint planning, links all work artifacts for reconciliation.
    """
    # Identity
    atx_id = models.CharField(
        max_length=50,
        unique=True,
        help_text="Format: atx_{period}_{project_code}_{sequence}"
    )
    project = models.ForeignKey(
        'CompanyProject',
        on_delete=models.CASCADE,
        related_name='atx_records'
    )
    sprint = models.ForeignKey(
        'SprintHistory',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='atx_records'
    )

    # Task definition (from sprint planning)
    task_title = models.CharField(max_length=500)
    task_description = models.TextField(blank=True)
    assignee = models.ForeignKey(
        'users.TeamMember',
        on_delete=models.SET_NULL,
        null=True,
        related_name='atx_assignments'
    )
    estimated_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True
    )

    # Acceptance criteria
    acceptance_internal = models.TextField(
        blank=True,
        help_text="Internal criteria: PR merged, tests pass, etc."
    )
    acceptance_client = models.TextField(
        blank=True,
        help_text="Client-facing criteria: feature works as specified"
    )
    CRITERIA_TYPE_CHOICES = (
        ('internal', 'Internal Only'),
        ('client', 'Client-Facing'),
        ('both', 'Both'),
    )
    criteria_type = models.CharField(
        max_length=20,
        choices=CRITERIA_TYPE_CHOICES,
        default='internal'
    )

    # Basecamp linkage
    basecamp_todo = models.ForeignKey(
        'Todo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='atx_records'
    )
    basecamp_todo_id = models.BigIntegerField(null=True, blank=True)

    # Reconciliation status
    STATUS_CHOICES = (
        ('planning', 'Planning'),
        ('in_progress', 'In Progress'),
        ('pending_review', 'Pending Review'),
        ('auto_approved', 'Auto Approved'),
        ('pm_approved', 'PM Approved'),
        ('pm_rejected', 'PM Rejected'),
        ('conflict', 'Has Conflicts'),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='planning'
    )

    # LLM validation results
    llm_validation = models.JSONField(
        default=dict,
        blank=True,
        help_text="LLM validation results: artifact_match, criteria_met, time_variance, etc."
    )
    validation_timestamp = models.DateTimeField(null=True, blank=True)

    # PM override
    pm_override = models.JSONField(
        default=dict,
        blank=True,
        help_text="PM manual decisions: action, reason, timestamp"
    )
    pm_override_by = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='atx_overrides'
    )

    # Computed fields (denormalized for reporting)
    actual_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Sum of linked TimeEntry hours"
    )
    variance_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="(actual - estimated) / estimated * 100"
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = "ATX Record"
        verbose_name_plural = "ATX Records"

    def __str__(self):
        return f"{self.atx_id}: {self.task_title}"

    def calculate_variance(self):
        """Recalculate variance from linked time entries."""
        from django.db.models import Sum
        total = self.time_entries.aggregate(Sum('hours'))['hours__sum'] or 0
        self.actual_hours = total
        if self.estimated_hours and self.estimated_hours > 0:
            self.variance_percent = ((total - self.estimated_hours) / self.estimated_hours) * 100
        self.save(update_fields=['actual_hours', 'variance_percent'])


class ATXArtifact(TimeStampedModel):
    """
    Artifacts linked to an ATX record (PRs, Figma files, docs, etc.)
    """
    ARTIFACT_TYPE_CHOICES = (
        ('github_pr', 'GitHub Pull Request'),
        ('github_commit', 'GitHub Commit'),
        ('figma', 'Figma Design'),
        ('document', 'Documentation'),
        ('other', 'Other'),
    )

    atx_record = models.ForeignKey(
        ATXRecord,
        on_delete=models.CASCADE,
        related_name='artifacts'
    )
    artifact_type = models.CharField(max_length=20, choices=ARTIFACT_TYPE_CHOICES)
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=1000)
    external_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="External system ID (PR number, commit SHA, etc.)"
    )

    # Metadata from external system
    metadata = models.JSONField(default=dict, blank=True)

    # Validation
    is_validated = models.BooleanField(default=False)
    validation_notes = models.TextField(blank=True)

    class Meta:
        ordering = ['artifact_type', '-created_at']
```

### 4.5 ATX ID Generation

```python
# utils/atx.py

def generate_atx_id(project, period=None):
    """
    Generate a unique ATX ID for a task.

    Format: atx_{period}_{project_code}_{sequence}
    Example: atx_2025q1_fw_0042

    Args:
        project: CompanyProject instance
        period: Optional period string (default: current quarter)

    Returns:
        str: Unique ATX ID
    """
    from django.utils import timezone
    from .models import ATXRecord

    if period is None:
        now = timezone.now()
        quarter = (now.month - 1) // 3 + 1
        period = f"{now.year}q{quarter}"

    # Generate project code (first 2-3 chars of project name)
    project_code = ''.join(
        word[0].lower() for word in project.name.split()[:3]
    )

    # Get next sequence number for this project/period
    prefix = f"atx_{period}_{project_code}_"
    last_record = ATXRecord.objects.filter(
        atx_id__startswith=prefix
    ).order_by('-atx_id').first()

    if last_record:
        last_seq = int(last_record.atx_id.split('_')[-1])
        next_seq = last_seq + 1
    else:
        next_seq = 1

    return f"{prefix}{next_seq:04d}"
```

---

## 5. What Exists vs. What Needs to Be Built

### EXISTS in Atlas PostgreSQL:

| Model | Location | Purpose |
|-------|----------|---------|
| `UserProfile` | `users/models.py` | Auth user, email-based |
| `TeamMember` | `users/models.py` | Basecamp-synced team member, has `tech_stack` |
| `UserAvailability` | `users/models.py` | Per-day availability/leave |
| `CompanyProject` | `projects/models.py` | Project with type, status, dates |
| `ProjectTeamMember` | `projects/models.py` | Binary membership (no allocation fields) |
| `Budgets` | `projects/models.py` | Project-level hours allocated/used |
| `Todo/TodoList/TodoSet` | `projects/models.py` | Task hierarchy, Basecamp-synced |
| `ATXTodoMapping` | `integrations/github/models.py` | **Minimal** - links TodoList → GithubRepository with ATX ID |
| `GithubCommit/GithubPR` | `integrations/github/models.py` | GitHub artifacts (commits, PRs) |

### NEEDS TO BE BUILT:

| Model | Purpose | Priority | Notes |
|-------|---------|----------|-------|
| `ATXRecord` | Reconciliation backbone - links tasks, artifacts, timesheets | **P0** | Extends/supersedes `ATXTodoMapping` |
| `ATXArtifact` | Artifacts (PRs, Figma, docs) linked to ATX records | **P0** | Leverages existing `GithubPR`, `GithubCommit` |
| `ProjectAllocation` | Resource allocation with role, effort, period | **P0** | New model |
| `TimeEntry` | Actual hours synced from Basecamp | **P1** | Links to ATXRecord |
| `Document` | CoS tasks/ideas/notes (migration target) | **P1** | New model for CoS migration |
| `DocumentComment` | Comments on documents | **P1** | New model |
| `Assignment` | Task delegation with approval workflow | **P2** | New model |

### DECISION: ProjectAllocation vs. Extend ProjectTeamMember

**Recommendation: New `ProjectAllocation` model** (not extending `ProjectTeamMember`)

Rationale:
- `ProjectTeamMember` is binary membership (you're on the project or not)
- `ProjectAllocation` is time-bounded allocation (Q1 2025: 2 days/week)
- A person can have multiple allocations to the same project over time
- Keeps Basecamp sync logic separate from allocation logic

---

## 6. CoS Migration Plan

### 6.1 Model Mapping: CoS (Couchbase) → Atlas (PostgreSQL)

| CoS Entity | CoS Location | Atlas Target | Notes |
|------------|--------------|--------------|-------|
| Document (task/idea/note) | `user_{email}/documents` | `Document` model | New model needed |
| Project | `rbac` collection | `CompanyProject` | Merge or map |
| ProjectMember | `rbac` collection | `ProjectTeamMember` + `ProjectAllocation` | Split membership from allocation |
| Assignment | `rbac` collection | `Assignment` model | New model needed |
| User | `users` bucket | `UserProfile` | Already exists, verify fields |
| Comment | Embedded in doc | `DocumentComment` | Extract to separate model |

### 6.2 New Django Models for CoS Data

```python
class Document(TimeStampedModel, SoftDeleteModel):
    """
    CoS task/idea/note/context - migrated from Couchbase.
    """
    DOC_TYPE_CHOICES = (
        ('task', 'Task'),
        ('idea', 'Idea'),
        ('note', 'Note'),
        ('context', 'Context'),
    )
    STATUS_CHOICES = (
        ('inbox', 'Inbox'),
        ('todo', 'Todo'),
        ('in-progress', 'In Progress'),
        ('blocked', 'Blocked'),
        ('done', 'Done'),
        ('archived', 'Archived'),
    )
    PRIORITY_CHOICES = (
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    )

    # Identity
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    owner = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.CASCADE,
        related_name='documents'
    )

    # Content
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    content = models.TextField()

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='inbox')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')

    # Organization
    project = models.ForeignKey(
        'CompanyProject',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents'
    )
    tags = ArrayField(
        models.CharField(max_length=50),
        blank=True,
        default=list
    )

    # Dates
    due_date = models.DateField(null=True, blank=True)

    # Metadata (for flexibility during migration)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']


class DocumentComment(TimeStampedModel):
    """
    Comments on documents (extracted from embedded comments in CoS).
    """
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    author = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.CASCADE
    )
    content = models.TextField()

    class Meta:
        ordering = ['created_at']


class Assignment(TimeStampedModel):
    """
    Task delegation with approval workflow (from CoS RBAC).
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('in_progress', 'In Progress'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('escalated', 'Escalated'),
        ('cancelled', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='assignments'
    )

    # Parties
    assignee = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.CASCADE,
        related_name='assignments_received'
    )
    assigned_by = models.ForeignKey(
        'users.UserProfile',
        on_delete=models.CASCADE,
        related_name='assignments_given'
    )

    # Workflow
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    instructions = models.TextField(blank=True, max_length=2000)
    due_date = models.DateField(null=True, blank=True)
    approval_required = models.BooleanField(default=True)

    # History (denormalized for query efficiency)
    history = models.JSONField(default=list)

    class Meta:
        ordering = ['-created_at']
```

### 6.3 API Gaps to Bridge

CoS currently exposes these endpoints that Atlas needs to replicate:

| CoS Endpoint | Method | Atlas Equivalent | Status |
|--------------|--------|------------------|--------|
| `/api/cos/docs` | GET | List documents with filters | **BUILD** |
| `/api/cos/docs` | POST | Create document | **BUILD** |
| `/api/cos/docs/{id}` | GET | Get document by ID (partial ID support) | **BUILD** |
| `/api/cos/docs/{id}` | PATCH | Update document | **BUILD** |
| `/api/cos/docs/{id}` | DELETE | Soft delete (archive) | **BUILD** |
| `/api/cos/docs/{id}/comments` | POST | Add comment | **BUILD** |
| `/api/cos/stats` | GET | Document statistics | **BUILD** |
| `/api/cos/projects` | GET/POST | Project CRUD | EXISTS (extend) |
| `/api/cos/assignments` | GET/POST | Assignment CRUD | **BUILD** |
| `/api/cos/assignments/{id}/action` | POST | Approve/reject/etc | **BUILD** |

**Key CoS behaviors to preserve:**
- Partial ID resolution (first N chars of UUID)
- Tag prefix matching (filter by "cos" matches "cos-api", "cos-server")
- Soft delete by default, hard delete optional
- Owner-scoped queries (user sees only their documents)

### 6.4 Migration Script Requirements

```python
# migration/cos_to_atlas.py

def migrate_documents(couchbase_client, postgres_db):
    """
    Migrate all documents from Couchbase user scopes to PostgreSQL.
    """
    # 1. Iterate all user scopes in Couchbase
    # 2. For each document:
    #    - Map doc_type, status, priority, tags
    #    - Resolve project reference (by name or create)
    #    - Extract embedded comments to DocumentComment
    #    - Preserve original UUID as primary key
    #    - Store unmapped fields in metadata JSON
    pass

def migrate_assignments(couchbase_client, postgres_db):
    """
    Migrate assignments from rbac collection.
    """
    # 1. Query all assignment:: docs from rbac
    # 2. Map to Assignment model
    # 3. Preserve history JSON
    pass

def verify_migration(couchbase_client, postgres_db):
    """
    Verify counts and spot-check records.
    """
    pass
```

---

## 7. Development Requirements

### 7.1 Test-Driven Development (Mandatory)

**100% coverage required for:**
- All new models (`ProjectAllocation`, `TimeEntry`, `Document`, `DocumentComment`, `Assignment`)
- All model methods and properties
- All database operations (CRUD, filtering, aggregation)
- All API endpoints

**Test Structure:**
```
tests/
├── test_models/
│   ├── test_project_allocation.py
│   ├── test_time_entry.py
│   ├── test_document.py
│   ├── test_document_comment.py
│   └── test_assignment.py
├── test_api/
│   ├── test_allocation_api.py
│   ├── test_document_api.py
│   └── test_assignment_api.py
├── test_queries/
│   ├── test_capacity_queries.py
│   └── test_document_filters.py
└── test_migration/
    └── test_cos_migration.py
```

### 7.2 Model Test Requirements

Each model test file must cover:

```python
class TestProjectAllocation:
    """100% coverage for ProjectAllocation model."""

    # Creation
    def test_create_allocation_with_required_fields(self): ...
    def test_create_allocation_with_all_fields(self): ...
    def test_unique_together_constraint(self): ...

    # Validation
    def test_effort_days_range_validation(self): ...
    def test_period_end_after_start_validation(self): ...
    def test_status_choices_validation(self): ...

    # Queries
    def test_filter_by_project(self): ...
    def test_filter_by_team_member(self): ...
    def test_filter_by_status(self): ...
    def test_filter_by_period_overlap(self): ...

    # Aggregations
    def test_total_effort_by_person(self): ...
    def test_total_effort_by_project(self): ...
    def test_available_capacity_query(self): ...

    # Soft delete
    def test_soft_delete(self): ...
    def test_exclude_deleted_by_default(self): ...
```

### 7.3 API Test Requirements

Each API test file must cover:

```python
class TestDocumentAPI:
    """100% coverage for Document API endpoints."""

    # List
    def test_list_documents_owner_scoped(self): ...
    def test_list_filter_by_status(self): ...
    def test_list_filter_by_priority(self): ...
    def test_list_filter_by_tags_prefix_match(self): ...
    def test_list_filter_by_project(self): ...
    def test_list_pagination(self): ...
    def test_list_sorting(self): ...

    # Create
    def test_create_task(self): ...
    def test_create_idea(self): ...
    def test_create_with_project(self): ...
    def test_create_with_tags(self): ...

    # Get
    def test_get_by_full_id(self): ...
    def test_get_by_partial_id(self): ...
    def test_get_partial_id_ambiguous_error(self): ...
    def test_get_not_found(self): ...
    def test_get_other_user_forbidden(self): ...

    # Update
    def test_update_status(self): ...
    def test_update_priority(self): ...
    def test_update_content(self): ...
    def test_update_tags(self): ...

    # Delete
    def test_soft_delete_default(self): ...
    def test_hard_delete_with_flag(self): ...

    # Comments
    def test_add_comment(self): ...
    def test_list_comments(self): ...
```

### 7.4 Capacity Query Tests

```python
class TestCapacityQueries:
    """Tests for resource allocation queries."""

    def test_person_total_allocation(self):
        """Sum of effort_days_per_week across all active allocations."""
        ...

    def test_person_available_capacity(self):
        """Default hours minus allocated hours."""
        ...

    def test_project_team_with_allocations(self):
        """List all team members with their roles and effort."""
        ...

    def test_overallocated_persons(self):
        """Find persons where sum(effort) > available capacity."""
        ...

    def test_persons_with_skill_and_capacity(self):
        """Find persons with skill X and Y available days."""
        ...

    def test_allocation_overlap_detection(self):
        """Detect overlapping allocations for same person/project."""
        ...
```

---

## 8. Implementation Order

### Phase 1: ATX Tracking Core (Priority: Highest)
1. `ATXRecord` model + tests
2. `ATXArtifact` model + tests
3. ATX ID generation utility + tests
4. ATX API endpoints + tests (create, link artifacts, update status)

### Phase 2: Resource Allocation
1. `ProjectAllocation` model + tests
2. `TimeEntry` model + tests (with ATX linkage)
3. Allocation API endpoints + tests
4. Capacity queries + tests

### Phase 3: CoS Document Migration
1. `Document` model + tests
2. `DocumentComment` model + tests
3. Document API endpoints + tests (with partial ID, tag prefix matching)

### Phase 4: Assignment & Workflow
1. `Assignment` model + tests
2. Assignment API endpoints + tests
3. Approval workflow actions + tests

### Phase 5: Migration & Integration
1. CoS → Atlas migration scripts
2. ATX ↔ Basecamp sync integration
3. Migration verification tests
4. Data validation and cleanup

---

## 9. Files to Create/Modify

### New Files:
```
atlas-pmp-backend/
├── allocations/
│   ├── __init__.py
│   ├── models.py          # ProjectAllocation, TimeEntry
│   ├── serializers.py
│   ├── views.py
│   ├── urls.py
│   └── admin.py
├── atx/
│   ├── __init__.py
│   ├── models.py          # ATXRecord, ATXArtifact (or extend github/models.py)
│   ├── serializers.py
│   ├── views.py
│   ├── urls.py
│   ├── utils.py           # ATX ID generation
│   └── admin.py
├── documents/
│   ├── __init__.py
│   ├── models.py          # Document, DocumentComment, Assignment
│   ├── serializers.py
│   ├── views.py
│   ├── urls.py
│   └── admin.py
├── tests/
│   ├── test_models/
│   │   ├── test_atx_record.py
│   │   ├── test_atx_artifact.py
│   │   ├── test_project_allocation.py
│   │   ├── test_time_entry.py
│   │   ├── test_document.py
│   │   └── test_assignment.py
│   ├── test_api/
│   │   ├── test_atx_api.py
│   │   ├── test_allocation_api.py
│   │   ├── test_document_api.py
│   │   └── test_assignment_api.py
│   ├── test_queries/
│   │   ├── test_capacity_queries.py
│   │   └── test_atx_reconciliation.py
│   └── test_migration/
│       └── test_cos_migration.py
└── migration/
    ├── cos_to_atlas.py
    └── atx_mapping_migration.py  # Migrate ATXTodoMapping → ATXRecord
```

### Modified Files:
```
users/models.py                    # Add default_hours_per_week to TeamMember (if needed)
projects/models.py                 # No changes (keep ProjectTeamMember separate)
integrations/github/models.py      # Deprecate ATXTodoMapping, add FK to ATXRecord on GithubPR/Commit
integrations/github/serializers.py # Update to include ATX linkage
core/urls.py                       # Add allocations/, atx/, documents/ routes
```

