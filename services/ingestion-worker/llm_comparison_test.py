#!/usr/bin/env python3
"""
A/B/C Comparison Test for LLM Chunking and Summarization

Compares:
- A: IBM Granite 4 Hybrid Tiny (local, fast, small)
- B: Qwen3-30B (local, slower, larger)
- C: Claude Sonnet 4 (API, quality baseline)

Usage:
    pip install anthropic  # if not installed
    export ANTHROPIC_API_KEY=your_key
    python llm_comparison_test.py
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

import httpx

# Check for anthropic SDK
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("⚠️  anthropic SDK not installed. Run: pip install anthropic")


@dataclass
class ModelConfig:
    name: str
    provider: str  # "lmstudio" or "anthropic"
    model_id: str
    base_url: Optional[str] = None


@dataclass
class TestResult:
    model_name: str
    task: str
    latency_ms: float
    json_valid: bool
    json_parse_error: Optional[str] = None
    output: Any = None
    raw_response: str = ""
    error: Optional[str] = None


# Model configurations
MODELS = {
    "granite": ModelConfig(
        name="Granite 4 H Tiny",
        provider="lmstudio",
        model_id="ibm/granite-4-h-tiny",
        base_url="http://macstudio.local:1234"
    ),
    "qwen": ModelConfig(
        name="Qwen3 30B",
        provider="lmstudio",
        model_id="qwen/qwen3-30b-a3b-2507",
        base_url="http://macstudio.local:1234"
    ),
    "claude": ModelConfig(
        name="Claude Sonnet 4",
        provider="anthropic",
        model_id="claude-sonnet-4-20250514"
    )
}

# Test cases
TEST_CASES = {
    "embedded_sql": {
        "name": "Embedded SQL Detection",
        "language": "python",
        "code": '''
def get_user_transactions(user_id: int, start_date: date, end_date: date) -> List[Transaction]:
    """Fetch all transactions for a user within a date range."""

    query = f"""
        SELECT
            t.id,
            t.amount,
            t.transaction_date,
            t.description,
            c.name as category_name,
            a.account_number
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.user_id = {user_id}
          AND t.transaction_date BETWEEN '{start_date}' AND '{end_date}'
          AND t.status = 'completed'
        ORDER BY t.transaction_date DESC
    """

    results = db.execute(query)
    return [Transaction(**row) for row in results]
'''
    },
    "business_logic": {
        "name": "Business Logic Identification",
        "language": "python",
        "code": '''
class LoanEligibilityCalculator:
    """Calculate loan eligibility based on multiple factors."""

    SCORE_WEIGHTS = {
        "income": 0.30,
        "credit_history": 0.25,
        "employment_length": 0.20,
        "debt_ratio": 0.15,
        "collateral": 0.10
    }

    def calculate_score(self, applicant: LoanApplicant) -> float:
        """Calculate eligibility score (0-100)."""
        base_score = 50

        # Income factor: +20 for income > 100k, +10 for > 50k
        if applicant.annual_income > 100000:
            income_score = 20
        elif applicant.annual_income > 50000:
            income_score = 10
        else:
            income_score = applicant.annual_income / 10000

        # Credit history: direct mapping from credit score
        credit_score = min(applicant.credit_score / 850 * 25, 25)

        # Debt-to-income ratio penalty
        dti = applicant.total_debt / max(applicant.annual_income, 1)
        dti_penalty = max(0, (dti - 0.36) * 50)

        final_score = base_score + income_score + credit_score - dti_penalty
        return max(0, min(100, final_score))

    def is_eligible(self, applicant: LoanApplicant, min_score: float = 65) -> bool:
        """Check if applicant meets minimum eligibility threshold."""
        return self.calculate_score(applicant) >= min_score
'''
    },
    "api_endpoint": {
        "name": "API Endpoint Analysis",
        "language": "python",
        "code": '''
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

class OrderCreate(BaseModel):
    customer_id: int
    items: List[dict]
    shipping_address: str
    payment_method: str

class OrderResponse(BaseModel):
    id: int
    status: str
    total: float
    created_at: datetime

@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new order with validation and inventory check."""
    # Verify customer exists and user has permission
    if not current_user.can_create_orders:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Check inventory availability
    for item in order.items:
        product = db.query(Product).get(item["product_id"])
        if not product or product.stock < item["quantity"]:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {item['product_id']}")

    # Calculate total with tax
    subtotal = sum(item["price"] * item["quantity"] for item in order.items)
    tax = subtotal * 0.08
    total = subtotal + tax

    # Create order record
    db_order = Order(
        customer_id=order.customer_id,
        total=total,
        status="pending",
        shipping_address=order.shipping_address
    )
    db.add(db_order)
    db.commit()

    return OrderResponse.from_orm(db_order)
'''
    },
    "file_summary": {
        "name": "File Summarization",
        "language": "python",
        "code": '''
"""
Authentication and Authorization Module

Handles user authentication via JWT tokens and role-based access control.
"""

from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


class AuthService:
    """Manages authentication operations."""

    def verify_password(self, plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    def hash_password(self, password: str) -> str:
        return pwd_context.hash(password)

    def create_token(self, user_id: int, roles: list) -> str:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {"sub": str(user_id), "roles": roles, "exp": expire}
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    def decode_token(self, token: str) -> dict:
        try:
            return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")


def require_role(allowed_roles: list):
    """Decorator to check user roles."""
    def decorator(func):
        async def wrapper(*args, current_user=Depends(get_current_user), **kwargs):
            if not any(role in current_user.roles for role in allowed_roles):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator
'''
    }
}


# Prompts for different tasks
CHUNKING_PROMPT = """Analyze this {language} code and identify any significant code embedded in strings or important business logic patterns.

Look for:
- SQL queries in strings
- Business logic (validation, calculations, authorization)
- API patterns

Code to analyze:
```{language}
{code}
```

Respond with JSON array of found items:
```json
[
  {{
    "type": "embedded_sql|business_logic|api_endpoint|validation",
    "name": "descriptive_name",
    "content": "the relevant code section",
    "start_line": 1,
    "end_line": 10,
    "purpose": "What this code does",
    "tags": ["tag1", "tag2"],
    "confidence": 0.95
  }}
]
```

Return empty array [] if nothing significant found. Only include items with confidence > 0.7."""


SUMMARY_PROMPT = """Analyze this {language} file and provide a structured summary.

File content:
```{language}
{code}
```

Provide your analysis in this exact JSON format:
{{
    "summary": "2-3 sentence description of what this code does",
    "purpose": "Why does this code exist? What problem does it solve?",
    "key_symbols": [
        {{"name": "SymbolName", "type": "class|function", "purpose": "What it does"}}
    ],
    "usage_pattern": "How would a developer use this?",
    "integrations": ["list", "of", "dependencies"],
    "quality_notes": "Any issues noticed or null"
}}

Respond with only valid JSON."""


class LLMTester:
    """Runs comparison tests across multiple LLMs."""

    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=180.0)
        self.anthropic_client = None
        if ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            self.anthropic_client = anthropic.Anthropic()

    async def call_lmstudio(self, config: ModelConfig, prompt: str) -> tuple[str, float]:
        """Call LM Studio API, returns (response, latency_ms)."""
        start = time.perf_counter()

        response = await self.http_client.post(
            f"{config.base_url}/v1/responses",
            json={
                "model": config.model_id,
                "input": f"You are a code analysis expert. Respond only with valid JSON.\n\n{prompt}",
                "temperature": 0.2,
                "max_output_tokens": 4000
            }
        )
        response.raise_for_status()

        latency = (time.perf_counter() - start) * 1000

        data = response.json()
        output = data.get("output", [])
        for item in output:
            if item.get("type") == "message":
                content = item.get("content", [])
                for block in content:
                    if block.get("type") == "output_text":
                        return block.get("text", ""), latency

        return str(data), latency

    def call_claude_sync(self, prompt: str) -> tuple[str, float]:
        """Call Claude API (sync), returns (response, latency_ms)."""
        if not self.anthropic_client:
            raise RuntimeError("Anthropic client not available")

        start = time.perf_counter()

        message = self.anthropic_client.messages.create(
            model=MODELS["claude"].model_id,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": f"You are a code analysis expert. Respond only with valid JSON.\n\n{prompt}"
                }
            ]
        )

        latency = (time.perf_counter() - start) * 1000
        return message.content[0].text, latency

    def parse_json_response(self, response: str) -> tuple[bool, Any, Optional[str]]:
        """Parse JSON from response. Returns (valid, parsed, error)."""
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        if json_match:
            response = json_match.group(1)

        response = response.strip()

        try:
            parsed = json.loads(response)
            return True, parsed, None
        except json.JSONDecodeError as e:
            # Try fixing common issues
            try:
                fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response)
                parsed = json.loads(fixed)
                return True, parsed, None
            except json.JSONDecodeError:
                return False, None, str(e)

    async def run_test(
        self,
        model_key: str,
        task_name: str,
        prompt: str
    ) -> TestResult:
        """Run a single test."""
        config = MODELS[model_key]

        try:
            if config.provider == "lmstudio":
                raw_response, latency = await self.call_lmstudio(config, prompt)
            elif config.provider == "anthropic":
                if not self.anthropic_client:
                    return TestResult(
                        model_name=config.name,
                        task=task_name,
                        latency_ms=0,
                        json_valid=False,
                        error="Anthropic API key not set"
                    )
                raw_response, latency = self.call_claude_sync(prompt)
            else:
                raise ValueError(f"Unknown provider: {config.provider}")

            json_valid, parsed, parse_error = self.parse_json_response(raw_response)

            return TestResult(
                model_name=config.name,
                task=task_name,
                latency_ms=latency,
                json_valid=json_valid,
                json_parse_error=parse_error,
                output=parsed,
                raw_response=raw_response
            )

        except Exception as e:
            return TestResult(
                model_name=config.name,
                task=task_name,
                latency_ms=0,
                json_valid=False,
                error=str(e)
            )

    async def close(self):
        await self.http_client.aclose()


def print_result(result: TestResult, verbose: bool = False):
    """Pretty print a test result."""
    status = "✅" if result.json_valid else "❌"
    latency = f"{result.latency_ms:.0f}ms"

    print(f"\n{'='*60}")
    print(f"{status} {result.model_name} | {result.task} | {latency}")
    print(f"{'='*60}")

    if result.error:
        print(f"ERROR: {result.error}")
        return

    if not result.json_valid:
        print(f"JSON Parse Error: {result.json_parse_error}")
        if verbose:
            print(f"Raw response (first 500 chars):\n{result.raw_response[:500]}")
        return

    # Pretty print the parsed output
    if isinstance(result.output, list):
        print(f"Found {len(result.output)} items:")
        for item in result.output[:3]:  # Show first 3
            print(f"  • {item.get('type', 'unknown')}: {item.get('name', 'unnamed')}")
            if item.get('purpose'):
                print(f"    Purpose: {item['purpose'][:80]}...")
    elif isinstance(result.output, dict):
        if 'summary' in result.output:
            print(f"Summary: {result.output['summary'][:200]}")
        if 'key_symbols' in result.output:
            print(f"Key symbols: {[s.get('name') for s in result.output['key_symbols'][:5]]}")

    if verbose:
        print(f"\nFull output:\n{json.dumps(result.output, indent=2)[:1000]}")


async def run_comparison(test_cases: List[str] = None, verbose: bool = False):
    """Run full A/B/C comparison."""

    print("\n" + "="*70)
    print("LLM COMPARISON TEST: Granite vs Qwen vs Claude")
    print("="*70)

    tester = LLMTester()

    # Check which models are available
    available_models = []
    for key, config in MODELS.items():
        if config.provider == "anthropic":
            if tester.anthropic_client:
                available_models.append(key)
                print(f"✅ {config.name} available (API)")
            else:
                print(f"⚠️  {config.name} skipped (no API key)")
        else:
            # Quick health check for LM Studio models
            try:
                await tester.http_client.get(f"{config.base_url}/v1/models", timeout=5.0)
                available_models.append(key)
                print(f"✅ {config.name} available")
            except Exception as e:
                print(f"⚠️  {config.name} unavailable: {e}")

    if not available_models:
        print("No models available!")
        return

    # Filter test cases
    cases_to_run = test_cases or list(TEST_CASES.keys())

    all_results = []

    for case_key in cases_to_run:
        case = TEST_CASES[case_key]
        print(f"\n{'='*70}")
        print(f"TEST: {case['name']}")
        print(f"{'='*70}")

        # Test chunking task
        chunking_prompt = CHUNKING_PROMPT.format(
            language=case["language"],
            code=case["code"]
        )

        # Test summary task
        summary_prompt = SUMMARY_PROMPT.format(
            language=case["language"],
            code=case["code"]
        )

        for model_key in available_models:
            # Chunking test
            result = await tester.run_test(
                model_key,
                f"{case['name']} - Chunking",
                chunking_prompt
            )
            all_results.append(result)
            print_result(result, verbose)

            # Summary test
            result = await tester.run_test(
                model_key,
                f"{case['name']} - Summary",
                summary_prompt
            )
            all_results.append(result)
            print_result(result, verbose)

    # Print summary table
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    # Group by model
    model_stats = {}
    for result in all_results:
        if result.model_name not in model_stats:
            model_stats[result.model_name] = {
                "total": 0,
                "json_valid": 0,
                "total_latency": 0,
                "errors": 0
            }
        stats = model_stats[result.model_name]
        stats["total"] += 1
        if result.json_valid:
            stats["json_valid"] += 1
        if result.error:
            stats["errors"] += 1
        stats["total_latency"] += result.latency_ms

    print(f"\n{'Model':<20} {'Success Rate':<15} {'Avg Latency':<15} {'Errors':<10}")
    print("-" * 60)
    for model_name, stats in model_stats.items():
        success_rate = f"{stats['json_valid']}/{stats['total']}"
        avg_latency = f"{stats['total_latency']/max(stats['total'],1):.0f}ms"
        print(f"{model_name:<20} {success_rate:<15} {avg_latency:<15} {stats['errors']:<10}")

    await tester.close()

    return all_results


if __name__ == "__main__":
    import sys

    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    # Optional: specify test cases
    # e.g., python llm_comparison_test.py embedded_sql business_logic
    test_cases = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if not test_cases:
        test_cases = None  # Run all

    asyncio.run(run_comparison(test_cases, verbose))
