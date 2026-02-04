import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import globals from 'globals';

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
      }
    },
  },
  // Node.js scripts
  {
    files: ["scripts/**/*.js"],
    languageOptions: {
      globals: {
        ...globals.node,
      }
    },
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
);
