# Cypress E2E Testing

This directory contains end-to-end tests for Open Chat Studio using Cypress.

## Setup

1. **Install dependencies** (if not already done):
   ```bash
   npm install
   ```

2. **Configure environment variables**:
   Edit `cypress.env.json` with your test credentials and team slug:
   ```json
   {
     "TEST_USER": "your-test-user@example.com",
     "TEST_PASSWORD": "your-test-password",
     "TEAM_SLUG": "your-team-slug"
   }
   ```

3. **Start your development server**:
   ```bash
   # Make sure your Django server is running on http://localhost:8000
   python manage.py runserver
   ```

## Running Tests

### Interactive Mode (recommended for development)
```bash
npm run cypress:open
```
This opens the Cypress Test Runner where you can select and run individual tests.

### Headless Mode (for CI/CD)
```bash
npm run cypress:run
```
This runs all tests headlessly in the terminal.

## Test Files

- `home.cy.js` - Tests for the home page
- `experiments.cy.js` - Tests for experiments-related pages
- `core-pages.cy.js` - Tests for dashboard, assistants, chatbots, documents, files, analysis, and help pages

## Custom Commands

Custom commands are defined in `cypress/support/commands.js`:

- `cy.login(username, password)` - Login to the application
- `cy.logout()` - Logout from the application
- `cy.seedTestData()` - Seed test data (customize as needed)
- `cy.cleanupTestData()` - Clean up test data (customize as needed)

## Best Practices

1. **Use the baseUrl**: All routes are relative to `http://localhost:8000` (configured in `cypress.config.ts`)
2. **Authentication**: Tests use `cy.login()` in `beforeEach` hooks to authenticate
3. **Test isolation**: Each test should be independent and not rely on other tests
4. **Selectors**: Prefer data attributes like `data-cy="element"` over CSS classes or IDs
5. **Wait strategically**: Cypress automatically waits for elements, but use `cy.wait()` for API calls if needed

## Configuration

The main configuration file is `cypress.config.ts` in the project root. You can customize:
- `baseUrl` - The base URL for your application
- `viewportWidth` / `viewportHeight` - Browser viewport size
- `defaultCommandTimeout` - How long to wait for commands to complete
- And more...

See [Cypress Configuration](https://docs.cypress.io/guides/references/configuration) for all options.
