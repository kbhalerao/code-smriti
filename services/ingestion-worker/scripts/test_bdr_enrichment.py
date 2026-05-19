#!/usr/bin/env python3
"""
Test BDR Enrichment

Tests the BDR (Business Development Representative) enrichment prompt
on the agkit.io-backend repository using Nemotron thinker.

Uses chat/completions API directly to capture thinking traces.
"""

import asyncio
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_enricher import LLMEnricher, LLMConfig
from loguru import logger

# Configure for Nemotron-3-nano (30b thinking model)
LLM_CONFIG = LLMConfig(
    provider="lmstudio",
    model="nvidia/nemotron-3-nano",
    base_url="http://macstudio.local:1234",
    temperature=0.3,
    max_tokens=8000,
    timeout_seconds=300.0,
)


# Repo data from code-smriti
REPO_ID = "kbhalerao/agkit.io-backend"

REPO_SUMMARY = """AgKit.io-backend is a Django-based geospatial application platform designed for agricultural and environmental data management, enabling multi-tenant, role-based access to structured client, farm, field, contact, and event data with rich spatial capabilities via PostGIS. It leverages Django, DRF (Django REST Framework), and PostgreSQL/PostGIS for robust backend operations, with modular architecture supporting scalable, secure, and extensible workflows. Key modules include `clients` (geospatial client/farm/field management), `foundations` (dynamic forms, custom fields), `associates` (organization/user management), `gislayers` (vector/raster GIS data), `events` (calendar integration), `products` (product catalog), and `workflows` (business process modeling). Tier2 apps add data integrations (soils, topography, notifications, zone_maker), while Tier3 provides domain-specific modules like LIMS for laboratory workflows."""

README_CONTENT = """# AgKit.io (fmr. LabCoreOS)

The next generation web framework for geospatial businesses

## Value Proposition of AgKit

AgKit.io empowers agricultural and environmental businesses with state-of-the-art tools for managing
geospatial workflows. Its highly customizable architecture makes it ideal for
organizations seeking to reduce costs and increase outcome quality through automation.

### Whom is AgKit.io for?

AgKit.io is particularly useful to create tools for:

- **Agronomy service providers** delivering decision support and crop management services.
- **Environmental consultants** managing geospatial data for land use and conservation.
- **Input suppliers and dealers** ensuring efficient delivery and utilization of resources.
- **Landowners and Farm managers** seeking a comprehensive view of their assets and operations.
- **Technology and SaaS companies** building ecosystem management tools for the agricultural domain.

### Key Value Proposition

1. **Geospatial Intelligence Integration**
   AgKit.io enables organizations to leverage GIS data for spatial reasoning. Visualize your assets
   on a map, implement tracking and documentation, and optimize operations.
2. **Dynamic, Customizable Workflows**
   Use the `DynamicForm` and `Workflow` modules to precisely model your data intake workflow.
   Reduce time spent on collecting information while increasing consistency of the resulting
   products and services.
3. **Scalable and Modular Design**
   AgKit.io comes with lightweight, robustly-tested and production-ready models, APIs, and admin interfaces.
   Its modular architecture means that you can construct your proprietary business logic
   without having spent valuable resources on reinventing the wheel.

## Rationale for AgKit

Businesses in the Agricultural and Environmental services need
web-based GIS-enabled platforms to improve their productivity.

Most commercial off-the-shelf solutions don't quite fit the bill,
thus creating the need for numerous consulting service that end up
reinventing the wheel.

AgKit.io is an attempt to provide some basic building blocks for a
well-tested and production-ready system upon which to build custom
business workflows.

LabCore, the predecessor to this codebase has been in production since
early 2019, and is currently serving numerous companies.

## Tier I applications: Core models

- Foundations: Abstract models, DynamicForm, CustomField
- Contacts: User, Person, Company records
- Associates: Organizations, OrganizationUsers, Roles
- Clients: Client, Farm, Field (geospatial hierarchy)
- GISLayers: Vector and raster GIS data attachments
- Events: Calendar events with dynamic forms
- Products: Product catalog with pricing
- Workflows: Business process modeling

## Tier II Applications: Data Integration

- SSURGO Soils Data (NRCS, USDA)
- Cropland Data Layer (NASS, USDA)
- GRACE-FO Soil Moisture data (NASA)
- PRISM Weather Data (NRCS, USDA)
- Flood Maps (FEMA)

## Tier III Applications: Domain-specific modules

- LIMS: Laboratory Information Management System
  - Sample handling, measurements, lab workflows
  - Jobs, Samples, Trays, Batches, Measurements
"""

MODULE_SUMMARIES = """
### tier1apps/clients/models/
Defines core geospatial data models for client management, organizing clients, farms, and fields with rich spatial capabilities. Key components include Client, Farm, and Field models with GeoJSON serialization, spatial queries, and access control via SharedAccessPass.

### tier1apps/gislayers/models/
Provides flexible framework for managing geospatial data layers—both vector and raster. Key components include GISLayerBase, GISVectorLayer, GISRasterLayer, and domain-specific proxy models like SoilProperties.

### tier1apps/foundations/
Contains abstract models (timestamped, named), DynamicForm for runtime form definition, CustomField and CustomFieldOption for dynamic field types.

### tier1apps/associates/
Enables organization-based user management and role-based access control. Organization, OrganizationUser, and Role models for multi-tenant support.

### tier2apps/zone_maker/
Generates and manages spatial zones using geospatial data for agricultural or land-use modeling. RasterPreprocessor for standardizing inputs like field boundaries and soil layers.

### tier2apps/soils/
Handles ingestion and processing of soil data, creating GIS raster layers and persisting to SoilProperties model.

### tier2apps/notifications/
Real-time notification system using WebSocket-based communication. NotificationConsumer for broadcasting, token-based authentication.

### tier3apps/lims/
Laboratory Information Management System for tracking sample workflows, measurements, and lab operations. Sample, Job, Measurement, MeasurableConstituent models. REST API, admin interfaces, workflow integration.

### tier1apps/products/
Product and pricing management with multi-currency, role-based visibility, and promotional pricing. Product, Price, SaleUnit, Discount models.

### tier1apps/workflows/
Business process modeling with configurable stages and transitions.
"""


async def generate_bdr_brief():
    """Generate BDR brief using Qwen3-Next-80b."""

    enricher = LLMEnricher(LLM_CONFIG)

    prompt = f"""You are helping a Business Development Representative understand
what business value this codebase represents. Translate technical capabilities
into business intelligence that helps match prospects to solutions.

## Repository: {REPO_ID}

## Technical Summary:
{REPO_SUMMARY}

## README:
{README_CONTENT}

## Module Summaries:
{MODULE_SUMMARIES}

---

Analyze this repository and generate a BDR brief:

### BUSINESS VALUE
What business outcome does this enable? Focus on ROI, efficiency, risk reduction,
or competitive advantage — not technical features.

### TARGET PROSPECTS
Who specifically would need this? Be concrete about:
- Industry/segment
- Role/title
- Company type

### PAIN POINTS ADDRESSED
What problems are these prospects experiencing that this solves?
Write as the prospect would describe it (not technical jargon).

### DISCOVERY QUESTIONS
What should the BDR ask to qualify if this is a fit?
5-7 questions that reveal whether the prospect has the problem this solves.

### PROSPECT SIGNALS
How would a prospect describe this need? Write 5-10 ways they might phrase it.

### KEYWORD TRIGGERS
Terms that should trigger retrieval of this capability:
- Business terms (what prospects say)
- Technical terms (what engineers say)
- Acronyms and expansions
- Adjacent concepts

### NOT A FIT
When should the BDR disqualify? What problems does this NOT solve?

### ADJACENT OPPORTUNITIES
If a prospect needs this, what else might they need?

### COMPETITIVE CONTEXT
What alternatives exist? How is this different?
(If unknown, say "requires market research")
"""

    logger.info(f"Generating BDR brief for {REPO_ID}")
    logger.info(f"Using model: {LLM_CONFIG.model}")
    logger.info(f"Prompt length: {len(prompt)} chars")

    try:
        # Use generate_with_reasoning to capture thinking trace
        result = await enricher.generate_with_reasoning(prompt)
        reasoning = result.get("reasoning")
        output = result.get("output")
        usage = result.get("usage", {})

        print("\n" + "="*80)
        print("BDR BRIEF: " + REPO_ID)
        print("="*80 + "\n")

        if reasoning:
            print("## REASONING TRACE ##")
            print(reasoning)
            print("\n" + "-"*40 + "\n")

        print("## OUTPUT ##")
        print(output)
        print("\n" + "="*80)

        if usage:
            reasoning_tokens = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
            logger.info(f"Tokens - input: {usage.get('input_tokens')}, output: {usage.get('output_tokens')}, reasoning: {reasoning_tokens}")

        # Save to file (model name in filename for comparison)
        model_suffix = LLM_CONFIG.model.replace("/", "_")
        output_path = Path(__file__).parent / f"bdr_brief_agkit_backend_{model_suffix}.md"
        with open(output_path, "w") as f:
            f.write(f"# BDR Brief: {REPO_ID}\n\n")
            f.write(f"Generated by: {LLM_CONFIG.model}\n\n")
            f.write("---\n\n")

            if reasoning:
                f.write("## Reasoning Trace\n\n")
                f.write(f"```\n{reasoning}\n```\n\n")
                f.write("---\n\n")

            f.write(output)

        logger.info(f"Saved to {output_path}")

    except Exception as e:
        logger.error(f"Failed: {e}")
        raise
    finally:
        await enricher.close()


if __name__ == "__main__":
    asyncio.run(generate_bdr_brief())
