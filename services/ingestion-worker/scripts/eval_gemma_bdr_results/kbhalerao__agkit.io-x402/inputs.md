# Inputs for kbhalerao/agkit.io-x402

- commit_hash: 0ce4d8b75ba8e4872ac06413bf8777b2e1148578
- prompt_chars: 16159

## Repo summary

The **x402 Control Plane** (agkit.io-x402) is a multi-tenant, billing-aware API platform that enables secure, monetized access to API services through a model control plane (MCP) architecture. Built on **Django**, **Django REST Framework (DRF)**, and **FastAPI** (via FastMCP), it leverages PostgreSQL for multi-tenant data isolation and integrates with Stripe for payment processing. Key modules include `apps/common` for tenant-aware authorization, `apps/adapter` for secure, gated API tools, `apps/catalog` for versioned product catalogs, `apps/billing` for credit accounts and Stripe integration, and `apps/ledger` for auditable financial transaction tracking via Write-Ahead Logs (WAL). These modules work together through a unified control plane: the `adapter` service exposes authenticated, tenant-scoped endpoints via the `mcp.tool()` decorator, uses `catalog` data to define pricing and access, enforces payments via `billing`, and logs all transactions in `ledger`, while management commands automate operations like WAL archiving and MCP server launches.

## README (first 5000 chars)

(No README available)

## Module summaries

### (root)/
This module is the root of a Django-based web application, serving as the central hub for project configuration and management. It includes `manage.py`, which provides command-line tools for running the development server, creating migrations, and managing the database, and likely contains Django settings and project-level configuration. Other parts of the codebase interact with it through Django’s built-in management commands and settings, leveraging `manage.py` to execute tasks like database migrations or starting the server. The module enables local development, deployment, and administrative operations essential for the application’s lifecycle.

### (root)/
This module serves as the core Django application for the x402 control plane's static landing pages, providing a structured, testable frontend for user onboarding and vendor documentation. Its key components include `TemplateView`-based pages, comprehensive tests in `tests_landing.py` that validate HTML structure and content, and a `manage.py` entry point for development and deployment tasks. Other code uses it to ensure consistent, correct rendering of static content through automated tests and Django’s management commands, enabling reliable deployment and easy maintenance.

### (root)/
This module serves as the entry point for Django's management commands, enabling administrative tasks like migrations, server startup, and user creation. Its key component is the `main` function, which sets the Django settings module and delegates command execution to Django’s built-in management system. Other code uses this module via the `python manage.py` command, allowing developers to interact with the application’s configuration, database, and services during development and deployment.

### apps/
The `apps/` directory defines the core business logic and infrastructure for a multi-tenant, platform-as-a-service (PaaS) system, enabling secure, scalable management of users, organizations, financial operations, and product catalogs. Key components include tenant-aware models and permissions, a secure API layer via the adapter module, financial tracking via the ledger and billing systems, and catalog-driven configuration for platform services. Other parts of the codebase use these modules to enforce tenant isolation, authenticate and authorize requests, manage payments and usage, and securely expose platform capabilities through standardized APIs and tools.

### apps/
The `apps/` module implements a secure, multi-tenant platform for managing API-driven products, financial transactions, and user access within the x402 ecosystem. Key components include tenant-scoped models (e.g., `Organization`, `Wallet`, `WALEntry`), role-based access controls, Redis-backed catalog caching, and REST APIs for CRUD operations, payments, and data isolation. Other parts of the codebase use these components via Django views, DRF viewsets, management commands, and decorators to enforce tenant boundaries, validate access, and securely expose data and operations across organizations.

### apps/
The `apps/` module collectively implements a secure, multi-tenant platform for managing API-driven services, billing, cataloging, and financial tracking across distributed tenants. Key components include tenant-scoped models, role-based access control, Stripe and WAL-based billing, cached catalog APIs, and secure tooling via decorators. Other parts of the codebase use these apps to authenticate users, enforce tenant isolation, process payments, expose versioned APIs, and trigger workflows like provisioning or settlement.

### apps/adapter/
The `apps/adapter/` module implements a secure, multi-tenant Model Control Plane (MCP) adapter for the x402 platform, enabling controlled access to resources like product groups, wallets, and transactions via authenticated API tools. Its key components include a Django app configuration, a payment gate enforcing access via bypass tokens or credit, a suite of role-based tools for data management, and a management command to launch the server over various transports (stdio, SSE, HTTP). Other parts of the codebase use the `@mcp.tool()` decorator to invoke these tools securely, leveraging shared auth, tenant isolation, and standardized responses to expose a consistent, monetized API layer.

### apps/adapter/
The `apps/adapter/` module serves as the core backend for the x402 Control Plane, providing a secure, tool-driven API layer for managing platform resources like groups, items, wallets, and transactions. Its key components include a FastMCP server with transport flexibility, role-based tools with authentication and tenant isolation, an x402 payment gate for monetized access, and comprehensive tests ensuring correctness and security. Other code uses it via the `mcp.tool()` decorator to expose validated endpoints, leveraging built-in auth, tenant resolution, and payment gating for secure, scalable interactions with the control plane.

### apps/adapter/
The `apps/adapter/` module implements a secure, multi-tenant Model Control Plane (MCP) adapter for the x402 platform, enabling programmatic management of resources like catalogs, wallets, and transactions with built-in payment gating and role-based access control. Key components include a FastMCP server launcher, Django app configuration, payment gate enforcement, and a toolkit of secure, auditable CRUD operations for platform data. Other parts of the codebase use this module via management commands (`run_mcp`), Django views, and tool decorators to expose API endpoints, validate access, and manage tenant-isolated data flows in a monetized, enterprise environment.

### apps/adapter/management/
This module provides a Django management command to launch an MCP (Model Control Plane) server with configurable transport protocols—stdio, SSE, or streamable-http—enabling flexible communication backends. Its core is a `Command` class that parses CLI arguments for transport type, host, and port, then delegates server startup to `apps.adapter.server.mcp.run`. Other parts of the codebase invoke this command to deploy or run the MCP server locally, adapting to different environments via protocol selection. It serves as the primary entry point for initiating the MCP server in various deployment scenarios.

### apps/adapter/management/
This module provides a Django management command to launch an MCP (Model Control Plane) server with configurable transport protocols (stdio, SSE, or streamable HTTP). Its key components include a `Command` class that defines CLI arguments for transport type, host, and port, and logic to invoke `mcp.run` with the specified settings. Other code uses this command to start the MCP server in different modes—local for development or networked for deployment—enabling flexible, protocol-agnostic communication with clients during testing, debugging, or production.

### apps/adapter/management/
This module provides a Django management command to launch an MCP (Model Control Plane) server with configurable transport protocols—stdio, SSE, or streamable-http—enabling flexible deployment across environments. Its core component is a `Command` class that parses CLI arguments and invokes `mcp.run` with the appropriate transport settings. Other parts of the codebase use this command via `python manage.py run_mcp` to start the MCP server consistently in development, testing, and production. It serves as a standardized, extensible entry point for MCP server operations.

### apps/adapter/management/commands/
This module provides a Django management command to launch an MCP (Model Control Plane) server with configurable transport protocols (stdio, SSE, or streamable-http). Its key component is a `Command` class that parses CLI arguments for transport type, host, and port, then invokes the server runner in `apps.adapter.server.mcp.run`. Other parts of the codebase use this command to deploy or run the MCP server locally, enabling flexible integration with different communication backends depending on deployment context.

### apps/adapter/management/commands/
This module provides a Django management command to launch an MCP (Model Control Plane) server with configurable transport protocols. Its key components include a `Command` class defining CLI arguments for transport type (stdio, SSE, or streamable HTTP), host, and port, and logic to invoke the `mcp.run` function accordingly. Other code uses this command to start the MCP server in different modes—local for development, or networked for deployment—enabling flexible client communication during testing, debugging, or production use.

### apps/adapter/management/commands/
This Django management command module launches an MCP (Model Control Plane) server with configurable transport protocols—stdio, SSE, or streamable-http—enabling flexible deployment across different environments. Its core component is a `Command` class that parses CLI arguments for transport type, host, and port, then invokes `mcp.run` with appropriate settings to initialize the server. Other parts of the codebase use this command to start the MCP server via `python manage.py run_mcp`, providing a standardized entry point for development, testing, and production setups.

### apps/adapter/tools/
This module provides a secure, role-based API layer for managing organizational configuration, financial data, and product catalogs within the adapter service. Key components include admin-protected tools for retrieving config and wallet data, CRUD operations for product groups and items, and gatekeeping via the `x402_gated` decorator for monetized access. Other code uses it through the `mcp.tool()` decorator to expose validated, authenticated endpoints, leveraging shared utilities for auth, tenant resolution, and data serialization. The module acts as a centralized, secure interface for internal and external systems to interact with sensitive operational and financial data.

### apps/adapter/tools/
This module provides a secure, role-based toolkit for managing organizational configuration, catalog data, wallets, and ledgers within a multi-tenant system. It includes core tools for retrieving and manipulating data—such as `get_config_version`, `list_product_groups`, `create_wallet`, and `get_dashboard`—all enforced by shared authentication, authorization, and serialization utilities. These tools are registered via `tools/__init__.py` and used by the MCP server to expose API endpoints that validate user roles, isolate tenant data, and return standardized, JSON-ready responses. Other parts of the codebase invoke these tools through the `@mcp.tool()` decorator to safely perform operations like fetching config, managing products, or listing transactions.

### apps/adapter/tools/
This module provides a secure, multi-tenant API toolkit for managing configuration, catalog data, wallets, and financial ledgers within the adapter service. Its key components include role-based access control, integration with Django models, and standardized data serialization via shared helpers like `model_to_dict` and `require_admin`. Other parts of the codebase use these tools—through decorators and direct imports—to safely expose CRUD operations for catalogs, configurations, wallets, and transactions, enabling dynamic, permission-protected data flows in a monetized, enterprise-grade system.

### apps/billing/
The `apps/billing/` module manages a comprehensive billing system for a Django application, handling prepaid credit accounts, Stripe integrations, and service-to-service authentication. Key components include the `CreditAccount` model for tracking balances and replenishment rules, Stripe wrapper functions for payment operations, serializers for validating financial data, and admin interfaces for oversight. Other parts of the codebase use this module to securely process payments, deduct credits atomically, enforce business rules, and authenticate internal service calls—ensuring financial operations are consistent, auditable, and decoupled from core business logic.

### apps/billing/
The `apps/billing/` module implements a secure, automated billing system for API usage, managing prepaid credit accounts, Stripe integrations, and service-to-service authentication. Key components include the `CreditAccount` model for tracking balances, Stripe wrapper functions for payments and setup, serializers for data validation, and authentication/permission classes for internal API security. Other parts of the codebase use this module to process charges, fund accounts, enforce payment rules, and securely authorize service calls—ensuring reliable, auditable billing operations across the platform.
