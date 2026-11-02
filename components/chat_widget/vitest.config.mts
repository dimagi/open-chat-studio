import { defineVitestConfig } from '@stencil/vitest/config';
import { stencilVitestPlugin } from '@stencil/vitest/plugin';

export default defineVitestConfig({
  stencilConfig: './stencil.config.ts',
  plugins: [stencilVitestPlugin()],
  test: {
    name: 'spec',
    include: ['src/**/*.spec.{ts,tsx}'],
    environment: 'stencil',
    globals: true,
  },
});
