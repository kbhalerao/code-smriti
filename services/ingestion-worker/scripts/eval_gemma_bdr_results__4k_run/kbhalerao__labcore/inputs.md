# Inputs for kbhalerao/labcore

- commit_hash: 6c09d47821833a2dd72bcbfce3086336c67c0ab1
- prompt_chars: 15069

## Repo summary

This Django-based web application, **labcore**, is a comprehensive platform for managing agricultural and laboratory workflows, including user and organization associations, field data tracking, soil testing, and report generation. It leverages **Django** as the core framework, with **HTML templates**, **Django admin interfaces**, and **management commands** for automation and data integrity. Key modules include user/organization management (e.g., `associates`, `organizations`), data collection and reporting (e.g., `clients`, `products`, `workflows`), and administrative tools (e.g., `contact`, `deprecated` modules for legacy systems). The modules work together through templated UIs (HTML), Django models, and background management commands—such as syncing subscriptions or validating data—to support end-to-end processes like user onboarding, field data tracking, report generation, and system maintenance, with a strong emphasis on email communication and administrative control.

## README (first 5000 chars)

(No README available)

## Module summaries

### (root)/
The `soildx-labcore` package is a Django-based core module for soil diagnostics, managing lab workflows, data migration, and geospatial processing. Its key components include Django/DRF for web services, utilities for copying complex model hierarchies across databases, and GDAL-integrated scripts for raster operations (e.g., merging, polygonizing, sieving). Other code uses it to synchronize lab data between environments, migrate models with integrity, and process geospatial rasters in batch workflows.

### (root)/
This module is a geospatial and data management toolkit built around Django, designed for processing raster data and managing complex object hierarchies in soil diagnostics applications. Key components include GDAL-based utilities for raster operations (e.g., `gdal_merge.py`, `gdal_sieve.py`, `gdal_polygonize.py`) and Django model copying tools for cross-database data migration. Other code uses it to automate geospatial workflows—such as merging satellite imagery or cleaning classification maps—and to duplicate structured data (e.g., lab samples and measurements) across environments while preserving relationships and integrity.

### (root)/
The `soildx-labcore` package is a Django-based core module for soil diagnostics, managing lab workflows, data migration, and geospatial processing. Key components include Django/DRF for web services, utilities for copying model instances across databases with relationship integrity, and GDAL-integrated scripts for raster operations (e.g., merge, sieve, polygonize). Other code uses it to synchronize lab data between environments, process classification maps, and automate geospatial workflows via command-line tools.

### (root)/
The `soildx-labcore` package is a Django-based core module for soil diagnostics, managing lab workflows, data integrity, and geospatial processing. Its key components include Django models with advanced copy utilities for cross-database migration, geospatial tools like `gdal_merge.py`, `gdal_polygonize.py`, and `gdal_sieve.py` for raster and vector processing, and a `setup.py` that enables installation and distribution. Other code uses this module to synchronize lab data across environments, process classification maps, and integrate geospatial workflows via GDAL and Django's ORM.

### (root)/
The `soildx-labcore` package is a Django-based core module for soil diagnostics, managing lab workflows, data integrity, and geospatial processing. It includes key components like Django models with advanced copy utilities for cross-database migration, GDAL-integrated scripts for raster processing (e.g., `gdal_merge.py`, `gdal_sieve.py`, `gdal_polygonize.py`), and a `manage.py` interface for administrative tasks. Other code uses this module to synchronize lab data across environments, process classified raster outputs, and automate geospatial workflows through standardized, reusable utilities.

### (root)/
The `soildx-labcore` module is a Django-based package for core soil management workflows, integrating geospatial data processing and database management. Key components include Django/DRF for web services, custom utilities for cross-database model copying (preserving relationships), and GDAL-powered CLI tools like `gdal_sieve.py`, `gdal_polygonize.py`, and `gdal_merge.py` for raster processing. Other code uses this module to manage lab data (e.g., samples, orders) across environments, synchronize databases with integrity, and automate geospatial preprocessing for classification and mapping tasks.

### (root)/
This module, `labcore`, is a Django-based package for managing soil diagnostic data workflows, integrating geospatial processing and database operations. It includes core components like GDAL-powered raster tools (`gdal_merge.py`, `gdal_polygonize.py`, `gdal_sieve.py`) for processing remote sensing and classification data, and utilities for copying Django models across databases to support data migration and synchronization. Other code uses it via `manage.py` for Django administration, `setup.py` for installation and distribution, and its utilities to automate data workflows—such as merging satellite rasters, converting them to vector polygons, or duplicating lab data with full relational integrity.

### (root)/
This module, `labcore`, is a Django-based package for managing soil diagnostic data workflows, enabling data migration, raster processing, and database synchronization. Key components include utility functions for copying Django models across databases with relationship integrity, GDAL-based scripts for raster merging, polygonization, and sieve filtering, and a `setup.py` that defines dependencies for distribution. Other code uses it to duplicate lab data (e.g., Orders, Samples) across environments, process geospatial rasters in batch, and integrate with Django’s ORM and GDAL for robust data management.

### agwx/
Module: agwx/

Files (14):
- views.py: This file defines two Django REST framework views for managi
- urls.py: This file defines URL patterns for the AGWX (Agricultural We
- models.py: This file defines the `AgWxSubscription` model and its manag
- serializers.py: This file defines a serializer for the `AgWxSubscription` mo
- admin.py: This Django admin file configures the administration interfa
- admin.py: This Django admin file registers the `AgWxSubscription` mode
- apps.py: This file defines the configuration for the `agwx` Django ap
- tests.py: This file contains unit tests for the `AgWxSubscription` mod
- models.py: This file defines the `AgWxSubscription` model and its manag
- apps.py: This file defines the configuration for the `agwx` Django ap

Submodules (1):
- agwx/migrations/

### agwx/
Module: agwx/

Files (14):
- views.py: This file defines two Django REST framework views for managi
- urls.py: This file defines URL patterns for the AGWX (Agricultural We
- models.py: This file defines the `AgWxSubscription` model and its manag
- serializers.py: This file defines a serializer for the `AgWxSubscription` mo
- admin.py: This Django admin file configures the administration interfa
- admin.py: This Django admin file registers the `AgWxSubscription` mode
- apps.py: This file defines the configuration for the `agwx` Django ap
- tests.py: This file contains unit tests for the `AgWxSubscription` mod
- models.py: This file defines the `AgWxSubscription` model and its manag
- apps.py: This file defines the configuration for the `agwx` Django ap

Submodules (1):
- agwx/migrations/

### agwx/
Module: agwx/

Files (7):
- views.py: This file defines two Django REST framework views for managi
- admin.py: This Django admin file registers the `AgWxSubscription` mode
- apps.py: This file defines the configuration for the `agwx` Django ap
- models.py: This file defines the `AgWxSubscription` model and its manag
- tests.py: This file contains unit tests for the `agwx` app, primarily 
- urls.py: This Django URL configuration file defines API endpoints for
- serializers.py: This file defines a serializer for the `AgWxSubscription` mo

Submodules (1):
- agwx/migrations/

### agwx/
Module: agwx/

Files (14):
- views.py: This file defines two Django REST framework views for managi
- urls.py: This file defines URL patterns for the AGWX (Agricultural We
- models.py: This file defines the `AgWxSubscription` model and its manag
- serializers.py: This file defines a serializer for the `AgWxSubscription` mo
- admin.py: This Django admin file configures the administration interfa
- admin.py: This Django admin file registers the `AgWxSubscription` mode
- apps.py: This file defines the configuration for the `agwx` Django ap
- tests.py: This file contains unit tests for the `AgWxSubscription` mod
- models.py: This file defines the `AgWxSubscription` model and its manag
- apps.py: This file defines the configuration for the `agwx` Django ap

Submodules (1):
- agwx/migrations/

### agwx/
Module: agwx/

Files (14):
- views.py: This file defines two Django REST framework views for managi
- urls.py: This file defines URL patterns for the AGWX (Agricultural We
- models.py: This file defines the `AgWxSubscription` model and its manag
- serializers.py: This file defines a serializer for the `AgWxSubscription` mo
- admin.py: This Django admin file configures the administration interfa
- admin.py: This Django admin file registers the `AgWxSubscription` mode
- apps.py: This file defines the configuration for the `agwx` Django ap
- tests.py: This file contains unit tests for the `AgWxSubscription` mod
- models.py: This file defines the `AgWxSubscription` model and its manag
- apps.py: This file defines the configuration for the `agwx` Django ap

Submodules (1):
- agwx/migrations/

### agwx/
The `agwx` module manages automated subscriptions to the AGWX agricultural weather data service, enabling farms to access external weather data through a scalable, lifecycle-managed integration. Key components include the `AgWxSubscription` model and its serializer, which handle subscription creation, renewal, and synchronization with AGWX via API calls, along with Django admin, views, URLs, and tests for full CRUD functionality and access control. Other parts of the codebase use this module to securely retrieve weather subscription status, enforce dealership-level data access, and automate farm onboarding and subscription renewals based on expiration dates and farm coordinates.

### agwx/
The `agwx` module manages third-party weather data subscriptions for farms, replacing the legacy PrismWeather system by integrating with the AGWX external service. Its core components include the `AgWxSubscription` model (with Django ORM and ownership controls), a serializer for creating and validating subscriptions, Django REST Framework views with dealership-level access control, and admin/UI support for monitoring subscriptions. Other parts of the codebase use it to securely create, retrieve, and renew weather subscriptions tied to farms, ensuring data privacy and seamless integration with external weather APIs.

### agwx/
Module: agwx/

Files (14):
- views.py: This file defines two Django REST framework views for managi
- urls.py: This file defines URL patterns for the AGWX (Agricultural We
- models.py: This file defines the `AgWxSubscription` model and its manag
- serializers.py: This file defines a serializer for the `AgWxSubscription` mo
- admin.py: This Django admin file configures the administration interfa
- admin.py: This Django admin file registers the `AgWxSubscription` mode
- apps.py: This file defines the configuration for the `agwx` Django ap
- tests.py: This file contains unit tests for the `AgWxSubscription` mod
- models.py: This file defines the `AgWxSubscription` model and its manag
- apps.py: This file defines the configuration for the `agwx` Django ap

Submodules (1):
- agwx/migrations/

### agwx/migrations/
This module defines Django migrations that establish and modify the `AgWxSubscription` model to manage agricultural weather subscriptions. It enforces a one-to-one relationship between subscriptions and farms, ensuring each subscription is uniquely tied to a single farm with cascade deletion. Other parts of the codebase use this model to track subscription validity, control access to weather data, and manage renewals based on expiration dates. The migrations are applied during database setup or updates to maintain data integrity between the AGWX service and client farm records.

### agwx/migrations/
Module: agwx/migrations/

Files (4):
- 0001_initial.py: This Django migration file creates the `AgWxSubscription` mo
- 0002_alter_agwxsubscription_farm.py: This Django migration file modifies the `agwxsubscription` m
- 0001_initial.py: This Django migration file creates the `AgWxSubscription` mo
- 0002_alter_agwxsubscription_farm.py: This Django migration file modifies the `agwxsubscription` m

### agwx/migrations/
Module: agwx/migrations/

Files (4):
- 0001_initial.py: This Django migration file creates the `AgWxSubscription` mo
- 0002_alter_agwxsubscription_farm.py: This Django migration file modifies the `agwxsubscription` m
- 0001_initial.py: This Django migration file creates the `AgWxSubscription` mo
- 0002_alter_agwxsubscription_farm.py: This Django migration file modifies the `agwxsubscription` m

### agwx/migrations/
Module: agwx/migrations/

Files (2):
- 0001_initial.py: This Django migration file creates the `AgWxSubscription` mo
- 0002_alter_agwxsubscription_farm.py: This Django migration file modifies the `agwxsubscription` m
