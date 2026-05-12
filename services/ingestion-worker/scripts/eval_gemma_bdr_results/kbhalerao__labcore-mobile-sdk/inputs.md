# Inputs for kbhalerao/labcore-mobile-sdk

- commit_hash: 85bc54cd476ec10c907f01635d3b8ca190d066ac
- prompt_chars: 10727

## Repo summary

The Labcore Mobile SDK is a React Native-based mobile application framework designed for agricultural data collection, enabling farmers and field workers to create and manage field boundaries, track GPS data, and operate seamlessly offline. It leverages Redux with redux-offline, redux-saga, and redux-thunk for centralized, offline-first state management, while integrating React Navigation for screen routing, MapboxGL for GIS visualization, and AsyncStorage for local persistence. Key modules include `store` for global state (e.g., field data, map UI), `screens` for user workflows (e.g., field creation, data upload), `components` for reusable UI (e.g., interactive map markers, multi-select dropdowns), and `constants` for environment and theme configuration. These modules work cohesively: actions dispatched from screens update state via Redux, sagas handle complex async operations (like sorting subfield layers), and shared components and configurations ensure consistent, maintainable UI and behavior across the app.

## README (first 5000 chars)

(No README available)

## Module summaries

### Labcore/
The Labcore module is a React Native-based mobile SDK for agricultural data collection, designed to support offline-first workflows with centralized state management and modular navigation. Key components include Redux-based state management (via redux-offline, redux-saga, and redux-thunk), React Navigation for screen routing, reusable UI components (like Mapbox-powered `ShapeSource` and `MultiSelect2`), and shared configuration (theme, constants, icons). Other parts of the app import screens from `screens/`, state and actions from `store/`, UI components from `components/`, and configuration from `constants/`, enabling consistent, scalable, and maintainable development across features like field management, data upload, and user authentication.

### Labcore/__tests__/
The Labcore/__tests__/ module contains unit tests for the React Native App component, ensuring its rendering stability across updates. It uses `react-test-renderer` to generate and validate snapshot outputs, verifying that the UI structure remains intact. Other parts of the codebase rely on these tests to catch unintended changes during development or deployment. This module plays a critical role in maintaining UI consistency within the Labcore Mobile SDK.

### Labcore/components/
The Labcore/components module provides reusable React Native UI components for a mobile health application, primarily focused on map visualization and user input controls. It includes `ShapeSource` for rendering interactive point markers on a MapboxGL map—used to display locations and trigger logout actions—and `MultiSelect2`, a customizable multi-select dropdown for choosing football teams with visual feedback. These components are designed to integrate seamlessly into larger app screens, such as dashboards or filtering interfaces, leveraging external libraries like MapboxGL, react-native-multi-selectbox, and lodash for functionality and styling. Other parts of the app use them to render location data and collect user preferences in a consistent, branded interface.

### Labcore/constants/
This module provides a centralized configuration for consistent UI and application behavior in the Labcore mobile app. It exports key components including theme variables (COLORS, SIZES, FONTS, STYLES), icon asset paths, and global constants like SEND_DATA. Other parts of the codebase import from this module to uniformly apply styling, reference icons, and access shared values without duplication. This ensures visual consistency and simplifies maintenance across the app’s components.

### Labcore/screens/
This module, `Labcore/screens/`, serves as the central navigation and screen management layer for the Labcore mobile application, organizing key user-facing interfaces for farming data workflows. It includes core components like `AddNewField`, `OfflineFields`, `UploadFieldData`, and `FieldView` that handle field creation, offline data management, data upload, and GIS visualization—integrating with Redux for state, AsyncStorage for auth and config, and React Navigation for routing. Other parts of the app use this module to import and register screens, leverage shared state and API logic, and maintain consistent navigation and data flow across features like grower management, field mapping, and service requests.

### Labcore/screens/AddFieldBoundary/
The `AddFieldBoundary` module enables users to draw field boundaries on a map by tracking their real-time location and recording coordinates to form a polygon. It consists of a main React Native component (`AddFieldBoundaryOnMap`) that integrates with `react-native-maps`, handles location permissions via `react-native-geolocation-service`, and uses Redux to manage user location state. The module also includes a `styles.js` file exporting reusable UI styles for layout, buttons, and a floating overlay panel. Other parts of the Labcore app use this module to capture field geometry during onboarding or field data collection, feeding the boundary data into downstream processing or storage.

### Labcore/store/
The Labcore/store module implements a centralized, offline-first state management system for a mobile agricultural data collection app using Redux, redux-offline, redux-saga, and redux-thunk. It manages field and farm data, map interactions, GPS tracking, and offline synchronization through reducers, action creators, and sagas that handle complex workflows like subfield layer sorting and data persistence. Other parts of the app dispatch standardized actions (e.g., `UPDATE_FARM_LIST`, `TOGGLE_MAP_SCREEN`) to update state predictably, while sagas orchestrate side effects like fetching and sorting layer data in response to user or network events. This module ensures consistent, reliable state across online and offline scenarios, forming the core data layer for field operations.

### Labcore/store/actions/
This module manages application state in a GIS-based agricultural mobile app using Redux, focusing on user location, field data, map interactions, and offline operations. It exports action type constants and pure action creators that standardize state updates for features like field boundary management, GPS tracking, and offline data synchronization. Other parts of the app use these actions to dispatch events to the store, enabling predictable state changes and seamless integration with offline persistence via redux-offline.

### Labcore/store/reducers/
This module manages application state for offline-capable features in the Labcore mobile SDK, primarily handling data related to fields, farms, calendars, and map interfaces. Key components include reducers for client info, farm lists, offline fields, sub-field layers, and UI visibility toggles (e.g., draw boundary, zoom screen), all integrated into the Redux store. Other parts of the app use these reducers by dispatching actions (e.g., `UPDATE_FARM_LIST`, `TOGGLE_MAP_SCREEN`) to update state predictably, enabling consistent UI behavior and offline data synchronization.

### Labcore/store/sagas/
This module manages asynchronous workflows for handling offline subfield layer data in the Labcore mobile application. It uses Redux Sagas to listen for actions like `GET_OFFLINE_SUBFIELD_LAYER_NAMES_SUCCESS` and `SORT_OFFLINE_SUBFIELD_LAYERS`, triggering operations such as sorting and fetching layer data via `sortLayers` and `getLayers`. The key components include `yield takeEvery` for action handling, `select` to read state, and `put` to dispatch new actions, ensuring efficient, side-effect-free data management. Other parts of the codebase use this module by dispatching relevant actions, allowing the saga to orchestrate complex layer data operations in response to user or system events.

### Labcore/utils/
This module manages environment-specific base URLs for Labcore's API and web services, enabling seamless configuration across different deployment stages. It exports constants like `TOPSOIL_URL` and `DEMO_URL` to define production, demo, and local endpoints, with a commented-out local URL for Android testing. Other modules import these constants to construct full API paths dynamically based on the current environment. This centralization simplifies environment management and ensures consistent endpoint usage throughout the application.
