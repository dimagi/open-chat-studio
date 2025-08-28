import { Config } from '@stencil/core';
import { postcss } from '@stencil-community/postcss';
import autoprefixer from 'autoprefixer';
import tailwind from '@tailwindcss/postcss'

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
    postcss({
      plugins: [
        require("postcss-import"),
        tailwind(),
        autoprefixer()
      ]
    })
  ],
};
