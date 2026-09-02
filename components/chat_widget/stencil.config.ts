import { Config } from '@stencil/core';
import path from 'path';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const SRC_DIR = path.resolve(__dirname, 'src');
const COMPONENTS_DIR = path.join(SRC_DIR, 'components');
const TAILWIND_ENTRY = path.join(SRC_DIR, 'tailwind.css');
// CSS import paths take forward slashes on every platform; a Windows path would read as escapes.
const TAILWIND_IMPORT = TAILWIND_ENTRY.split(path.sep).join('/');

/**
 * Runs Tailwind over each component stylesheet so `@apply` resolves and the utilities used in
 * the component's markup are emitted inside its shadow root.
 *
 * Stencil resolves `@import` statements itself before any css plugin runs and cannot read the
 * bare `tailwindcss` package import, so the Tailwind entry is prepended here, in memory, where
 * only Tailwind sees it.
 */
function tailwindComponentStyles() {
  return {
    pluginType: 'css',
    name: 'tailwind-component-styles',
    async transform(code: string, id: string, context: { config: { devMode?: boolean } }) {
      const file = id.split('?')[0];
      if (!file.endsWith('.css') || file.includes('node_modules')) {
        return { code, map: null };
      }
      const tailwind = tailwindcss({
        base: COMPONENTS_DIR,
        optimize: context.config.devMode ? false : { minify: true },
      });
      const result = await postcss([tailwind]).process(`@import "${TAILWIND_IMPORT}";\n${code}`, {
        from: file,
        map: false,
      });
      // Tailwind's parser drops the `/** @prop */` comments that feed the readme's CSS custom
      // properties table, and Stencil reads them from this output. Put them back; Stencil strips
      // comments again before anything reaches the bundle.
      const docComments = code.match(/\/\*\*(?!\/)[\s\S]*?\*\//g) ?? [];
      const dependencies = result.messages.filter((m) => m.type === 'dependency').map((m) => m.file);
      return { code: `${docComments.join('\n')}\n${result.css}`, map: null, dependencies: [TAILWIND_ENTRY, ...dependencies] };
    },
  };
}

export const config: Config = {
  namespace: 'open-chat-studio-widget',
  env: {
    version: process.env.npm_package_version
  },
  outputTargets: [
    {
      type: 'dist',
      esmLoaderPath: '../loader',
    },
    {
      type: 'dist-custom-elements',
    },
    {
      type: 'docs-readme',
    },
    {
      type: 'www',
      serviceWorker: null, // disable service workers
    },
  ],
  testing: {
    browserHeadless: "new",
    moduleNameMapper: {
      // marked v18 is published as ESM only (its `exports` map resolves to lib/marked.esm.js)
      // and Stencil's Jest preprocessor only transforms .ts/.tsx/.jsx/.css/.mjs, so Jest's
      // CommonJS runtime chokes on `export {...}` when a spec imports src/utils/markdown.ts.
      // Point spec tests at marked's UMD build: same source, same esbuild config, but wrapped
      // for CommonJS. Production/dist builds are unaffected and still use the ESM entry point.
      '^marked$': '<rootDir>/node_modules/marked/lib/marked.umd.js',
    },
  },
  devServer: {
    reloadStrategy: 'pageReload',
    openBrowser: false,
  },
  plugins : [
    tailwindComponentStyles(),
  ],
};
