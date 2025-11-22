#!/usr/bin/env python3
"""
Test code similarity retrieval: Can we find similar or duplicate code?

This test evaluates embedding quality by:
1. Checking if the search API is running
2. Using a known code snippet to search for similar code
3. Analyzing result quality (similar patterns, same framework, duplicate code)
"""
import asyncio
import httpx
from typing import List, Dict
import json


# Test code snippet - Django class-based view
TEST_CODE_SNIPPET = '''class ClientDetail(FilteredObjectMixin, FilteredQuerySetMixin, DealershipRequiredMixin, UpdateView):
    form_class = UpdateClientForm
    model = Client
    template_name = "clients/client_update.html"
    success_url = reverse_lazy('client_list')
    client_has_permission = True

    def get_queryset(self):
        qs = super().get_initial_queryset().select_related("originator", "originator__organization")
        qs = qs.prefetch_related("farms", "farms__fields", "farms__fields__hallpass")
        return qs

    def get_form_kwargs(self):
        kwargs = super(ClientDetail, self).get_form_kwargs()
        kwargs['org'] = self.org
        return kwargs

    def get_context_data(self, **kwargs):
        self.set_extra_context()
        context = super(ClientDetail, self).get_context_data(**kwargs)
        context['client'] = self.object
        context['formtype'] = "Edit existing Client"
        fields = Field.objects.filter(farm__client=self.object)
        annotated_fields = PrismSubscription.objects.process_field_queryset_to_provide_attributes(fields)
        context['fields'] = annotated_fields
        context['google_api_key'] = settings.GOOGLE_API_KEY
        return context
'''


async def check_api_health():
    """Step 1: Ensure the search API is up and running"""
    print("=" * 80)
    print("STEP 1: API HEALTH CHECK")
    print("=" * 80)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try to hit a simple endpoint
            response = await client.get('http://localhost:8000/')
            print(f"✓ API is responding (status: {response.status_code})")
            return True
    except httpx.ConnectError:
        print("❌ API is NOT running at http://localhost:8000")
        print("   Please start the API server first:")
        print("   cd 4-consume/api-server && uvicorn main:app --reload")
        return False
    except Exception as e:
        print(f"❌ Error connecting to API: {e}")
        return False


def analyze_result_quality(results: List[Dict], test_snippet: str):
    """Analyze the quality of search results"""
    print("\n" + "=" * 80)
    print("RESULT QUALITY ANALYSIS")
    print("=" * 80)

    if not results:
        print("❌ NO RESULTS FOUND")
        return

    # Analysis metrics
    django_patterns = 0
    class_based_views = 0
    similar_mixins = 0
    update_views = 0
    form_handling = 0
    context_data_methods = 0
    queryset_methods = 0

    print(f"\nAnalyzing {len(results)} results...\n")

    for i, result in enumerate(results[:10], 1):
        content = result.get('content', '')
        file_path = result.get('file_path', 'unknown')
        repo_id = result.get('repo_id', 'unknown')
        score = result.get('score', 0)

        print(f"{i}. {repo_id}/{file_path}")
        print(f"   Score: {score:.4f}")

        # Check for relevant patterns
        patterns_found = []

        if 'UpdateView' in content or 'CreateView' in content or 'DetailView' in content:
            class_based_views += 1
            patterns_found.append("Class-Based View")

        if 'Mixin' in content:
            similar_mixins += 1
            patterns_found.append("Uses Mixins")

        if 'UpdateView' in content:
            update_views += 1
            patterns_found.append("UpdateView")

        if 'get_form_kwargs' in content or 'form_class' in content:
            form_handling += 1
            patterns_found.append("Form Handling")

        if 'get_context_data' in content:
            context_data_methods += 1
            patterns_found.append("Context Data Method")

        if 'get_queryset' in content:
            queryset_methods += 1
            patterns_found.append("Queryset Method")

        if 'django' in file_path.lower() or 'views.py' in file_path:
            django_patterns += 1
            patterns_found.append("Django File")

        if patterns_found:
            print(f"   Patterns: {', '.join(patterns_found)}")
        else:
            print(f"   Patterns: None detected")

        # Show snippet of content
        content_preview = content[:200].replace('\n', ' ')
        if len(content) > 200:
            content_preview += "..."
        print(f"   Preview: {content_preview}")
        print()

    # Summary
    print("=" * 80)
    print("SIMILARITY METRICS")
    print("=" * 80)
    print(f"Django Class-Based Views: {class_based_views}/{min(len(results), 10)}")
    print(f"Using Mixins:             {similar_mixins}/{min(len(results), 10)}")
    print(f"UpdateView pattern:       {update_views}/{min(len(results), 10)}")
    print(f"Form handling:            {form_handling}/{min(len(results), 10)}")
    print(f"get_context_data():       {context_data_methods}/{min(len(results), 10)}")
    print(f"get_queryset():           {queryset_methods}/{min(len(results), 10)}")
    print(f"Django file patterns:     {django_patterns}/{min(len(results), 10)}")
    print()

    # Verdict
    total_relevant = class_based_views + similar_mixins + update_views
    relevance_score = total_relevant / (min(len(results), 10) * 3) * 100

    print("=" * 80)
    print("VERDICT")
    print("=" * 80)

    if relevance_score >= 70:
        print(f"✅ EXCELLENT: {relevance_score:.0f}% relevance")
        print("   Embeddings are finding very similar code patterns!")
    elif relevance_score >= 40:
        print(f"⚠️  MODERATE: {relevance_score:.0f}% relevance")
        print("   Finding some similar patterns but could be better")
    else:
        print(f"❌ POOR: {relevance_score:.0f}% relevance")
        print("   Results don't match the query code pattern")

    print()


async def test_code_similarity():
    """Main test function"""
    print("\n" + "=" * 80)
    print("CODE SIMILARITY RETRIEVAL TEST")
    print("=" * 80)
    print()
    print("Test Objective: Find similar or duplicate code using embeddings")
    print()
    print("Test Code Snippet:")
    print("-" * 80)
    print(TEST_CODE_SNIPPET[:300] + "...")
    print("-" * 80)
    print()

    # Step 1: Check API health
    if not await check_api_health():
        return

    print()

    # Step 2: Search for similar code
    print("=" * 80)
    print("STEP 2: SEMANTIC SEARCH FOR SIMILAR CODE")
    print("=" * 80)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Use the code snippet as the query
            # The API will convert it to an embedding vector automatically
            search_request = {
                "query": TEST_CODE_SNIPPET,
                "limit": 20,
                "doc_type": "code_chunk"
            }

            print(f"Searching with code snippet ({len(TEST_CODE_SNIPPET)} chars)...")
            print(f"Requesting {search_request['limit']} results")
            print()

            response = await client.post(
                'http://localhost:8000/api/chat/search',
                json=search_request
            )

            if response.status_code != 200:
                print(f"❌ Search failed with status {response.status_code}")
                print(f"   Response: {response.text}")
                return

            data = response.json()
            results = data.get('results', [])

            print(f"✓ Search completed successfully")
            print(f"  Found {len(results)} results")
            print()

            # Step 3: Analyze results
            analyze_result_quality(results, TEST_CODE_SNIPPET)

            # Save detailed results
            output_file = "/tmp/code_similarity_results.json"
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"Detailed results saved to: {output_file}")

    except Exception as e:
        print(f"❌ Search request failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_code_similarity())
