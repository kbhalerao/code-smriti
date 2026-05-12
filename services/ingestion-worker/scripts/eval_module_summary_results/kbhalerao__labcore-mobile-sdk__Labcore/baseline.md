# Baseline (stored): kbhalerao/labcore-mobile-sdk/Labcore

- file_count: None

## Output

The Labcore module is a React Native-based mobile SDK for agricultural data collection, designed to support offline-first workflows with centralized state management and modular navigation. Key components include Redux-based state management (via redux-offline, redux-saga, and redux-thunk), React Navigation for screen routing, reusable UI components (like Mapbox-powered `ShapeSource` and `MultiSelect2`), and shared configuration (theme, constants, icons). Other parts of the app import screens from `screens/`, state and actions from `store/`, UI components from `components/`, and configuration from `constants/`, enabling consistent, scalable, and maintainable development across features like field management, data upload, and user authentication.
