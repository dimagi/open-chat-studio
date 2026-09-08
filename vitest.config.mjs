import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {
    // Scoped to the Django app's own JS. The chat widget under components/chat_widget is a
    // standalone StencilJS package with its own Jest-based runner, so its specs are excluded.
    include: ['assets/javascript/**/*.test.js', 'assets/javascript/**/*.test.tsx'],
    // jsdom is a superset of what the existing plain-function .test.js specs need
    // (they don't rely on anything jsdom would remove), and React component specs
    // need the DOM it provides, so run everything on it rather than split environments.
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
  },
});
