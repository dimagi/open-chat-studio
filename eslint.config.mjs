import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-non-null-asserted-optional-chain": "off",
      "@typescript-eslint/no-unused-vars": ["error", { "caughtErrors": "none" }]
    },
    "languageOptions": {
      "globals": {
        ...globals.browser,
        htmx: "readonly",
      },
      parserOptions: {
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    // The pipeline app is the only React code in this repo (see AGENTS.md). Only the
    // two well-established hooks rules are enabled here, not the plugin's full v7
    // "recommended-latest" preset -- that preset adds a much larger, stricter set of
    // React-Compiler-oriented rules (purity, immutability, set-state-in-render, etc.)
    // that haven't been evaluated against this codebase.
    files: ["assets/javascript/apps/pipeline/**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
);
