import { Config } from '@stencil/core';
import tailwind, { setPluginConfigurationDefaults } from 'stencil-tailwind-plugin';

setPluginConfigurationDefaults({
  tailwindCssPath: './src/tailwind.css',
});

export const config: Config = {
  namespace: 'open-chat-studio-widget',
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
  },
  devServer: {
    reloadStrategy: 'pageReload',
    openBrowser: false,
  },
  plugins : [
    tailwind(),
  ],
};
