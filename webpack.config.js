const {sentryWebpackPlugin} = require("@sentry/webpack-plugin");
const path = require('path');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const TerserPlugin = require('terser-webpack-plugin');

// Legacy config for traditional bundles (UMD with SiteJS global)
// These will be migrated to ES modules over time
const config = {
  entry: {
    'site-base': './assets/site-base.js',  // base styles shared between frameworks
    'site-tailwind': './assets/site-tailwind.js',  // required for tailwindcss styles
    site: './assets/javascript/site.js',  // global site javascript
    app: './assets/javascript/app.js',  // logged-in javascript
    'pipeline': './assets/javascript/apps/pipeline.tsx',
    adminDashboard: './assets/javascript/admin-dashboard.js',
    trends: './assets/javascript/trends.js',
    dashboard: './assets/javascript/dashboard.js',  // dashboard analytics
    'tagMultiselect': './assets/javascript/tag-multiselect.js',
    'tokenCounter': './assets/javascript/tiktoken.js',
    'editors': './assets/javascript/editors.js',
    'evaluations': './assets/javascript/apps/evaluations/dataset-mode-selector.js',
    'evaluationTrends': './assets/javascript/apps/evaluations/trend-charts.js',
    'human_annotations': './assets/javascript/apps/human_annotations/session-selector.js',
  },

  output: {
    path: path.resolve(__dirname, './static'),
    filename: 'js/[name]-bundle.js',
    library: ["SiteJS", "[name]"],
  },

  resolve: {
    extensions: ['.js', '.jsx', '.ts', '.tsx'],
  },

  module: {
    rules: [
      {
        test: /\.(js|jsx|ts|tsx)$/,
        exclude: /node_modules/,
        loader: "babel-loader",
      },
      {
        test: /\.css$/i,
        use: [
          MiniCssExtractPlugin.loader,
          'css-loader',
          'postcss-loader'
        ],
      },
    ],
  },

  plugins: [
    new MiniCssExtractPlugin({
      'filename': 'css/[name].css',
    })
  ],

  optimization: {
    minimizer: [new TerserPlugin({
      extractComments: false,  // disable generation of license.txt files
    })],
  },

  devtool: "source-map"
};

// ES modules config - all new JavaScript should be added here
// Add new modules to assets/javascript/modules/ and register them in the entry below
const modulesConfig = {
  entry: {
    'chat-widget-context': './assets/javascript/modules/chat-widget-context.js',
  },

  experiments: {
    outputModule: true,
  },

  output: {
    path: path.resolve(__dirname, './static/js/modules'),
    filename: '[name].js',
    library: {
      type: 'module',
    },
  },

  resolve: {
    extensions: ['.js', '.ts'],
  },

  module: {
    rules: [
      {
        test: /\.(js|ts)$/,
        exclude: /node_modules/,
        loader: "babel-loader",
      },
    ],
  },

  optimization: {
    minimizer: [new TerserPlugin({
      extractComments: false,
    })],
  },

  devtool: "source-map"
};

module.exports = (env, argv) => {
  if (argv.mode === 'production' && process.env.GITHUB_REF === 'refs/heads/main') {
    // Uploads source maps to Sentry
    // These env variables must be set in the environment when running 'npm run build'
    // for the source maps to be uploaded.
    const sentryPlugin = sentryWebpackPlugin({
      authToken: process.env.SENTRY_AUTH_TOKEN,
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      telemetry: false,
    });
    config.plugins = config.plugins.concat([sentryPlugin]);
    modulesConfig.plugins = [sentryPlugin];
  }
  return [config, modulesConfig];
}
