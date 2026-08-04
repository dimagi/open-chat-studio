import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {
    // Scoped to the Django app's own JS. The chat widget under components/chat_widget is a
    // standalone StencilJS package with its own Jest-based runner, so its specs are excluded.
    include: ['assets/javascript/**/*.test.js'],
    environment: 'node',
  },
});
