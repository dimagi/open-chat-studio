# Cypress End-to-End Tests

This directory contains comprehensive end-to-end tests for the Open Chat Studio application using Cypress.

## Test Coverage

The following applications have E2E test coverage (excluding experiments):

- **Chatbots** (`chatbots.cy.js`) - Tests for chatbot creation, management, settings, and sessions
- **Participants** (`participants.cy.js`) - Tests for participant management, data editing, import/export
- **Dashboard** (`dashboard.cy.js`) - Tests for analytics dashboard, charts, filters, and data visualization
- **Assistants** (`assistants.cy.js`) - Tests for OpenAI assistant management, tools, files, and sync
- **Files & Documents** (`files-documents.cy.js`) - Tests for file uploads, document collections, and integrations
- **Core Pages** (`core-pages.cy.js`) - Basic page load tests for all main sections

## Prerequisites

1. **Install Cypress:**
   ```bash
   npm install cypress --save-dev
   ```

2. **Configure Environment Variables:**
   Create a `cypress.env.json` file in the root directory:
   ```json
   {
     "TEAM_SLUG": "your-team-slug",
     "TEST_USER": "test@example.com",
     "TEST_PASSWORD": "testpassword"
   }
   ```

3. **Ensure Test Data:**
   - Create a test team with the slug specified in `TEAM_SLUG`
   - Create a test user with the credentials specified above
   - Optionally seed test data (chatbots, participants, files, etc.)

## Running Tests

### Open Cypress Test Runner (Interactive Mode)
```bash
npx cypress open
```
This opens the Cypress UI where you can select and run individual tests.

### Run All Tests (Headless Mode)
```bash
npx cypress run
```

### Run Specific Test File
```bash
npx cypress run --spec "cypress/e2e/chatbots.cy.js"
```

### Run Tests in Different Browsers
```bash
npx cypress run --browser chrome
npx cypress run --browser firefox
npx cypress run --browser edge
```

### Run Tests with Video Recording
```bash
npx cypress run --video
```

## Test Structure

Each test file follows a consistent structure:

1. **Login Setup** - All tests use `cy.login()` before each test
2. **Page Navigation** - Tests navigate to specific application pages
3. **Graceful Degradation** - Tests check if elements exist before interacting
4. **Multiple Assertions** - Tests verify various aspects of functionality

### Example Test Pattern

```javascript
describe('Application Feature', () => {
  beforeEach(() => {
    cy.login()
  })

  it('performs an action', () => {
    cy.visit(`/a/${teamSlug}/page/`)
    
    // Gracefully handle optional elements
    cy.get('button').then(($btn) => {
      if ($btn.length > 0) {
        cy.wrap($btn).click()
        cy.get('form').should('exist')
      }
    })
  })
})
```

## Test Best Practices

1. **Isolation** - Each test should be independent and not rely on previous tests
2. **Cleanup** - Tests should clean up after themselves (use fixtures or test data)
3. **Timeouts** - Use appropriate timeouts for dynamic content (e.g., `{ timeout: 10000 }`)
4. **Selectors** - Prefer data attributes over CSS classes for stability
5. **Assertions** - Use meaningful assertions that verify actual functionality

## Custom Commands

Custom Cypress commands are defined in `cypress/support/commands.js`:

- `cy.login()` - Logs in with default or provided credentials
- `cy.logout()` - Logs out the current user
- `cy.seedTestData()` - Seeds the database with test data (if implemented)
- `cy.cleanupTestData()` - Cleans up test data (if implemented)

## Debugging Tests

### Visual Debugging
```bash
npx cypress open --browser chrome
```
Click on a test to run it and watch the browser interact with your app.

### Screenshot on Failure
Cypress automatically takes screenshots when tests fail. Find them in:
```
cypress/screenshots/
```

### Video Recording
Videos are saved when running in headless mode:
```
cypress/videos/
```

### Console Logging
Add debug logs in tests:
```javascript
cy.log('Debug message here')
```

## Configuration

Cypress configuration is in `cypress.config.js`. Key settings:

- `baseUrl` - Base URL of the application
- `viewportWidth/Height` - Default viewport size
- `defaultCommandTimeout` - Default timeout for commands
- `video` - Enable/disable video recording
- `screenshotOnRunFailure` - Enable/disable screenshots on failure

## Continuous Integration

To run tests in CI/CD:

```yaml
# Example GitHub Actions workflow
- name: Run Cypress tests
  run: |
    npm install
    npx cypress run --browser chrome
```

## Troubleshooting

### Tests are flaky
- Increase timeouts for slow-loading elements
- Add explicit waits: `cy.wait(1000)`
- Ensure test data exists before running tests

### Login fails
- Verify `TEST_USER` and `TEST_PASSWORD` in `cypress.env.json`
- Check that the user exists in the database
- Ensure the login flow matches your authentication method

### Elements not found
- Check that the team slug is correct
- Verify that test data (chatbots, participants, etc.) exists
- Inspect the page structure - selectors may need updating

## Adding New Tests

When adding new E2E tests:

1. Create a new `.cy.js` file in `cypress/e2e/`
2. Follow the existing test structure and patterns
3. Use descriptive test names that explain what is being tested
4. Include both positive and negative test cases
5. Test edge cases and error handling
6. Document any special setup requirements

## Test Maintenance

- **Review tests quarterly** to ensure they match current UI
- **Update selectors** when UI components change
- **Add tests** for new features as they're developed
- **Remove tests** for deprecated features
- **Keep test data clean** to avoid test pollution

## Resources

- [Cypress Documentation](https://docs.cypress.io/)
- [Cypress Best Practices](https://docs.cypress.io/guides/references/best-practices)
- [Cypress API Reference](https://docs.cypress.io/api/table-of-contents)
