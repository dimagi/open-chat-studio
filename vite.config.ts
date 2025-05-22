import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [
    react(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './assets/javascript'),
    },
  },
  base: '/static/', // Should match Django's STATIC_URL
  build: {
    manifest: true, // The manifest.json file is needed for django-vite
    outDir: path.resolve(__dirname, './static'), // Output directory for production build
    emptyOutDir: false, // Preserve the outDir to not clobber Django's other files.
    rollupOptions: {
      input: {
        'site-base': path.resolve(__dirname, './assets/site-base.js'),
        'site-tailwind': path.resolve(__dirname, './assets/site-tailwind.js'),
        'site': path.resolve(__dirname, './assets/javascript/site.js'),
        'app': path.resolve(__dirname, './assets/javascript/app.js'),
        'pipeline': path.resolve(__dirname, './assets/javascript/apps/pipeline.tsx'),
        'adminDashboard': path.resolve(__dirname, './assets/javascript/admin-dashboard.js'),
        'tagMultiselect': path.resolve(__dirname, './assets/javascript/tag-multiselect.js'),
        'tokenCounter': path.resolve(__dirname, './assets/javascript/tiktoken.js'),
        'jsonEditor': path.resolve(__dirname, './assets/javascript/json-editor.js'),
      },
      output: {
        // Output JS bundles to js/ directory with -bundle suffix
        entryFileNames: `js/[name]-bundle.js`,
        // For shared chunks, keep hash for cache busting
        chunkFileNames: `js/[name]-[hash].js`,
        // For CSS and other assets
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith('.css')) {
            // Try to name CSS files like css/[entry_name].css, removing potential hash
            let baseName = path.basename(assetInfo.name, '.css');
            const hashPattern = /\.[0-9a-fA-F]{8}$/;
            baseName = baseName.replace(hashPattern, '');
            return `css/${baseName}.css`;
          }
          // Default for other assets (fonts, images, etc.)
          return `assets/[name]-[hash][extname]`;
        },
      },
    },
  },
  server: {
    port: 5173, // Default Vite dev server port, must match DJANGO_VITE settings
    strictPort: true,
    watch: {
      ignored: [
        path.resolve(__dirname, 'docs/**'),
        path.resolve(__dirname, 'components/**'),
      ],
    },
  },
});
