# google/gemma-4-26b-a4b (no thinking) — kbhalerao/agkit.io-x402/apps/adapter
- latency: 2.6s
- input_tokens: 1342
- output_tokens: 132
- reasoning_tokens: 0

## Output

The `apps/adapter` module serves as a specialized MCP (Model Context Protocol) adapter that implements an x402 payment gate to manage monetization and access control for API tools. It provides a secure gateway that validates user eligibility through bypass tokens, credit accounts, or payment proofs before allowing tool execution. The module is composed of a Django-integrated FastMCP server, a suite of management tools for handling wallets, catalogs, and transactions, and a robust testing framework to ensure tenant isolation and correct authorization logic. Other components in the system use this module as a central gatekeeper to enforce paid access and programmatically interact with the x402 control plane.