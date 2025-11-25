import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { sentryVitePlugin } from "@sentry/vite-plugin";
import path from 'path';

export default defineConfig({
  base: '/static/',

  plugins: [
    react(),
    process.env.GITHUB_REF === 'refs/heads/main' && sentryVitePlugin({
      authToken: process.env.SENTRY_AUTH_TOKEN,
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      telemetry: false,
    }),
  ].filter(Boolean),

  build: {
    outDir: path.resolve(__dirname, './static'),
    emptyOutDir: false,  // Don't delete other Django static files
    manifest: "manifest.json",
    sourcemap: true,  // For Sentry

    rollupOptions: {
      input: {
        'site-base': path.resolve(__dirname, './assets/site-base.js'),
        'site-tailwind': path.resolve(__dirname, './assets/site-tailwind.js'),
        'site': path.resolve(__dirname, './assets/javascript/site.js'),
        'app': path.resolve(__dirname, './assets/javascript/app.js'),
        'pipeline': path.resolve(__dirname, './assets/javascript/apps/pipeline.tsx'),
        'adminDashboard': path.resolve(__dirname, './assets/javascript/admin-dashboard.js'),
        'trends': path.resolve(__dirname, './assets/javascript/trends.js'),
        'dashboard': path.resolve(__dirname, './assets/javascript/dashboard.js'),
        'tagMultiselect': path.resolve(__dirname, './assets/javascript/tag-multiselect.js'),
        'tokenCounter': path.resolve(__dirname, './assets/javascript/tiktoken.js'),
        'editors': path.resolve(__dirname, './assets/javascript/editors.js'),
        'evaluations': path.resolve(__dirname, './assets/javascript/apps/evaluations/dataset-mode-selector.js'),
      },

      output: {
        entryFileNames: 'js/[name]-bundle.js',
        chunkFileNames: 'js/[name]-[hash].js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name.endsWith('.css')) {
            return 'css/[name].css';
          }
          return 'assets/[name]-[hash][extname]';
        },
      },
    },
  },

  server: {
    port: 5173,
    host: 'localhost',
    strictPort: true,
    origin: 'http://localhost:5173',
  },

  // CSS handling
  css: {
    postcss: './postcss.config.js',
  },
});
