# Cypress End-to-End Tests

This directory contains comprehensive end-to-end tests for the Open Chat Studio application using Cypress.

## Test Coverage

The following applications have E2E test coverage (excluding experiments):

### Simplified Tests (Recommended)
These tests are robust and test what actually exists on the pages:

- **`chatbots-simple.cy.js`** - Tests chatbots pages load and display content correctly
- **`participants-simple.cy.js`** - Tests participants pages load and display content correctly  
- **`all-apps-simple.cy.js`** - Tests all application pages (dashboard, assistants, files, documents, analysis, help)
- **Core Pages** (`core-pages.cy.js`) - Basic page load tests for all main sections

### Detailed Tests (Optional)
These tests make more assumptions about page structure:

- **Chatbots** (`chatbots.cy.js`) - Detailed tests for chatbot features
- **Participants** (`participants.cy.js`) - Detailed tests for participant features
- **Dashboard** (`dashboard.cy.js`) - Detailed tests for dashboard features
- **Assistants** (`assistants.cy.js`) - Detailed tests for assistant features
- **Files & Documents** (`files-documents.cy.js`) - Detailed tests for file/document features

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

3. **Create Test User and Team:**
   
   **Quick Setup (Recommended):**
   ```bash
   python manage.py create_cypress_test_user
   ```
   
   Or alternatively:
   ```bash
   python cypress/create_test_user.py
   ```
   
   This will create a test user and team, then show you the credentials to add to `cypress.env.json`.
   
   **Manual Setup:**
   
   Alternatively, you can create a test user manually. Run these commands:
   
   ```bash
   # Using Django shell
   python manage.py shell
   ```
   
   Then in the Python shell:
   ```python
   from django.contrib.auth import get_user_model
   from apps.teams.models import Team, Membership
   
   # Create test user
   User = get_user_model()
   user = User.objects.create_user(
       username='testuser',
       email='test@example.com',
       password='testpassword'
   )
   
   # Create test team
   team = Team.objects.create(name='Test Team', slug='test-team')
   
   # Add user to team
   Membership.objects.create(user=user, team=team, role='admin')
   
   print(f"Created user: {user.email}")
   print(f"Created team: {team.slug}")
   ```
   
   Then update `cypress.env.json` with:
   ```json
   {
     "TEAM_SLUG": "test-team",
     "TEST_USER": "test@example.com",
     "TEST_PASSWORD": "testpassword"
   }
   ```

4. **Optionally Seed Test Data:**
   - Create some chatbots, participants, files, etc. for more meaningful tests
   - Tests will run even with empty data, they just won't find much content

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
