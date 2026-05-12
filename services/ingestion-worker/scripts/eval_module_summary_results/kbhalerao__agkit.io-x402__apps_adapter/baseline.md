# Baseline (stored): kbhalerao/agkit.io-x402/apps/adapter

- file_count: None

## Output

The `apps/adapter/` module serves as the core backend for the x402 Control Plane, providing a secure, tool-driven API layer for managing platform resources like groups, items, wallets, and transactions. Its key components include a FastMCP server with transport flexibility, role-based tools with authentication and tenant isolation, an x402 payment gate for monetized access, and comprehensive tests ensuring correctness and security. Other code uses it via the `mcp.tool()` decorator to expose validated endpoints, leveraging built-in auth, tenant resolution, and payment gating for secure, scalable interactions with the control plane.
