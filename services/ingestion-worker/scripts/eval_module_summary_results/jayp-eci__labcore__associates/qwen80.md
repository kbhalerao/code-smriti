# qwen/qwen3-next-80b (thinking) — jayp-eci/labcore/associates
- latency: 3.1s
- input_tokens: 1166
- output_tokens: 134
- reasoning_tokens: 0

## Output

The `associates` module manages organization-user relationships, roles, and access control within the codebase, serving as the central system for defining dealerships, assigning roles (e.g., Primary, Dealer), and enforcing permission rules. Key components include Django models (`Organization`, `OrganizationUser`), admin interfaces, DRF serializers and APIs for CRUD operations, custom permissions (`IsSuperUserOrIsOrgStaffUser`), and role-based privilege resolution via `UserPrivilegeResolution`. Other parts of the codebase use it to authenticate users, restrict access to organizational resources, onboard new users via invitations, and dynamically apply role-based permissions in both web UIs and APIs.