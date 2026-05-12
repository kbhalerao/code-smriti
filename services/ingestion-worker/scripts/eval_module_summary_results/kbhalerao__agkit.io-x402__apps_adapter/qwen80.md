# qwen/qwen3-next-80b (thinking) — kbhalerao/agkit.io-x402/apps/adapter
- latency: 3.0s
- input_tokens: 1265
- output_tokens: 128
- reasoning_tokens: 0

## Output

The `apps/adapter` module implements an x402 payment gate and control plane adapter for monetizing API tools, enforcing access via bypass tokens, credit accounts, or payment proofs. Its key components include a Django app configuration (`AdapterConfig`), the `check_x402_gate` payment enforcement logic, a FastMCP server for managing platform resources (wallets, groups, transactions), and test suites validating tool behavior and tenant isolation. Other code integrates this module by adding `apps.adapter` to Django’s `INSTALLED_APPS`, then using its tools and middleware to secure API endpoints and enforce payment policies before tool execution.