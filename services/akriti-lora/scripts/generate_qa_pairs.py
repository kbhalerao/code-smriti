#!/usr/bin/env python3
"""
Generate QA pairs for Akriti LoRA training via grounded exploration.

Uses a hypothesis → verification → emit loop:
1. Model generates candidate Q&A (hypothesis)
2. Each answer is verified via ask_codebase
3. Only grounded answers become training pairs

Run interactively with:
    uv run python scripts/generate_qa_pairs.py --interactive

Or in batch mode:
    uv run python scripts/generate_qa_pairs.py --rounds 50
"""

import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import time
import readline  # For better interactive input
import re

import httpx
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

# LM Studio API endpoint
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen/qwen3-next-80b"

# MCP API endpoints
API_BASE = os.getenv("CODESMRITI_API_URL", "http://macstudio.local") + "/api/rag"
API_USERNAME = os.getenv("CODESMRITI_USERNAME", "")
API_PASSWORD = os.getenv("CODESMRITI_PASSWORD", "")

# Token cache
_auth_token: str | None = None


def get_auth_token() -> str:
    """Get JWT token for API authentication."""
    global _auth_token
    if _auth_token:
        return _auth_token

    if not API_USERNAME or not API_PASSWORD:
        raise ValueError("CODESMRITI_USERNAME and CODESMRITI_PASSWORD must be set in .env")

    base_url = os.getenv("CODESMRITI_API_URL", "http://macstudio.local")
    response = httpx.post(
        f"{base_url}/api/auth/login",
        json={"email": API_USERNAME, "password": API_PASSWORD},
        timeout=30.0,
        verify=False
    )
    response.raise_for_status()
    _auth_token = response.json()["token"]
    return _auth_token


def get_auth_headers() -> dict:
    """Get authorization headers for API calls."""
    token = get_auth_token()
    return {"Authorization": f"Bearer {token}"}


# ANSI colors for interactive mode
class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def list_repos() -> list[dict]:
    """Get list of available repositories."""
    response = httpx.get(
        f"{API_BASE}/repos",
        headers=get_auth_headers(),
        timeout=30.0,
        verify=False
    )
    response.raise_for_status()
    return response.json().get("repos", [])


def search_codebase(query: str, level: str = "module", repo_filter: str = None, limit: int = 5) -> list[dict]:
    """Search the codebase at specified level."""
    payload = {
        "query": query,
        "level": level,
        "limit": limit,
    }
    if repo_filter:
        payload["repo_filter"] = repo_filter

    response = httpx.post(
        f"{API_BASE}/search",
        json=payload,
        headers=get_auth_headers(),
        timeout=60.0,
        verify=False
    )
    response.raise_for_status()
    return response.json().get("results", [])


def ask_codebase(query: str) -> str:
    """Ask a question about the codebase."""
    response = httpx.post(
        f"{API_BASE}/",
        json={"query": query, "stream": False},
        headers=get_auth_headers(),
        timeout=300.0,  # 5 minutes - RAG can be slow
        verify=False
    )
    response.raise_for_status()
    return response.json().get("answer", "")


def call_llm(messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048) -> str:
    """Call LM Studio API."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    response = httpx.post(LM_STUDIO_URL, json=payload, timeout=300.0)  # 5 minutes for 80B
    response.raise_for_status()

    result = response.json()
    return result["choices"][0]["message"]["content"]


# =============================================================================
# STAKEHOLDER PERSPECTIVES
# =============================================================================

STAKEHOLDER_PERSPECTIVES = {
    "executive": {
        "role": "CEO/CTO evaluating AgKit as a technology partner",
        "concerns": [
            "What capabilities already exist?",
            "Time-to-market vs building custom",
            "Total cost of ownership",
            "Scalability and proven deployments",
            "Integration with existing systems",
        ],
        "question_styles": [
            "What can AgKit do for...",
            "How much of X already exists?",
            "Why choose AgKit over...",
            "What's the ROI of...",
        ],
    },
    "engineer": {
        "role": "Software engineer building on AgKit",
        "concerns": [
            "Architecture and data models",
            "Extension points and APIs",
            "How components connect",
            "Testing and validation patterns",
            "Deployment and configuration",
        ],
        "question_styles": [
            "How does X work?",
            "How do I extend...",
            "What's the structure of...",
            "How are X and Y related?",
        ],
    },
    "agronomist": {
        "role": "Field agronomist or consultant using AgKit",
        "concerns": [
            "Managing multiple clients efficiently",
            "Field data and mapping",
            "Workflow automation for operations",
            "Reporting and compliance",
            "Mobile access for field work",
        ],
        "question_styles": [
            "How do I manage...",
            "Can AgKit track...",
            "How does field mapping work?",
            "What reports can I generate?",
        ],
    },
    "farmer": {
        "role": "Farm operator using AgKit day-to-day",
        "concerns": [
            "Ease of use for daily operations",
            "Tracking inputs and costs",
            "Field boundaries and records",
            "Weather and timing decisions",
            "Equipment integration",
        ],
        "question_styles": [
            "How do I record...",
            "Can I see my...",
            "How do I track...",
            "What's the easiest way to...",
        ],
    },
    "regulator": {
        "role": "Compliance officer or auditor",
        "concerns": [
            "Audit trails and traceability",
            "Data integrity and validation",
            "Reporting for certifications",
            "Record retention",
            "Chain of custody for inputs",
        ],
        "question_styles": [
            "How does AgKit ensure...",
            "Can I audit...",
            "What records are kept for...",
            "How is data validated?",
        ],
    },
    "competitor": {
        "role": "Technology evaluator comparing platforms",
        "concerns": [
            "Technical differentiation",
            "Architecture advantages",
            "Extensibility vs locked-in",
            "Open standards support",
            "AI/ML readiness",
        ],
        "question_styles": [
            "What makes AgKit different from...",
            "How does AgKit's architecture compare...",
            "What standards does AgKit support?",
            "How AI-ready is the platform?",
        ],
    },
}

# =============================================================================
# INTERVIEW SYSTEM - The consultant interviews the codebase
# =============================================================================

INTERVIEWER_SYSTEM_PROMPT = """You are a {persona_name} having your first real conversation with the AgKit codebase.

## Your Situation
{persona_description}

## What is AgKit?
AgKit is an agricultural technology platform. labcore is the original codebase, agkit.io-backend is the industrialized rewrite. They share the same "Akriti" - invariant patterns.

## How This Works
You ask questions. The codebase answers with real code snippets and documentation. You learn, then ask follow-up questions based on what surprised you, confused you, or made you curious.

## Be Genuinely Curious
Don't work through a mental checklist. Instead:
- When an answer mentions something unexpected, ask about THAT
- When something is unclear, dig into it
- When you see a pattern, ask where else it appears
- When you find a capability, ask about its limits or edge cases
- Follow the thread wherever it leads

The best interviews happen when you're genuinely trying to understand, not just collecting facts.

## What You've Learned So Far
{learnings}

## Your Next Move

If the last answer taught you something valuable, capture it:
```qa
Q: [a natural question someone might ask]
A: [what you learned, in your own words]
```

Then ask your next question - something that genuinely follows from what you just learned. What are you now curious about? What didn't make sense? What do you want to know more about?

Be specific and direct. No meta-commentary."""

INTERVIEW_PERSONAS = {
    "marketing_consultant": {
        "name": "Marketing Consultant",
        "description": """You're preparing to pitch AgKit to potential customers. You need to understand what it actually does - not vague promises, but concrete capabilities you can demonstrate. You're curious about what makes it different, what's already proven, and what problems it solves for farmers, agronomists, and agribusinesses.""",
    },
    "new_pm": {
        "name": "New Project Manager",
        "description": """You just joined the team and need to get up to speed fast. You're trying to build a mental map: what are the main pieces, how do they connect, what's the data model, where are the extension points? You want to understand the system well enough to plan work and answer developer questions.""",
    },
    "field_consultant": {
        "name": "Agronomist Consultant",
        "description": """You advise multiple farming operations and you're evaluating whether AgKit could help your practice. You care about managing client relationships, tracking field data, automating your workflows, and generating reports. You're practical - you want to know if this will actually make your job easier.""",
    },
    "tech_evaluator": {
        "name": "Technical Evaluator",
        "description": """You're doing a build-vs-buy analysis. You need to understand the architecture deeply enough to assess: Is this well-designed? Can we extend it? Will it integrate with our systems? Is it modern enough to last? You're looking for both strengths and red flags.""",
    },
    "compliance_officer": {
        "name": "Compliance Officer",
        "description": """You need to verify this system can meet regulatory requirements. You care about audit trails, data integrity, record keeping, and change tracking. You're skeptical by nature - you want to see how things actually work, not just be told they're compliant.""",
    },
}

ANSWER_GROUNDING_PROMPT = """You are grounding an answer using verified information from the codebase.

## Question
{question}

## Verified Information from Codebase
{grounded_context}

## Instructions

Write a clear, accurate answer based ONLY on the verified information above.

Rules:
1. Only state what the verified information supports
2. If the information is incomplete, say what IS known and acknowledge gaps
3. Do NOT invent file paths, function names, or technical details not in the context
4. Match your answer style to the question's apparent audience:
   - Business questions → emphasize capabilities and value
   - Technical questions → emphasize architecture and implementation
   - Operational questions → emphasize workflows and how-to

Output your answer directly (no JSON wrapping)."""


# =============================================================================
# INTERVIEW-BASED QA GENERATION
# =============================================================================

@dataclass
class InterviewState:
    """Track interview progress for a persona."""
    persona: str
    questions_asked: list = field(default_factory=list)
    learnings: list = field(default_factory=list)
    qa_pairs: list = field(default_factory=list)
    topics_explored: set = field(default_factory=set)


def ask_codebase_for_interview(question: str, use_rag: bool = False) -> str:
    """
    Ask the codebase a question and return grounded answer.

    This is the interviewer's window into the codebase.

    By default, uses fast search_codebase calls. Set use_rag=True to also
    try the slower ask_codebase RAG endpoint.
    """
    context_parts = []

    # Primary: targeted searches (fast, reliable)
    keywords = extract_keywords_from_question(question)
    for keyword in keywords[:3]:  # Top 3 keywords
        try:
            # Search docs first
            doc_results = search_codebase(keyword, level="doc", limit=2)
            if doc_results:
                formatted = format_search_results(doc_results)
                context_parts.append(f"Documentation '{keyword}':\n{formatted}")

            # Then search code
            code_results = search_codebase(keyword, level="file", limit=2)
            if code_results:
                formatted = format_search_results(code_results)
                context_parts.append(f"Code '{keyword}':\n{formatted}")
        except Exception as e:
            pass

    # Optional: RAG answer (slow, may timeout)
    if use_rag:
        try:
            answer = ask_codebase(question)
            if answer and len(answer) > 50:
                context_parts.insert(0, f"RAG Answer:\n{answer}")
        except Exception as e:
            print(f"      ask_codebase error: {e}")

    return "\n\n---\n\n".join(context_parts) if context_parts else "No relevant information found."


def extract_keywords_from_question(question: str) -> list[str]:
    """Extract key terms for targeted searches."""
    stopwords = {
        "what", "how", "does", "is", "are", "can", "the", "a", "an", "in", "of",
        "to", "for", "with", "on", "at", "by", "from", "agkit", "agkit's", "do", "i",
        "tell", "me", "about", "explain", "describe", "show",
    }
    words = re.findall(r'\b\w+\b', question.lower())
    keywords = [w for w in words if w not in stopwords and len(w) > 2]

    # Boost domain terms
    domain_terms = ["workflow", "client", "farm", "field", "gis", "layer", "contact",
                    "product", "notification", "audit", "permission", "api"]
    for term in domain_terms:
        if term in question.lower() and term not in keywords:
            keywords.insert(0, term)

    return keywords[:5]


def format_search_results(results: list[dict]) -> str:
    """Format search results for interview context."""
    parts = []
    for r in results:
        path = r.get("file_path") or r.get("module_path") or r.get("path", "unknown")
        content = r.get("content", r.get("summary", ""))[:300]
        parts.append(f"[{path}]\n{content}")
    return "\n---\n".join(parts)


def run_interview_turn(state: InterviewState) -> tuple[str, list[dict]]:
    """
    Run one turn of the interview.

    Returns: (interviewer's response, any QA pairs generated)
    """
    persona = INTERVIEW_PERSONAS[state.persona]

    # Build the interviewer's prompt with current state
    system_prompt = INTERVIEWER_SYSTEM_PROMPT.format(
        persona_name=persona["name"],
        persona_description=persona["description"],
        learnings="\n".join(f"- {l[:200]}" for l in state.learnings[-5:]) if state.learnings else "(This is your first question - start wherever you're most curious)",
    )

    # Build conversation so far
    messages = [{"role": "system", "content": system_prompt}]

    # Add recent Q&A history (last 3 exchanges to keep context manageable)
    for i, (q, a) in enumerate(zip(state.questions_asked[-3:], state.learnings[-3:])):
        messages.append({"role": "assistant", "content": f"Interview question: {q}"})
        messages.append({"role": "user", "content": f"Codebase says: {a[:1000]}"})

    # Prompt for next question
    if not state.questions_asked:
        messages.append({"role": "user", "content": "Begin your interview. What's your first question?"})
    else:
        messages.append({"role": "user", "content": "Based on what you've learned, what's your next question? If you've learned enough about a topic, create a QA pair first."})

    # Get interviewer's response
    response = call_llm(messages, temperature=0.7, max_tokens=1024)

    # Parse any QA pairs from the response
    qa_pairs = parse_qa_pairs_from_interview(response)

    # Extract the interview question
    interview_question = extract_interview_question(response)

    if interview_question:
        print(f"    {Colors.CYAN}Interviewer asks:{Colors.END} {interview_question[:70]}...")

        # Get answer from codebase
        codebase_answer = ask_codebase_for_interview(interview_question)

        # Store in state
        state.questions_asked.append(interview_question)
        state.learnings.append(codebase_answer[:500])  # Truncate for context

        print(f"    {Colors.GREEN}Codebase responds:{Colors.END} {codebase_answer[:100]}...")

    # Store any QA pairs generated and append to file immediately
    for qa in qa_pairs:
        state.qa_pairs.append(qa)
        append_qa_pair(qa)  # Incremental save
        print(f"    {Colors.YELLOW}QA pair created:{Colors.END} {qa['instruction'][:50]}...")

    return response, qa_pairs


def parse_qa_pairs_from_interview(response: str) -> list[dict]:
    """Parse QA pairs from interviewer's response."""
    pairs = []

    # Pattern 1: ```qa blocks (strict format)
    qa_pattern = r'```qa\s*\n?Q:\s*(.+?)\s*\n+A:\s*(.+?)\s*```'
    for match in re.finditer(qa_pattern, response, re.DOTALL | re.IGNORECASE):
        q = match.group(1).strip()
        a = match.group(2).strip()
        if q and a and len(a) > 30:
            pairs.append({"instruction": q, "output": a, "input": ""})

    # Pattern 2: **Q:** / **A:** format (markdown bold)
    qa_pattern2 = r'\*\*Q:\*\*\s*(.+?)\s*\n+\*\*A:\*\*\s*(.+?)(?=\n\n|\n\*\*Q:|\Z)'
    for match in re.finditer(qa_pattern2, response, re.DOTALL | re.IGNORECASE):
        q = match.group(1).strip()
        a = match.group(2).strip()
        if q and a and len(a) > 30 and not any(p["instruction"] == q for p in pairs):
            pairs.append({"instruction": q, "output": a, "input": ""})

    # Pattern 3: Q: / A: without formatting (simple)
    qa_pattern3 = r'(?:^|\n)Q:\s*(.+?)\s*\n+A:\s*(.+?)(?=\n\nQ:|\n\n[A-Z]|\Z)'
    for match in re.finditer(qa_pattern3, response, re.DOTALL | re.IGNORECASE):
        q = match.group(1).strip()
        a = match.group(2).strip()
        if q and a and len(a) > 30 and not any(p["instruction"] == q for p in pairs):
            pairs.append({"instruction": q, "output": a, "input": ""})

    return pairs


def extract_interview_question(response: str) -> str | None:
    """Extract the interview question from the interviewer's response."""

    # Meta-phrases that indicate internal monologue, not actual questions
    meta_phrases = [
        "shall i", "should i", "do you want", "would you like",
        "we need to", "let's", "i need to", "i should",
        "shift from", "pivot to", "instead of", "rather than",
        "the question is", "my approach", "i'll", "i will",
    ]

    def is_meta_question(q: str) -> bool:
        q_lower = q.lower()
        return any(meta in q_lower for meta in meta_phrases)

    def is_valid_interview_question(q: str) -> bool:
        """Check if this looks like a real question about AgKit/the codebase."""
        q_lower = q.lower().strip()

        # Must start with a capital letter or question word (not a fragment)
        valid_starts = [
            "what", "how", "does", "can", "is", "are", "which", "where",
            "why", "when", "could", "would", "will", "do", "has", "have",
        ]
        starts_properly = (
            q[0].isupper() or
            any(q_lower.startswith(word) for word in valid_starts)
        )

        if not starts_properly:
            return False

        # Reject fragments that look like they start mid-sentence
        fragment_indicators = [
            q_lower.startswith(")"),
            q_lower.startswith(","),
            q_lower.startswith("and "),
            q_lower.startswith("or "),
            q_lower.startswith("to "),
            q_lower.startswith("for "),
            q_lower.startswith("with "),
            q_lower.startswith("from "),
        ]
        if any(fragment_indicators):
            return False

        # Should be asking about something concrete
        interview_signals = [
            "what", "how", "does", "can", "is there", "are there",
            "which", "where", "workflow", "client", "field", "farm",
            "api", "data", "model", "agkit", "labcore", "module",
            "feature", "capability", "support", "track", "manage",
        ]
        return (
            not is_meta_question(q) and
            any(signal in q_lower for signal in interview_signals) and
            len(q) > 20  # Not too short
        )

    # Look for explicit question markers first
    patterns = [
        r'(?:my (?:next )?question|I\'d like to (?:ask|know)|Let me ask)[:\s]+(.+?\?)',
        r'(?:^|\n)(?:Question|Interview question)[:\s]+(.+?\?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE | re.MULTILINE)
        if match:
            q = match.group(1).strip()
            if is_valid_interview_question(q):
                return q

    # Find all questions in the response
    questions = re.findall(r'([^.!?\n]{20,}\?)', response)

    # Filter to valid interview questions
    valid_questions = [q.strip() for q in questions if is_valid_interview_question(q.strip())]

    if valid_questions:
        # Prefer the last valid question (usually the actual interview question)
        return valid_questions[-1]

    return None


def run_interview_session(persona: str, max_turns: int = 10) -> list[dict]:
    """
    Run a complete interview session with one persona.

    Returns all QA pairs generated.
    """
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.CYAN}Starting interview: {INTERVIEW_PERSONAS[persona]['name']}{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}")

    state = InterviewState(persona=persona)

    for turn in range(max_turns):
        print(f"\n{Colors.YELLOW}Turn {turn + 1}/{max_turns}{Colors.END}")

        try:
            response, qa_pairs = run_interview_turn(state)
            time.sleep(1)  # Rate limiting

            # If interviewer didn't ask a question, they might be done
            if not state.questions_asked or len(state.questions_asked) == len(state.learnings) - 1:
                if turn > 3:  # At least a few turns
                    print(f"  Interview seems complete after {turn + 1} turns")
                    break

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Interview paused{Colors.END}")
            break
        except Exception as e:
            print(f"  {Colors.RED}Error: {e}{Colors.END}")
            continue

    print(f"\n{Colors.GREEN}Interview complete: {len(state.qa_pairs)} QA pairs generated{Colors.END}")
    return state.qa_pairs


def ground_qa_pair(qa: dict) -> dict | None:
    """
    Double-check and ground a QA pair against the codebase.

    This is the verification step to ensure hallucination-free answers.
    """
    question = qa["instruction"]
    proposed_answer = qa["output"]

    # Get fresh grounding from codebase
    fresh_context = ask_codebase_for_interview(question)

    if not fresh_context or len(fresh_context) < 50:
        return None  # Can't verify

    # Have the LLM reconcile the proposed answer with fresh evidence
    prompt = f"""Verify and correct this QA pair using the fresh evidence below.

## Original Question
{question}

## Proposed Answer (may contain hallucinations)
{proposed_answer}

## Fresh Evidence from Codebase
{fresh_context}

## Instructions
1. Check if the proposed answer is supported by the evidence
2. Correct any hallucinated file paths, function names, or technical details
3. Keep information that IS supported by evidence
4. Remove or acknowledge gaps for unsupported claims
5. Output the corrected answer only (no explanation)

Corrected answer:"""

    messages = [{"role": "user", "content": prompt}]
    corrected = call_llm(messages, temperature=0.2, max_tokens=512)

    if corrected and len(corrected) > 50:
        return {
            "instruction": question,
            "output": corrected.strip(),
            "input": "",
        }
    return None


def print_qa_pair(qa: dict):
    """Pretty print a QA pair."""
    print(f"\n{Colors.GREEN}{'─'*60}{Colors.END}")
    print(f"{Colors.BOLD}Q:{Colors.END} {qa['instruction']}")
    print(f"{Colors.BOLD}A:{Colors.END} {qa['output'][:300]}...")
    print(f"{Colors.GREEN}{'─'*60}{Colors.END}")


# =============================================================================
# MAIN INTERVIEW LOOP
# =============================================================================

def run_full_interview_cycle(
    personas: list[str] | None = None,
    turns_per_persona: int = 10,
    verify_qa: bool = True,
) -> list[dict]:
    """
    Run interviews with multiple personas, then verify all QA pairs.

    This is the main entry point for batch generation.
    """
    if personas is None:
        personas = list(INTERVIEW_PERSONAS.keys())

    all_qa_pairs = []

    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.CYAN}Starting interview cycle with {len(personas)} personas{Colors.END}")
    print(f"Personas: {', '.join(personas)}")
    print(f"Turns per persona: {turns_per_persona}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}")

    # Run interviews
    for persona in personas:
        try:
            qa_pairs = run_interview_session(persona, max_turns=turns_per_persona)
            all_qa_pairs.extend(qa_pairs)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Interrupted. Moving to verification...{Colors.END}")
            break
        except Exception as e:
            print(f"{Colors.RED}Error with {persona}: {e}{Colors.END}")
            continue

    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"Interviews complete: {len(all_qa_pairs)} raw QA pairs")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}")

    # Verification pass
    if verify_qa and all_qa_pairs:
        print(f"\n{Colors.CYAN}Verifying QA pairs against codebase...{Colors.END}")
        verified_pairs = []

        for i, qa in enumerate(all_qa_pairs):
            print(f"  [{i+1}/{len(all_qa_pairs)}] Verifying: {qa['instruction'][:50]}...")

            try:
                verified = ground_qa_pair(qa)
                if verified:
                    verified_pairs.append(verified)
                    print(f"    {Colors.GREEN}✓ Verified{Colors.END}")
                else:
                    print(f"    {Colors.YELLOW}✗ Could not verify{Colors.END}")
            except Exception as e:
                print(f"    {Colors.RED}Error: {e}{Colors.END}")

            time.sleep(0.5)  # Rate limiting

        print(f"\n{Colors.GREEN}Verified {len(verified_pairs)}/{len(all_qa_pairs)} QA pairs{Colors.END}")
        return verified_pairs

    return all_qa_pairs


def interactive_interview(persona: str = "marketing_consultant"):
    """
    Run an interactive interview session where user can observe and guide.
    """
    print(f"\n{Colors.BOLD}{Colors.CYAN}Akriti LoRA - Interactive Interview{Colors.END}")
    print("="*60)
    print(f"Interviewer: {INTERVIEW_PERSONAS[persona]['name']}")
    print(f"\n{Colors.YELLOW}The interviewer will explore the codebase.{Colors.END}")
    print(f"Press Ctrl+C to pause and interact.")
    print(f"\nCommands (when paused):")
    print(f"  {Colors.YELLOW}Enter{Colors.END} - Continue interview")
    print(f"  {Colors.YELLOW}/qa{Colors.END} - Show generated QA pairs")
    print(f"  {Colors.YELLOW}/save{Colors.END} - Save QA pairs")
    print(f"  {Colors.YELLOW}/switch <persona>{Colors.END} - Switch interviewer")
    print(f"  {Colors.YELLOW}/verify{Colors.END} - Verify all QA pairs")
    print(f"  {Colors.YELLOW}/quit{Colors.END} - Exit")
    print("="*60 + "\n")

    state = InterviewState(persona=persona)
    all_qa_pairs = []
    turn = 0

    while True:
        turn += 1
        print(f"\n{Colors.YELLOW}Turn {turn}{Colors.END}")

        try:
            response, qa_pairs = run_interview_turn(state)
            all_qa_pairs.extend(qa_pairs)
            time.sleep(1)

        except KeyboardInterrupt:
            print(f"\n\n{Colors.CYAN}Paused. Enter command or press Enter to continue:{Colors.END}")

            try:
                user_input = input("> ").strip()

                if not user_input:
                    continue

                if user_input.startswith("/"):
                    cmd_parts = user_input.split()
                    cmd = cmd_parts[0].lower()

                    if cmd in ("/quit", "/q"):
                        break

                    elif cmd == "/qa":
                        print(f"\n{Colors.BOLD}Generated QA Pairs ({len(all_qa_pairs)}):{Colors.END}")
                        for i, qa in enumerate(all_qa_pairs, 1):
                            print(f"\n{i}. {Colors.YELLOW}Q:{Colors.END} {qa['instruction']}")
                            print(f"   {Colors.GREEN}A:{Colors.END} {qa['output'][:150]}...")

                    elif cmd == "/save":
                        save_qa_pairs(all_qa_pairs)

                    elif cmd == "/switch" and len(cmd_parts) > 1:
                        new_persona = cmd_parts[1]
                        if new_persona in INTERVIEW_PERSONAS:
                            state = InterviewState(persona=new_persona)
                            print(f"Switched to: {INTERVIEW_PERSONAS[new_persona]['name']}")
                        else:
                            print(f"Unknown persona. Available: {', '.join(INTERVIEW_PERSONAS.keys())}")

                    elif cmd == "/verify":
                        print(f"\n{Colors.CYAN}Verifying {len(all_qa_pairs)} QA pairs...{Colors.END}")
                        verified = []
                        for qa in all_qa_pairs:
                            result = ground_qa_pair(qa)
                            if result:
                                verified.append(result)
                                print(f"  {Colors.GREEN}✓{Colors.END} {qa['instruction'][:50]}...")
                            else:
                                print(f"  {Colors.RED}✗{Colors.END} {qa['instruction'][:50]}...")
                        all_qa_pairs = verified
                        print(f"Kept {len(verified)} verified pairs")

                    else:
                        print(f"Unknown command: {cmd}")

                else:
                    # User wants to inject a question/direction
                    print(f"Injecting your question into the interview...")
                    # Add to state as if interviewer asked it
                    codebase_answer = ask_codebase_for_interview(user_input)
                    state.questions_asked.append(user_input)
                    state.learnings.append(codebase_answer[:500])
                    print(f"\n{Colors.GREEN}Codebase says:{Colors.END} {codebase_answer[:300]}...")

            except EOFError:
                break

        except Exception as e:
            print(f"{Colors.RED}Error: {e}{Colors.END}")
            continue

    # Save on exit
    if all_qa_pairs:
        print(f"\n{Colors.BOLD}Interview ended with {len(all_qa_pairs)} QA pairs{Colors.END}")
        save = input("Save QA pairs? [Y/n] ").strip().lower()
        if save != 'n':
            save_qa_pairs(all_qa_pairs)


def save_qa_pairs(qa_pairs: list[dict], filename: str = "qa_pairs.jsonl"):
    """Save QA pairs to file (overwrites)."""
    output_file = Path(__file__).parent.parent / "data" / filename
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        for qa in qa_pairs:
            f.write(json.dumps(qa) + "\n")
    print(f"{Colors.GREEN}Saved {len(qa_pairs)} QA pairs to {output_file}{Colors.END}")


def append_qa_pair(qa: dict, filename: str = "qa_pairs_v2.jsonl"):
    """Append a single QA pair to file (incremental save)."""
    output_file = Path(__file__).parent.parent / "data" / filename
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "a") as f:
        f.write(json.dumps(qa) + "\n")


def main():
    """Run QA generation via interview-based approach."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate QA pairs via persona-based interviews with the codebase",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive interview with marketing consultant
  uv run python scripts/generate_qa_pairs.py --interactive

  # Batch run with all personas (5 turns each)
  uv run python scripts/generate_qa_pairs.py --turns 5

  # Run specific personas
  uv run python scripts/generate_qa_pairs.py --personas marketing_consultant new_pm --turns 10

  # Skip verification (faster, less accurate)
  uv run python scripts/generate_qa_pairs.py --no-verify

Available personas:
  - marketing_consultant: Sales/positioning focus
  - new_pm: Architecture/modules focus
  - field_consultant: Agronomist operations focus
  - tech_evaluator: Technical comparison focus
  - compliance_officer: Audit/traceability focus
        """
    )
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Run in interactive mode (observe and guide)")
    parser.add_argument("--persona", type=str, default="marketing_consultant",
                        help="Persona for interactive mode")
    parser.add_argument("--personas", nargs="+", default=None,
                        help="Personas to use (batch mode). Default: all")
    parser.add_argument("--turns", type=int, default=10,
                        help="Interview turns per persona")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip verification pass (faster but may have hallucinations)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path")
    args = parser.parse_args()

    # Check connectivity
    print("Checking API connectivity...")
    try:
        repos = list_repos()
        print(f"  RAG API: OK ({len(repos)} repos)")
    except Exception as e:
        print(f"  RAG API error: {e}")
        sys.exit(1)

    try:
        httpx.post(
            LM_STUDIO_URL,
            json={"model": MODEL, "messages": [{"role": "user", "content": "test"}], "max_tokens": 10},
            timeout=30.0
        )
        print(f"  LM Studio: OK")
    except Exception as e:
        print(f"  LM Studio error: {e}")
        sys.exit(1)

    if args.interactive:
        # Interactive mode - single persona, user can observe and intervene
        if args.persona not in INTERVIEW_PERSONAS:
            print(f"Unknown persona: {args.persona}")
            print(f"Available: {', '.join(INTERVIEW_PERSONAS.keys())}")
            sys.exit(1)
        interactive_interview(args.persona)
    else:
        # Batch mode - run multiple personas
        personas = args.personas
        if personas:
            # Validate personas
            for p in personas:
                if p not in INTERVIEW_PERSONAS:
                    print(f"Unknown persona: {p}")
                    print(f"Available: {', '.join(INTERVIEW_PERSONAS.keys())}")
                    sys.exit(1)

        qa_pairs = run_full_interview_cycle(
            personas=personas,
            turns_per_persona=args.turns,
            verify_qa=not args.no_verify,
        )

        # Save results
        if qa_pairs:
            filename = args.output if args.output else "qa_pairs_v2.jsonl"
            save_qa_pairs(qa_pairs, filename)
            print(f"\nGenerated {len(qa_pairs)} grounded QA pairs")
        else:
            print("\nNo QA pairs generated")


if __name__ == "__main__":
    main()
