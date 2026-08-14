# kbhalerao/agkit.io-backend / tier1apps/gislayers

Model: `general` (held constant)  
Inputs: 6 files + 3 submodules = 9 children  
Old context: 3203 chars (capped at 6000, first 15 inputs)  
New context: 6623 chars

## Baseline (stored in Couchbase today)

The `gislayers` module provides a specialized API layer for managing and retrieving geospatial data, specifically handling both vector and raster layers. It includes core components such as Django Rest Framework serializers with spatial transformation utilities, dynamic API endpoints for layer retrieval, and custom validation exceptions to ensure geometry integrity. Other parts of the system or external frontend applications use this module to dynamically fetch spatial data through structured URL patterns based on specific model instances.

## Old context → summary

_4.0s_

The `gislayers` module provides a foundational geospatial data management system for handling vector and raster layers, including specialized agricultural proxies like soil properties and field boundaries. It utilizes core models, serializers for GeoJSON/spatial transformations, and optimized API views to manage spatial datasets and metadata. Other parts of the application interact with this module by instantiating these models to ingest, store, and retrieve geographic data through secure, filtered API endpoints.

## New context → summary

_6.2s_

The `gislayers` module provides the geospatial data management foundation for the AgKit.io backend, handling the storage, serialization, and retrieval of vector and raster layers. Key components include core models like `GISVectorLayer` and `GISRasterLayer`, specialized proxy models such as `SoilProperties` and `FieldBoundaryDraft`, and API views including `VectorLayerList` and `RasterLayerList`. The module also features a suite of serializers, notably the `GeoJSONFeatureSerializer` and `FieldBoundaryDraftSerializer`, which manage the conversion between GeoJSON formats and database storage. A significant piece of domain logic is found in the `_best_utm_epsg` function and its associated calculations, which determine an optimal UTM projection based on a geometry's centroid to ensure accurate area measurements in acres. This approach avoids the distortion inherent in using Web Mercator (EPSG:3857) for field-scale acreage calculations.

## New context (verbatim prompt input)

```
### tier1apps/gislayers/admin.py (python, 12 lines)
File: tier1apps/gislayers/admin.py (python)
Imports: django.contrib, tier1apps.gislayers.models

### tier1apps/gislayers/apps.py (python, 7 lines)
File: tier1apps/gislayers/apps.py (python)
Classes: GislayersConfig
Imports: django.apps

### tier1apps/gislayers/exceptions.py (python, 13 lines)
File: tier1apps/gislayers/exceptions.py (python)
Classes: ContentTypeInvalidError, GeometryTypeInvalidError
Imports: django.core.exceptions

### tier1apps/gislayers/serializers.py (python, 180 lines)
This file provides Django Rest Framework serializers for handling geographic data, including GeoJSON features, vector layers, and raster layers. It includes utility functions like `_best_utm_epsg` to calculate optimal UTM projections for accurate area measurements and specialized serializers that manage spatial geometry transformations. These components serve as the data translation layer between the underlying GIS models and external clients, ensuring spatial data is correctly formatted for web-based mapping applications.
Imports: rest_framework, rest_framework_gis.serializers, django.contrib.gis.geos, tier1apps.gislayers.models, tier1apps.foundations.serializers, tier1apps.clients.constants
Symbols:
- GeoJSONFeatureSerializer (class, L9-11): Defines a machine-readable response shape for a single GeoJSON Feature to ensure consistency in endpoints that hand-build collections.
- _best_utm_epsg (function, L29-40): Best UTM-zone EPSG for a geometry's centroid. Python mirror of ``postgis_expressions.get_best_epsg`` (which is an ORM expression and can't run on a bare GEOS geometry). Used to measure area in an equal-area-at-field-scale projection…
- VectorLayerListSerializer (class, L43-45): Serializes GISVectorLayer model instances for list views, including category details.
- RasterLayerListSerializer (class, L52-54): Serializes GISRasterLayer model instances for list views, providing an absolute URL for the raster file instead of raw file data.
- RasterLayerListSerializer.get_raster_url (method, L68-74): Absolute URL to the raster file, or None when none is attached.
- FieldBoundaryDraftSerializer (class, L77-79): Handles serialization and deserialization of FieldBoundaryDraft objects, including converting between MultiPolygon GeoJSON and GeometryCollection formats.
- FieldBoundaryDraftSerializer.get_geometry_area (method, L114-128): Compute area in acres from the stored GeometryCollection. Measures in the geometry's best local UTM zone — the same approach as ``Field.geometry_area`` (``get_area_expression``). EPSG:3857 (Web Mercator) is conformal, not equal-area, and…
- FieldBoundaryDraftSerializer.to_representation (method, L142-163): Convert GeometryCollection to MultiPolygon GeoJSON for output.
- FieldBoundaryDraftSerializer.to_internal_value (method, L165-178): Convert MultiPolygon GeoJSON to GeometryCollection for storage.
- field_boundary_draft_expected_shape (json_schema): Describes the expected JSON structure for a FieldBoundaryDraft object
- utm_epsg_projection_selection (calculation): Determines the optimal UTM projection (EPSG code) based on a geometry's centroid to ensure accurate area measurements at field scale.
- field_area_acreage_calculation (calculation): Calculates the physical area of a field boundary in acres by transforming the geometry into its local UTM zone to avoid projection distortion.
- geometry_collection_to_multipolygon_serialization (transform): Extracts a MultiPolygon from a stored GeometryCollection to provide a standard GeoJSON response format for clients.
- multipolygon_to_geometry_collection_storage (transform): Wraps incoming MultiPolygon GeoJSON into a GeometryCollection to match the database storage schema.

### tier1apps/gislayers/urls.py (python, 8 lines)
File: tier1apps/gislayers/urls.py (python)
Imports: django.urls, tier1apps.gislayers.views

### tier1apps/gislayers/views.py (python, 39 lines)
This file defines API endpoints for listing GIS vector and raster layers using Django Rest Framework. It provides two main view classes, `VectorLayerList` and `RasterLayerList`, which implement optimized querysets that pre-fetch related category data to reduce database hits. These views serve as the list-based interface within the `gislayers` module, integrating with access control mixins to provide filtered, read-only access to spatial data.
Imports: rest_framework, tier1apps.foundations.views, tier1apps.gislayers.models, tier1apps.gislayers.serializers
Symbols:
- RasterLayerList.get_initial_queryset (method, L33-37)

### tier1apps/gislayers/migrations/ (submodule)
This module defines the foundational database schema for a geospatial data management system, establishing core `GISRasterLayer` and `GISVectorLayer` models to store spatial datasets with metadata, ownership tracking, and file storage capabilities. It includes specialized proxy models like `SoilProperties` and `FieldBoundaryDraft` to enable distinct query behaviors and permission handling for specific data types without altering the underlying database structure. Other code interacts with this module by instantiating these models to manage geospatial resources, utilizing their built-in mixins for versioning and access control through Django's ORM or API interfaces.

### tier1apps/gislayers/models/ (submodule)
This module defines the core geospatial data models for the AgKit.io backend, establishing a foundation for managing both vector and raster GIS layers alongside specialized agricultural proxies like `SoilProperties` and `FieldBoundaryDraft`. Key components include the abstract base classes `GISVectorLayer` and `GISRasterLayer`, which handle dynamic metadata and GeoJSON operations, as well as domain-specific validators that enforce strict geometry types and content object relationships. Other code utilizes these models through views, serializers, and data pipelines to ingest spatial data, visualize field attributes, and manage auditable workflows for proposed boundary changes without altering the underlying database schema.

### tier1apps/gislayers/tests/ (submodule)
This module provides a comprehensive test suite for validating GIS data handling, focusing on vector layer management and API security. It includes unit tests for geospatial geometry serialization (GeoJSON/KML), integration tests for authenticated layer-list endpoints, and validation for importing 2D/3D spatial features. Developers would use this module to ensure the integrity of geographic data persistence and to verify that access control logic correctly handles user authentication during API requests.
```

## Old context (verbatim prompt input)

```
File: tier1apps/gislayers/admin.py (python)

---

File: tier1apps/gislayers/apps.py (python)
Classes: GislayersConfig

---

File: tier1apps/gislayers/exceptions.py (python)
Classes: ContentTypeInvalidError, GeometryTypeInvalidError

---

This file provides Django Rest Framework serializers for handling geographic data, including GeoJSON features, vector layers, and raster layers. It includes utility functions like `_best_utm_epsg` to calculate optimal UTM projections for accurate area measurements and specialized serializers that manage spatial geometry transformations. These components serve as the data translation layer between the underlying GIS models and external clients, ensuring spatial data is correctly formatted for web-based mapping applications.

---

File: tier1apps/gislayers/urls.py (python)

---

This file defines API endpoints for listing GIS vector and raster layers using Django Rest Framework. It provides two main view classes, `VectorLayerList` and `RasterLayerList`, which implement optimized querysets that pre-fetch related category data to reduce database hits. These views serve as the list-based interface within the `gislayers` module, integrating with access control mixins to provide filtered, read-only access to spatial data.

---

This module defines the foundational database schema for a geospatial data management system, establishing core `GISRasterLayer` and `GISVectorLayer` models to store spatial datasets with metadata, ownership tracking, and file storage capabilities. It includes specialized proxy models like `SoilProperties` and `FieldBoundaryDraft` to enable distinct query behaviors and permission handling for specific data types without altering the underlying database structure. Other code interacts with this module by instantiating these models to manage geospatial resources, utilizing their built-in mixins for versioning and access control through Django's ORM or API interfaces.

---

This module defines the core geospatial data models for the AgKit.io backend, establishing a foundation for managing both vector and raster GIS layers alongside specialized agricultural proxies like `SoilProperties` and `FieldBoundaryDraft`. Key components include the abstract base classes `GISVectorLayer` and `GISRasterLayer`, which handle dynamic metadata and GeoJSON operations, as well as domain-specific validators that enforce strict geometry types and content object relationships. Other code utilizes these models through views, serializers, and data pipelines to ingest spatial data, visualize field attributes, and manage auditable workflows for proposed boundary changes without altering the underlying database schema.

---

This module provides a comprehensive test suite for validating GIS data handling, focusing on vector layer management and API security. It includes unit tests for geospatial geometry serialization (GeoJSON/KML), integration tests for authenticated layer-list endpoints, and validation for importing 2D/3D spatial features. Developers would use this module to ensure the integrity of geographic data persistence and to verify that access control logic correctly handles user authentication during API requests.
```