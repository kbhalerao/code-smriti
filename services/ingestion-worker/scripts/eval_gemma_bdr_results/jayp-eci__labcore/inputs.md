# Inputs for jayp-eci/labcore

- commit_hash: 7a79e1817ec6d0482b6ab997c578f25b8ba88cf0
- prompt_chars: 16063

## Repo summary

The **labcore** repository is a Django-based web application designed for agricultural and environmental data management, enabling users to onboard organizations, manage field and sensor data, generate variability and product reports, and automate workflows and communications. It leverages **Django**, **Leaflet/Google Maps**, **Mermaid.js**, **DataTables**, and **custom template filters** to provide dynamic, interactive interfaces for field mapping, sensor monitoring, report generation, and administrative oversight. Key modules include field and weather data integration, sensor device management, dynamic reporting with statistical summaries, email templating for onboarding, and reusable document generation for orders and work. These modules work together through Django’s MVC architecture—views pass context to templates, signals trigger emails, management commands maintain data integrity, and frontend components use JavaScript and filters to render interactive, data-driven UIs, creating a cohesive platform for lab and field operations.

## README (first 5000 chars)

(No README available)

## Module summaries

### (root)/
This module, `soildx-labcore`, is a Django-based package for soil data management, providing tools for geospatial data processing and database synchronization. Key components include GDAL-integrated scripts for raster merging, polygonization, and filtering, as well as utilities for copying Django models and their relationships across databases. Other code uses it to process and integrate spatial data (via GDAL tools) and migrate or replicate structured data between databases while preserving referential integrity. It serves as a foundational toolkit for soil analysis workflows, combining geospatial processing with robust data management.

### articles/
The `articles` module in the `jayp-eci/labcore` repository implements a Django-based API for managing and retrieving blog articles, primarily integrating with the Zinnia blogging framework. Its key components include a serializer (`ZinniaEntrySerializer`) for shaping API output, a view (`ArticleList`) that returns published articles with optional category filtering, and test cases ensuring proper data retrieval and filtering. The module is used by the broader application to serve structured article data via REST endpoints, with authentication and admin integration supporting secure, manageable content delivery.

### associates/
The `associates` module manages user and organization relationships in a Django application, enabling role-based access control, user onboarding via invitations, and structured team management within multi-organization environments. Key components include models for organizations and users, DRF serializers and views for API-driven setup, Django admin integration for UI management, custom permissions, and forms for secure data entry. Other parts of the codebase use this module to authenticate and authorize users, create and manage organizational hierarchies, and enforce business rules during onboarding and role assignment.

### associates/management/
This module provides management commands to maintain data integrity in the associates application. Its key components include a command to synchronize organization names with linked company names and another to audit AOUsers and Clients for contact data issues like missing fields, duplicate addresses, or multiple phone numbers. These commands are invoked via `manage.py` and are used during deployment or debugging to ensure consistent, reliable data across organizational and user models. Other parts of the codebase rely on these tools to validate and correct data before critical operations.

### associates/management/commands/
This module provides management commands for data integrity maintenance within the associates application. It includes two key commands: one that synchronizes organization names with their linked company names, and another that audits AOUsers and Clients for contact data issues like missing info, duplicate addresses, or multiple phone numbers. These commands are invoked via `manage.py` and serve as diagnostic and corrective tools during deployment or debugging. Other parts of the codebase rely on these utilities to ensure consistent, reliable data across organizational and user models.

### associates/migrations/
This module manages the database schema and data migration for organization-related models in the `associates` app, enabling multi-organization support with access control, user roles, and customizable settings. Key components include the `Organization` model with preferences, access permissions, and metadata; the `OrganizationUser` and `OrganizationOwner` models for user and ownership relationships; and fields for label templates, JSON-based preferences, and access control. Other parts of the codebase use these models to enforce role-based access, store per-organization configurations, and manage user affiliations across organizations.

### associates/templates/
This module provides a suite of Django templates for managing organization-user relationships within the labcore application, supporting user registration, team management, and account consolidation. Key components include a base layout for organization pages, a user merge interface for administrators, a team dashboard for viewing and managing members, and forms for membership actions like deletion and profile updates. Other parts of the codebase use these templates via Django views that pass dynamic context such as user roles, organization data, and permissions to render role-specific interfaces. The templates integrate with admin logic and workflows to enable secure, user-driven management of team memberships and account operations.

### associates/templates/admin/
This Django admin template module enables administrators to merge multiple organization users into a single user account, consolidating associated clients and roles while deactivating redundant accounts. Its key components include a form with radio buttons to select the primary user, hidden fields to trigger the merge action, and logic to mark non-primary users as administrators. Other code, particularly the admin views and merge logic, uses this template to process user consolidation requests, ensuring data integrity during account merging operations.

### associates/templates/associates/
This module provides a dashboard interface for organization administrators to view, manage, and invite team members within the labcore application. It includes an HTML template that renders a list of users with their roles, statuses, and contact information, along with admin controls for toggling user status and sending invitations. The template relies on context variables and permissions to conditionally display actions, ensuring only authorized users can modify team membership. Other parts of the codebase invoke this template via views that pass user data and permissions, integrating it into the broader admin workflow.

### associates/templates/assocorganizations/
This module manages organization and user association functionality within the associates portal, handling registration, membership listing, profile editing, and administrative actions like user deletion and reminders. Key components include templates for organization details, user membership lists, registration and edit forms, and confirmation pages for actions like login, deletion, and profile updates. Other parts of the codebase use these templates via Django views that pass context such as `org`, `organizations`, `form`, and `user` to render dynamic, user-specific interfaces for dashboard navigation and administrative workflows.

### associates/templates/assocorganizations/email/
This module contains Django HTML email templates used to send invitation and notification emails when users are added to or invited to join an organization. Key components include dynamic placeholders for the organization name, sender’s name, domain, and a secure token-based registration link generated via Django’s `url` template tag. Other parts of the codebase, such as views or signals, invoke these templates to dispatch personalized emails during user onboarding or organization membership updates.

### associates/tests/
This module provides a comprehensive suite of automated tests for the `associates` app, ensuring robust functionality across admin interfaces, API endpoints, user management, and organization workflows. Key components include browser-based integration tests using Selenium, API tests with DRF’s test client, model-level tests for data integrity, and admin action validation. Other parts of the codebase rely on this module to verify behavior during development, deployments, and pull requests, maintaining reliability and security in user and organization management.

### blm/
The `blm/` module in the `jayp-eci/labcore` repository is designed to support a Black Lives Matter–related application within a Django project, primarily handling geospatial data and administrative functionality. Its key components include geospatial utilities for parsing PLSS/TRS land descriptions, a Django app configuration, and placeholders for models, admin registration, views, and tests. Other code in the project would use this module to manage spatial data via its PLSS/latlon conversion functions, register models in the admin interface, and build views and tests for BLM-specific features. The structure suggests it's in early development, awaiting implementation of core models and functionality.

### bugs/
The `bugs` module in the labcore application provides a full-featured bug tracking system for lab staff to report, assign, and resolve software issues. Its key components include Django models for bugs and comments, REST API endpoints, admin interfaces, forms, and views that support rich data handling, user context, and email notifications. Other parts of the codebase integrate with it via API calls, form submissions, and database queries to create, display, and manage bug reports with metadata, attachments, and workflow state.

### bugs/migrations/
This module manages the evolving database schema for a bug tracking system in the labcore application, ensuring data integrity, scalability, and user-friendly input through migrations. Key components include model updates for bug categorization, user attribution, image attachments, field constraints, and large ID support via BigAutoField. Other parts of the codebase interact with the updated models—such as views, forms, and APIs—to create, display, and manage bugs with rich metadata, relationships, and user context.

### bugs/templates/
This module handles the web-facing bug reporting functionality in the labcore application, allowing users to submit and view bug reports. Its key components include a Django form with crispy_forms for bug submission, a DataTables-powered table for displaying open bugs, and detailed templates that render bug data, status, and S3-hosted media. Other parts of the codebase interact with it via the `bug_list_json` API endpoint and Django views, using JavaScript (bugs.js) to dynamically load and display bug data in the UI.

### bugs/templates/bugs/
This module provides a web interface for managing bug reports within the labcore application, enabling users to submit new bugs and view detailed information about existing ones. It includes a form for bug submission using Django and crispy_forms, a dynamic table powered by DataTables to display open bugs, and a detailed view template that renders bug attributes, status indicators, and media via AWS S3. Other parts of the codebase interact with this module through the `bug_list_json` API endpoint and Django views that render these templates, using JavaScript (bugs.js) to fetch and display bug data dynamically.

### bugs/tests/
This module provides a comprehensive test suite for the bugs application's API and views, ensuring reliable handling of bug creation, updates, comments, and file uploads. Its key components include Django REST framework test cases and unit tests that validate authentication, form validation, data integrity, and integration with models and fixtures. Other parts of the codebase rely on this module to verify functionality before deployment, using it as a gatekeeper for regressions and ensuring consistent behavior across features.

### clients/
The `clients` module in the `jayp-eci/labcore` repository manages agricultural client data, including farms, fields, and associated geospatial information, supporting full lifecycle operations for farm management and data analytics. Key components include Django models and forms for data integrity, GIS-enabled utilities like `soilsmixin.py` and `api_helper.py` for spatial enrichment, admin tools for bulk operations, and REST-like API endpoints for integration with web, mobile, and legacy systems. Other parts of the codebase use this module to create and manage client records, enrich field data with soil and geographic metadata, generate reports via PDF exports, and stream real-time GIS updates through WebSockets.

### clients/apiviews/
This module provides a comprehensive API layer for client, farm, and field data, supporting web, mobile, and legacy frontend needs through optimized, hierarchical data retrieval. Key components include search-enabled views (`APISearchAllClientsAssets`), mobile-optimized spatial queries (`APIClientListMobile`), and a centralized viewset for client management with caching and permissions. Other parts of the codebase import from this module to serve structured, performant API responses—enabling consistent, scalable access to client data across different client types and workflows.
