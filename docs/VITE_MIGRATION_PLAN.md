# Webpack to Vite Migration Plan

This document provides a comprehensive plan for migrating Open Chat Studio's frontend build system from Webpack 5 to Vite 6.

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture Analysis](#current-architecture-analysis)
3. [Migration Strategy](#migration-strategy)
4. [Phase 1: Foundation Setup](#phase-1-foundation-setup)
5. [Phase 2: Configuration Migration](#phase-2-configuration-migration)
6. [Phase 3: Entry Point Migration](#phase-3-entry-point-migration)
7. [Phase 4: Template Integration](#phase-4-template-integration)
8. [Phase 5: Development Workflow](#phase-5-development-workflow)
9. [Phase 6: Production Build & Sentry](#phase-6-production-build--sentry)
10. [Phase 7: Testing & Validation](#phase-7-testing--validation)
11. [Phase 8: Cleanup & Documentation](#phase-8-cleanup--documentation)
12. [Risk Assessment & Rollback Plan](#risk-assessment--rollback-plan)

---

## Executive Summary

### Why Migrate to Vite?

| Aspect | Webpack (Current) | Vite (Target) |
|--------|------------------|---------------|
| **Dev Server Startup** | ~5-15s cold start | <500ms (ESM-based) |
| **HMR Speed** | 1-3s | <50ms |
| **Build Speed** | Moderate | 10-100x faster (Rollup) |
| **Configuration** | Complex, verbose | Minimal, sensible defaults |
| **Native ESM** | Requires bundling | Native browser ESM in dev |
| **TypeScript** | Requires babel-loader | Native esbuild transpilation |
| **React Fast Refresh** | Requires plugins | Built-in support |
| **CSS/PostCSS** | Requires loaders | Built-in support |

### Scope of Migration

- **11 entry points** to migrate
- **Global namespace** (`SiteJS.*`) pattern must be preserved
- **Django integration** via static files (no django-webpack-loader)
- **CSS processing** with TailwindCSS v4 and PostCSS
- **Sentry source maps** for production builds
- **React 19** with ReactFlow for pipeline builder

---

## Current Architecture Analysis

### Entry Points (webpack.config.js)

```javascript
{
  'site-base': './assets/site-base.js',           // CSS-only: base styles
  'site-tailwind': './assets/site-tailwind.js',   // CSS-only: Tailwind styles
  'site': './assets/javascript/site.js',          // Global JS: Alpine, HTMX, etc.
  'app': './assets/javascript/app.js',            // Logged-in app JS
  'pipeline': './assets/javascript/apps/pipeline.tsx',  // React: Pipeline builder
  'adminDashboard': './assets/javascript/admin-dashboard.js',
  'trends': './assets/javascript/trends.js',
  'dashboard': './assets/javascript/dashboard.js',
  'tagMultiselect': './assets/javascript/tag-multiselect.js',
  'tokenCounter': './assets/javascript/tiktoken.js',
  'editors': './assets/javascript/editors.js',
  'evaluations': './assets/javascript/apps/evaluations/dataset-mode-selector.js',
  'evaluationTrends': './assets/javascript/apps/evaluations/trend-charts.js',
}
```

### Output Structure (Must Be Preserved)

```
static/
├── js/
│   ├── site-bundle.js
│   ├── app-bundle.js
│   ├── pipeline-bundle.js
│   └── [name]-bundle.js
└── css/
    ├── site-base.css
    ├── site-tailwind.css
    └── [name].css
```

### Global Namespace Pattern (Critical)

The current webpack config exposes bundles via:
```javascript
library: ["SiteJS", "[name]"]
```

This creates `window.SiteJS.app`, `window.SiteJS.pipeline`, etc.

**Templates depend on this pattern extensively:**
- `SiteJS.app.Cookies.get('csrftoken')`
- `SiteJS.app.copyToClipboard(...)`
- `SiteJS.pipeline.renderPipeline(...)`
- `SiteJS.editors.initJsonEditors()`
- `SiteJS.tokenCounter.countGPTTokens(...)`
- `SiteJS.trends.trendsChart(...)`
- `SiteJS.tagMultiselect.setupTagSelects()`
- `SiteJS.adminDashboard.barChartWithDates(...)`
- `SiteJS.evaluationTrends.renderTrendCharts(...)`

### Django Integration

Templates use Django's `{% static %}` tag:
```html
<link rel="stylesheet" href="{% static 'css/site-base.css' %}">
<script src="{% static 'js/site-bundle.js' %}"></script>
```

No django-webpack-loader or manifest files are used.

---

## Migration Strategy

### Approach: Parallel Build System

1. Install Vite alongside Webpack
2. Create Vite configuration matching Webpack output
3. Test Vite builds in isolation
4. Validate all functionality
5. Remove Webpack configuration

### Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Library Mode** | Use Vite's `build.lib` for each entry | Preserves `SiteJS.*` namespace |
| **Multiple Configs** | Single config with Rollup `input` object | Simpler than multiple builds |
| **CSS Extraction** | Vite's built-in CSS handling | No additional plugins needed |
| **React Plugin** | @vitejs/plugin-react | Fast Refresh, JSX transform |
| **TypeScript** | Native esbuild (Vite default) | Faster than Babel |
| **Dev Server** | Optional proxy to Django | Enhanced DX with HMR |

---

## Phase 1: Foundation Setup

### 1.1 Install Vite and Core Dependencies

```bash
npm install --save-dev vite @vitejs/plugin-react
npm install --save-dev vite-plugin-sentry  # For Sentry source maps
```

### 1.2 Update package.json Scripts

```json
{
  "scripts": {
    "dev": "vite build --mode development",
    "dev:watch": "vite build --mode development --watch",
    "dev:server": "vite",
    "build": "vite build --mode production",
    "preview": "vite preview",
    "type-check": "tsc --noEmit",
    "type-check:watch": "npm run type-check -- --watch",
    "lint": "eslint --fix assets/javascript",

    "// Legacy webpack commands (remove after migration)": "",
    "webpack:dev": "webpack --mode development",
    "webpack:build": "NODE_ENV=production webpack --mode production"
  }
}
```

### 1.3 Remove Unnecessary Dependencies

After successful migration, remove these packages:

```bash
npm uninstall webpack webpack-cli babel-loader @babel/core @babel/cli \
  @babel/preset-env @babel/preset-react @babel/preset-typescript \
  @babel/compat-data mini-css-extract-plugin css-loader postcss-loader \
  style-loader terser-webpack-plugin @sentry/webpack-plugin
```

### 1.4 Keep These Dependencies

- `postcss` - Still used by Vite
- `autoprefixer` - Still used
- `@tailwindcss/postcss` - TailwindCSS v4 PostCSS plugin
- All runtime dependencies (React, etc.)

---

## Phase 2: Configuration Migration

### 2.1 Create vite.config.ts

```typescript
// vite.config.ts
import { defineConfig, UserConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Entry points mapping (name -> path)
const entries = {
  'site-base': './assets/site-base.js',
  'site-tailwind': './assets/site-tailwind.js',
  'site': './assets/javascript/site.js',
  'app': './assets/javascript/app.js',
  'pipeline': './assets/javascript/apps/pipeline.tsx',
  'adminDashboard': './assets/javascript/admin-dashboard.js',
  'trends': './assets/javascript/trends.js',
  'dashboard': './assets/javascript/dashboard.js',
  'tagMultiselect': './assets/javascript/tag-multiselect.js',
  'tokenCounter': './assets/javascript/tiktoken.js',
  'editors': './assets/javascript/editors.js',
  'evaluations': './assets/javascript/apps/evaluations/dataset-mode-selector.js',
  'evaluationTrends': './assets/javascript/apps/evaluations/trend-charts.js',
};

export default defineConfig(({ mode }) => {
  const isDev = mode === 'development';
  const isProd = mode === 'production';
  const isMainBranch = process.env.GITHUB_REF === 'refs/heads/main';

  const config: UserConfig = {
    plugins: [
      react({
        // Use classic runtime for compatibility
        jsxRuntime: 'automatic',
      }),
    ],

    // Resolve extensions and aliases
    resolve: {
      extensions: ['.js', '.jsx', '.ts', '.tsx', '.json'],
      alias: {
        '@': path.resolve(__dirname, './assets'),
      },
    },

    // CSS configuration (PostCSS is auto-detected from postcss.config.js)
    css: {
      devSourcemap: true,
    },

    // Build configuration
    build: {
      outDir: 'static',
      emptyOutDir: false, // Don't delete static/images
      sourcemap: true,
      minify: isProd ? 'esbuild' : false,

      // Rollup options for multi-entry build
      rollupOptions: {
        input: entries,
        output: {
          // JS output format matching webpack
          entryFileNames: 'js/[name]-bundle.js',
          chunkFileNames: 'js/chunks/[name]-[hash].js',
          assetFileNames: (assetInfo) => {
            // CSS files go to css/ directory
            if (assetInfo.name?.endsWith('.css')) {
              return 'css/[name][extname]';
            }
            return 'assets/[name]-[hash][extname]';
          },

          // CRITICAL: Expose exports on window.SiteJS namespace
          // This replicates webpack's library: ["SiteJS", "[name]"]
          format: 'iife',
          extend: true,
          name: 'SiteJS',
          globals: {},
        },

        // Preserve exports for global access
        preserveEntrySignatures: 'exports-only',
      },
    },

    // Dev server configuration (optional, for HMR during development)
    server: {
      port: 3000,
      proxy: {
        // Proxy API requests to Django
        '/api': 'http://localhost:8000',
        '/accounts': 'http://localhost:8000',
        '/admin': 'http://localhost:8000',
      },
    },
  };

  // Add Sentry plugin for production builds on main branch
  if (isProd && isMainBranch && process.env.SENTRY_AUTH_TOKEN) {
    // Dynamic import to avoid issues when Sentry env vars aren't set
    const { sentryVitePlugin } = require('@sentry/vite-plugin');
    config.plugins!.push(
      sentryVitePlugin({
        authToken: process.env.SENTRY_AUTH_TOKEN,
        org: process.env.SENTRY_ORG,
        project: process.env.SENTRY_PROJECT,
        telemetry: false,
      })
    );
  }

  return config;
});
```

### 2.2 Alternative: Multiple Library Builds

If the single IIFE build doesn't properly expose each entry's exports, use this approach:

```typescript
// vite.config.ts - Library mode for each entry
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Build configuration for a single entry
function createLibraryConfig(name: string, entry: string) {
  return defineConfig({
    plugins: [react()],
    build: {
      outDir: 'static',
      emptyOutDir: false,
      sourcemap: true,
      lib: {
        entry: path.resolve(__dirname, entry),
        name: `SiteJS.${name}`,
        fileName: () => `js/${name}-bundle.js`,
        formats: ['iife'],
      },
      rollupOptions: {
        output: {
          extend: true,
          assetFileNames: `css/${name}[extname]`,
        },
      },
    },
  });
}

// Export configuration based on entry
export default defineConfig(({ mode }) => {
  const entry = process.env.VITE_ENTRY;
  if (entry) {
    const entries = { /* ... entry map ... */ };
    return createLibraryConfig(entry, entries[entry]);
  }
  // Default: build all (handled by build script)
  return {};
});
```

With a build script:
```bash
#!/bin/bash
# scripts/build-all.sh
entries=("site-base" "site-tailwind" "site" "app" "pipeline" ...)
for entry in "${entries[@]}"; do
  VITE_ENTRY=$entry npx vite build --mode production
done
```

### 2.3 Custom Vite Plugin for SiteJS Namespace

For precise control over the global namespace, create a custom plugin:

```typescript
// vite-plugin-sitejs.ts
import { Plugin } from 'vite';

export function siteJSPlugin(): Plugin {
  return {
    name: 'sitejs-namespace',
    generateBundle(options, bundle) {
      for (const [fileName, chunk] of Object.entries(bundle)) {
        if (chunk.type === 'chunk' && chunk.isEntry) {
          // Extract entry name from file path
          const name = fileName
            .replace('js/', '')
            .replace('-bundle.js', '');

          // Wrap the chunk code to expose on SiteJS namespace
          const wrappedCode = `
(function() {
  window.SiteJS = window.SiteJS || {};
  var exports = {};
  ${chunk.code}
  window.SiteJS['${name}'] = exports;
})();
`;
          chunk.code = wrappedCode;
        }
      }
    },
  };
}
```

---

## Phase 3: Entry Point Migration

### 3.1 Update Entry Point Files for ESM

Each entry point needs to explicitly export functions for global access.

**Before (app.js):**
```javascript
import * as JsCookie from "js-cookie";
export const Cookies = JsCookie.default;

export async function copyToClipboard(callee, elementId) {
  // ...
}
```

**After (app.js) - No changes needed!**

The exports are already correct. Vite will handle exposing them to `window.SiteJS.app`.

### 3.2 CSS-Only Entry Points

For CSS-only entry points (`site-base`, `site-tailwind`), Vite handles these automatically:

```javascript
// assets/site-base.js
import './styles/site-base.css';

// assets/site-tailwind.js
import './styles/site-tailwind.css';
```

Vite will extract CSS to `static/css/site-base.css` and `static/css/site-tailwind.css`.

### 3.3 React Entry Points

The pipeline React entry already works with Vite:

```tsx
// assets/javascript/apps/pipeline.tsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./pipeline/App";

export function renderPipeline(containerId: string, team_slug: string, pipelineId: number | undefined) {
  const root = document.querySelector(containerId)!;
  createRoot(root).render(<App team_slug={team_slug} pipelineId={pipelineId} />);
}
```

Vite's React plugin provides:
- Automatic JSX transform
- Fast Refresh in dev mode
- Efficient production builds

---

## Phase 4: Template Integration

### 4.1 No Template Changes Required

Since templates use Django's `{% static %}` tag and the output paths match:

```html
<!-- These work unchanged -->
<link rel="stylesheet" href="{% static 'css/site-base.css' %}">
<link rel="stylesheet" href="{% static 'css/site-tailwind.css' %}">
<script src="{% static 'js/site-bundle.js' %}"></script>
<script src="{% static 'js/app-bundle.js' %}"></script>
```

### 4.2 SiteJS Namespace Verification

All existing template code should continue working:

```javascript
// These patterns must continue to work
SiteJS.app.Cookies.get('csrftoken')
SiteJS.app.copyToClipboard(this, 'element-id')
SiteJS.pipeline.renderPipeline("#pipelineBuilder", "team-slug", 123)
SiteJS.editors.initJsonEditors()
```

### 4.3 Testing Checklist for Templates

After migration, verify each template bundle works:

| Template | Bundle | Functions Used |
|----------|--------|---------------|
| `base.html` | `site-bundle.js` | Alpine, HTMX, TomSelect |
| `app_base.html` | `app-bundle.js` | Cookies, copyToClipboard |
| `pipeline_builder.html` | `pipeline-bundle.js` | renderPipeline |
| `experiment_session_view.html` | `tagMultiselect-bundle.js` | setupTagSelects |
| `prompt_builder.html` | `tokenCounter-bundle.js` | countGPTTokens |
| `evaluator_form.html` | `editors-bundle.js` | initJsonEditors, initPythonEditors |
| `dashboard/index.html` | `dashboard-bundle.js` | Chart initialization |
| `admin/home.html` | `adminDashboard-bundle.js` | barChartWithDates |
| `chatbots/home.html` | `trends-bundle.js` | trendsChart |
| `evaluation_runs_home.html` | `evaluationTrends-bundle.js` | renderTrendCharts |

---

## Phase 5: Development Workflow

### 5.1 Development Commands

```bash
# One-time build
npm run dev

# Watch mode (rebuild on changes)
npm run dev:watch

# Vite dev server with HMR (optional, for React development)
npm run dev:server
```

### 5.2 Update Invoke Tasks

```python
# tasks.py
@task(
    help={
        "watch": "Build assets and watch for changes",
        "server": "Start Vite dev server with HMR",
        "install": "Install npm packages before building",
    }
)
def npm(c: Context, watch=False, server=False, install=False):
    """Build frontend assets with Vite. Use --watch for development."""
    if install:
        c.run("npm install", echo=True)

    if server:
        c.run("npm run dev:server", echo=True, pty=True)
    elif watch:
        c.run("npm run dev:watch", echo=True, pty=True)
    else:
        c.run("npm run dev", echo=True, pty=True)
```

### 5.3 Vite Dev Server Integration (Optional)

For enhanced development with Hot Module Replacement:

1. Run Vite dev server: `npm run dev:server`
2. Configure Django to proxy to Vite in development
3. Assets served directly by Vite with HMR

```python
# settings.py (development only)
if DEBUG:
    VITE_DEV_SERVER = 'http://localhost:3000'
```

```html
<!-- Template modification for dev server mode -->
{% if settings.DEBUG and settings.VITE_DEV_SERVER %}
  <script type="module" src="{{ settings.VITE_DEV_SERVER }}/@vite/client"></script>
  <script type="module" src="{{ settings.VITE_DEV_SERVER }}/assets/javascript/site.js"></script>
{% else %}
  <script src="{% static 'js/site-bundle.js' %}"></script>
{% endif %}
```

**Note:** This is optional. The simpler approach is to use `npm run dev:watch` and let Vite rebuild to static files, which Django serves normally.

---

## Phase 6: Production Build & Sentry

### 6.1 Production Build Command

```bash
npm run build
# or
NODE_ENV=production npx vite build --mode production
```

### 6.2 Sentry Source Maps

Install the Sentry Vite plugin:

```bash
npm install --save-dev @sentry/vite-plugin
```

The plugin is conditionally added in `vite.config.ts` when:
- Mode is production
- `GITHUB_REF === 'refs/heads/main'`
- `SENTRY_AUTH_TOKEN` is set

### 6.3 GitHub Actions Integration

No changes needed to CI/CD. The environment variables are already set:
- `SENTRY_AUTH_TOKEN`
- `SENTRY_ORG`
- `SENTRY_PROJECT`

Update the build command in workflows:

```yaml
# .github/workflows/build.yml
- name: Build frontend
  run: npm run build
  env:
    SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}
    SENTRY_ORG: ${{ secrets.SENTRY_ORG }}
    SENTRY_PROJECT: ${{ secrets.SENTRY_PROJECT }}
```

---

## Phase 7: Testing & Validation

### 7.1 Build Output Validation

Create a validation script:

```bash
#!/bin/bash
# scripts/validate-build.sh

echo "Validating Vite build output..."

# Check JS bundles exist
bundles=("site" "app" "pipeline" "adminDashboard" "trends" "dashboard"
         "tagMultiselect" "tokenCounter" "editors" "evaluations" "evaluationTrends")
for bundle in "${bundles[@]}"; do
  if [ ! -f "static/js/${bundle}-bundle.js" ]; then
    echo "ERROR: Missing static/js/${bundle}-bundle.js"
    exit 1
  fi
done

# Check CSS files exist
css_files=("site-base" "site-tailwind" "pipeline")
for css in "${css_files[@]}"; do
  if [ ! -f "static/css/${css}.css" ]; then
    echo "ERROR: Missing static/css/${css}.css"
    exit 1
  fi
done

# Check source maps exist
if [ ! -f "static/js/site-bundle.js.map" ]; then
  echo "WARNING: Missing source maps"
fi

echo "Build validation passed!"
```

### 7.2 Functional Testing

1. **Run Django development server:**
   ```bash
   npm run dev && inv runserver
   ```

2. **Test each page:**
   - [ ] Homepage - Site JS loads, HTMX works
   - [ ] Login/Dashboard - App JS loads, Cookies work
   - [ ] Pipeline builder - React renders, interactions work
   - [ ] Experiment session - Tag multiselect works
   - [ ] Prompt builder - Token counter works
   - [ ] Evaluator form - Code editors initialize
   - [ ] Admin dashboard - Charts render
   - [ ] Evaluation runs - Trend charts work

3. **Browser console checks:**
   - No JavaScript errors
   - `window.SiteJS` object exists with all properties
   - All functions callable

### 7.3 Cypress E2E Tests

Run existing Cypress tests to validate functionality:

```bash
npm run cypress:run
```

---

## Phase 8: Cleanup & Documentation

### 8.1 Remove Webpack Files

After successful validation:

```bash
rm webpack.config.js
rm .babelrc
```

### 8.2 Update package.json

Remove old scripts and dependencies:

```json
{
  "scripts": {
    "dev": "vite build --mode development",
    "dev:watch": "vite build --mode development --watch",
    "build": "vite build --mode production",
    "preview": "vite preview",
    "type-check": "tsc --noEmit",
    "lint": "eslint --fix assets/javascript"
  }
}
```

### 8.3 Update Documentation

Update `CLAUDE.md` / `AGENTS.md`:

```markdown
### Frontend (Node.js/Vite)
```bash
# Development builds
npm run dev                    # Build assets once
npm run dev:watch             # Build and watch for changes

# Production build
npm run build                 # Optimized production build

# Code quality
npm run lint                  # ESLint check and fix
npm run type-check            # TypeScript type checking
```
```

### 8.4 Update .gitignore

No changes needed - `static/js/` and `static/css/` are already ignored.

---

## Risk Assessment & Rollback Plan

### High-Risk Areas

1. **SiteJS Namespace Exposure**
   - Risk: Exports not properly exposed to `window.SiteJS`
   - Mitigation: Custom Vite plugin, thorough testing
   - Rollback: Revert to webpack, `git checkout webpack.config.js`

2. **CSS Output Paths**
   - Risk: CSS files in wrong location or missing
   - Mitigation: Validate `assetFileNames` configuration
   - Rollback: Adjust rollupOptions.output.assetFileNames

3. **React/TypeScript Transpilation**
   - Risk: JSX or TypeScript not properly transpiled
   - Mitigation: @vitejs/plugin-react, test pipeline builder
   - Rollback: Keep babel config temporarily

4. **Source Maps for Sentry**
   - Risk: Source maps not uploaded or incorrect
   - Mitigation: Test Sentry plugin in staging
   - Rollback: Manual source map upload

### Rollback Plan

1. Keep webpack.config.js in repository until migration is verified
2. Maintain legacy npm scripts during transition:
   ```json
   "webpack:dev": "webpack --mode development",
   "webpack:build": "webpack --mode production"
   ```
3. Tag the last webpack commit: `git tag pre-vite-migration`
4. If issues arise: `git revert` the Vite changes

---

## Implementation Checklist

### Phase 1: Foundation
- [ ] Install Vite and plugins
- [ ] Create `vite.config.ts`
- [ ] Update `package.json` scripts
- [ ] Verify PostCSS config works with Vite

### Phase 2: Configuration
- [ ] Configure multi-entry build
- [ ] Set up SiteJS namespace exposure
- [ ] Configure CSS extraction
- [ ] Add source map generation

### Phase 3: Entry Points
- [ ] Verify all entry points export correctly
- [ ] Test CSS-only entry points
- [ ] Test React entry point (pipeline)
- [ ] Verify TypeScript transpilation

### Phase 4: Templates
- [ ] Build assets with Vite
- [ ] Test site-bundle.js loads
- [ ] Test app-bundle.js functions
- [ ] Test pipeline-bundle.js React app
- [ ] Test all other bundles

### Phase 5: Development
- [ ] Test `npm run dev` command
- [ ] Test `npm run dev:watch` command
- [ ] Update invoke tasks
- [ ] Document new workflow

### Phase 6: Production
- [ ] Test production build
- [ ] Verify minification works
- [ ] Test Sentry source maps
- [ ] Verify in staging environment

### Phase 7: Testing
- [ ] Run build validation script
- [ ] Manual testing of all pages
- [ ] Run Cypress E2E tests
- [ ] Browser console verification

### Phase 8: Cleanup
- [ ] Remove webpack.config.js
- [ ] Remove .babelrc
- [ ] Remove unused npm packages
- [ ] Update documentation
- [ ] Update CI/CD pipelines

---

## Appendix: Quick Reference

### Vite vs Webpack Concepts

| Webpack | Vite |
|---------|------|
| `entry` | `build.rollupOptions.input` |
| `output.path` | `build.outDir` |
| `output.filename` | `build.rollupOptions.output.entryFileNames` |
| `output.library` | `build.rollupOptions.output.name` + custom plugin |
| `module.rules` (loaders) | `plugins` + native support |
| `babel-loader` | Built-in esbuild |
| `css-loader` + `postcss-loader` | Built-in PostCSS |
| `MiniCssExtractPlugin` | Built-in CSS extraction |
| `TerserPlugin` | Built-in esbuild minification |
| `devtool: "source-map"` | `build.sourcemap: true` |

### File Structure After Migration

```
open-chat-studio/
├── vite.config.ts              # NEW: Vite configuration
├── postcss.config.js           # UNCHANGED: PostCSS config
├── tailwind.config.js          # UNCHANGED: Tailwind config
├── tsconfig.json               # UNCHANGED: TypeScript config
├── eslint.config.mjs           # UNCHANGED: ESLint config
├── package.json                # UPDATED: New scripts
├── assets/                     # UNCHANGED: Source files
│   ├── javascript/
│   └── styles/
└── static/                     # OUTPUT: Built files
    ├── js/
    │   ├── site-bundle.js
    │   └── ...
    └── css/
        ├── site-base.css
        └── ...
```

---

## Conclusion

This migration plan provides a comprehensive path from Webpack to Vite while maintaining full compatibility with the existing Django integration and template patterns. The key challenges are:

1. Preserving the `SiteJS.*` global namespace
2. Maintaining identical output file paths
3. Ensuring React and TypeScript work correctly
4. Integrating Sentry source maps

By following this phased approach with proper testing at each stage, the migration can be completed safely with minimal risk to production functionality.
