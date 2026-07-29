import { Config } from '@stencil/core';
import tailwind, { setPluginConfigurationDefaults } from 'stencil-tailwind-plugin';

setPluginConfigurationDefaults({
  tailwindCssPath: './src/tailwind.css',
});

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
    tailwind(),
  ],
};
