# Inputs for kbhalerao/agkit.io

- commit_hash: 9a2aa726be5b7b00a7962c74758387205ced088e
- prompt_chars: 15098

## Repo summary

AgKit.io is a SvelteKit-based web application that provides a modern, accessible UI toolkit for building consistent, component-driven interfaces—particularly for developer tools and dashboards. Built with Svelte, `bits-ui`, Tailwind CSS, and `tailwind-variants`, it leverages a modular architecture to offer reusable, accessible components such as `Button`, `Card`, `Dialog`, `Input`, `Avatar`, `Badge`, `Breadcrumb`, `Command`, and `Label`. These components are designed with semantic HTML, ARIA support, and consistent styling via `cn()` and `tailwind-variants`, enabling developers to compose complex UIs with minimal boilerplate using simple props like `variant` and `size`. The modules work together seamlessly through a shared design system, with navigation, authentication, and session management (via `load` and server actions) integrated into the UI flow—ensuring secure, reactive, and cohesive user experiences across the app.

## README (first 5000 chars)

(No README available)

## Module summaries

### (root)/
This module is a SvelteKit-based web application framework that enables rapid development, testing, and deployment of modern web applications. Its key components include SvelteKit with Vite for development and build, Tailwind CSS for styling, ESLint with Prettier for code quality, Playwright for end-to-end testing, and Vitest for unit and integration tests. Other code uses this module through standardized npm scripts (e.g., `npm run dev`, `npm test`, `npm run build`) to start the dev server, run tests, or generate a production-ready build, leveraging its integrated tooling and configuration.

### e2e/
The `e2e/` module in the `agkit.io` repository runs end-to-end tests to validate the application's core user experience, primarily ensuring the homepage loads correctly. It uses Playwright to navigate to the root URL and verify that the `<h1>` element is visible, acting as a critical check for UI integrity. Other parts of the codebase rely on this module to confirm that changes don't break the homepage's basic functionality, making it essential for CI/CD and quality assurance.

### src/
This module is the core UI and application framework for AgKit.io, built with SvelteKit, Tailwind CSS, and TypeScript. It provides reusable, accessible components (like Button, Input, and Dialog), a theme system with light/dark mode support, authentication with JWT and OAuth, protected routing, and service worker-based offline capabilities. Other parts of the app consume these components via simple declarative props, use auth guards in `load` functions, and rely on the service worker for fast, offline-first performance—all integrated seamlessly through SvelteKit’s built-in tooling.

### src/lib/
This module provides a reusable, accessible UI component library for the AgKit.io application, built with Svelte and styled with Tailwind CSS and `tailwind-variants` for consistent, customizable designs. Key components include Button, Input, Card, Dialog, Breadcrumb, Avatar, Badge, and Command, each optimized for semantic markup, accessibility, and seamless integration. Other parts of the codebase import these components using simple, declarative props like `variant` or `size` to quickly build consistent, maintainable user interfaces with minimal boilerplate.

### src/lib/components/
This module provides a reusable, accessible UI component library for the AgKit.io application, built with Svelte, `bits-ui`, Tailwind CSS, and `tailwind-variants` for consistent, customizable styling. Key components include Button, Input, Card, Dialog, Breadcrumb, Avatar, Badge, and Command, each designed for semantic markup, accessibility, and seamless integration. Other parts of the codebase import these components with simple props like `variant` or `size` to quickly construct consistent, maintainable UIs with minimal boilerplate.

### src/lib/components/navigation/
This module provides a centralized navigation system for the AgKit.io application, managing breadcrumbs, user profiles, and keyboard-driven commands. Key components include `TopNavBar.svelte` (the main navigation bar), `SiteBreadCrumbs` (a singleton service for reactive breadcrumb state), `CommandsBar.svelte` (a platform-aware command palette), and type definitions for navigation and user data. Other parts of the app use these components to display consistent navigation UI, update breadcrumbs globally, trigger actions via keyboard shortcuts, and render user-specific content—all with strong type safety and reactive state management.

### src/lib/components/ui/
This module provides a comprehensive, reusable library of accessible UI components for a Svelte application, built with `bits-ui`, Tailwind CSS, and `tailwind-variants` for consistent, customizable styling. Key components include Avatar, Badge, Breadcrumb, Button, Card, Command, Dialog, Input, and Label—each designed for semantic structure, accessibility, and seamless integration. Other parts of the codebase import and compose these components using props like `variant`, `size`, or `href` to build consistent, maintainable UIs with minimal boilerplate.

### src/lib/components/ui/avatar/
This module provides reusable, accessible avatar components for a Svelte application using the `bits-ui` library and Tailwind CSS. It exports three core components: `Root` (the container), `Image` (for displaying user images), and `Fallback` (for default placeholders like initials). Developers import and compose these components to create consistent, styled avatars—e.g., `<Avatar><AvatarImage src="..." /><AvatarFallback>JD</AvatarFallback></Avatar>`—with customizable size, styling, and fallback behavior.

### src/lib/components/ui/badge/
This module provides a reusable, styled Badge component for displaying small, labeled indicators like status tags in a consistent, accessible way. It includes a main `Badge` Svelte component and a `badgeVariants` function that defines customizable visual states (e.g., default, secondary, destructive, outline) using Tailwind CSS and `tailwind-variants`. Developers import the `Badge` component and apply a `variant` prop to style it according to context, with optional `href` support for link behavior. It’s used throughout the app to maintain visual consistency in UI elements like status indicators or tags.

### src/lib/components/ui/breadcrumb/
This module provides a reusable, accessible breadcrumb navigation system for Svelte applications using Tailwind CSS. It includes key components like `Root` (navigation container), `List` (ordered list wrapper), `Item` (list item), `Link` (navigable item), `Separator` (visual divider), `Page` (current page indicator), and `Ellipsis` (omitted items placeholder). Developers import and compose these components to build hierarchical navigation trails, passing `href`, `children`, and custom classes to create accessible, styled breadcrumbs with minimal boilerplate.

### src/lib/components/ui/button/
This module provides a reusable, styled Button component for consistent UI rendering across the application. It includes a `Button` Svelte component with support for variants (e.g., default, destructive, outline), sizes (e.g., sm, lg, icon), and dynamic rendering as `<button>` or `<a>` based on props, powered by Tailwind CSS and `tailwind-variants`. Key exports include `ButtonProps` for type safety, `buttonVariants` for styling logic, and `cn` for class merging. Other code imports `Button` from `@/lib/components/ui/button` and uses it with props like `variant="outline"` or `size="lg"` to create consistent, accessible buttons.

### src/lib/components/ui/card/
This module provides a collection of reusable, accessible Svelte components for building consistent card UIs in a Svelte application. Key components include `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, and `CardFooter`, each designed for semantic structure and Tailwind CSS styling. Developers import and compose these components to create structured, accessible card layouts—such as for posts, products, or user info—with minimal boilerplate and full customization via `class` props and spread attributes.

### src/lib/components/ui/command/
This module provides a set of reusable, accessible Svelte components for building keyboard-driven command menus—commonly used for search, navigation, or action triggering in UIs. Key components include `Command`, `CommandInput`, `CommandList`, `CommandItem`, `CommandGroup`, `CommandEmpty`, `CommandSeparator`, `CommandShortcut`, and `CommandLoading`, all styled with Tailwind via `cn()` and built on the `bits-ui` primitives. Developers use these components together to construct consistent, keyboard-navigable command interfaces—such as global command palettes or modal search menus—by composing them within a `Command` container and binding values and refs for dynamic behavior.

### src/lib/components/ui/dialog/
This module provides a set of accessible, styled Svelte components for building modal dialogs with proper ARIA roles and keyboard navigation. Key components include `Dialog`, `DialogTitle`, `DialogContent`, `DialogDescription`, `DialogHeader`, `DialogFooter`, `DialogOverlay`, and `DialogClose`, each designed for specific UI roles and enhanced with Tailwind styling via `cn()`. Other parts of the codebase import these components to construct consistent, reusable modals—such as confirmation dialogs or form overlays—by composing them within a dialog structure, ensuring a seamless and accessible user experience.

### src/lib/components/ui/input/
This module provides a reusable, accessible, and styled input component for use in forms across the application. It exports a `Root` component (aliased as `Input`) that renders a standard text input with Tailwind CSS styling, focus states, and disabled handling via the `cn` utility. Developers import and use it like a regular `<input>`, passing props for value binding, custom classes, and additional attributes through `restProps`. It serves as a foundational UI element in the component library, ensuring consistent input styling and behavior throughout the app.

### src/lib/components/ui/label/
This module provides an accessible, styled label component for use in forms, ensuring proper accessibility and visual consistency. It exports a `Label` component built on `LabelPrimitive` from `bits-ui`, enhanced with Tailwind styling and dynamic class handling via `cn()`. The label can be used with a `for` attribute to associate with form inputs, such as `<Label for="email">Email</Label>`. Other code imports and uses it within forms to label inputs reliably and semantically.

### src/routes/
This module implements authentication and routing logic for a SvelteKit-based web application, managing user sessions, protected routes, and secure API interactions. Key components include server-side `load` functions for user data fetching, JWT-based login with OAuth support, session management via HTTP-only cookies, and reusable UI for login, landing, and error pages. Other parts of the app use it by navigating to protected routes, submitting login forms, or rendering authenticated layouts that display user-specific content.

### src/routes/(protected)/
This module manages authenticated user sessions in a SvelteKit application, ensuring only logged-in users can access protected routes. It includes a `load` function that validates session cookies and fetches user data, and a logout action that destroys the session and redirects to the home page. Other parts of the app use it by navigating to protected routes or submitting the logout form, triggering secure session validation and termination.

### src/routes/(protected)/logout/
This module handles secure user logout within a protected route, invalidating sessions and clearing authentication tokens. It consists of a server action that deletes cookies and invalidates the session via a POST request, and a Svelte page that renders a confirmation interface with breadcrumbs and a destructive logout button. Other parts of the app use this module by navigating to the logout route or submitting the form, triggering session termination and redirecting to the home page.

### src/routes/login/
This module handles user authentication in a SvelteKit application, providing a secure login flow via email/password or Google OAuth. It consists of a server action (`+page.server.ts`) that validates credentials against a backend API and sets a JWT token in an HTTP-only cookie, and a Svelte component (`+page.svelte`) that renders a responsive login form with input validation, error handling, and loading states. Other parts of the app use this module by routing to the login page and relying on the server action to manage authentication state and redirect users post-login.
