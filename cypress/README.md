# Cypress E2E Testing

This directory contains end-to-end tests for Open Chat Studio using Cypress.

## Setup

1. **Install dependencies** (if not already done):
   ```bash
   bun install
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
bun run cypress:open
```
This opens the Cypress Test Runner where you can select and run individual tests.

### Headless Mode (for CI/CD)
```bash
bun run cypress:run
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

### Core Testing Philosophy

**Test functionality, not appearance.** Verify that features work correctly through assertions, not just by logging that steps executed. Focus on user actions, data persistence, and application state.

### 1. Write Assertions, Not Logs

- **DO**: Use `.should()` assertions to verify outcomes
- **DON'T**: Just log that steps executed without verifying results

```javascript
// BAD - Only logs, doesn't verify anything
it('creates an experiment', () => {
  cy.visit('/experiments/new');
  cy.get('[data-cy="experiment-name"]').type('Test');
  cy.get('[data-cy="submit"]').click();
  cy.log('Experiment created'); // This will pass regardless
});

// GOOD - Verifies the outcome
it('creates an experiment', () => {
  cy.visit('/experiments/new');
  cy.get('[data-cy="experiment-name"]').type('Test');
  cy.get('[data-cy="submit"]').click();
  cy.url().should('include', '/experiments');
  cy.contains('Test').should('exist');
});
```

### 2. Test Complete User Workflows

Test end-to-end journeys, not isolated UI elements:

```javascript
// GOOD - Complete workflow with verification
it('creates and starts an experiment', () => {
  cy.visit('/experiments/new');
  cy.get('[data-cy="name"]').type('My Experiment');
  cy.get('[data-cy="submit"]').click();

  cy.url().should('include', '/experiments');
  cy.contains('My Experiment').click();
  cy.get('[data-cy="start"]').click();
  cy.get('[data-cy="status"]').should('contain', 'Running');
});
```

### 3. Selector Strategy

- **PREFERRED**: `data-cy` attributes
- **ACCEPTABLE**: Semantic HTML (`button`, `nav`) or ARIA labels
- **AVOID**: CSS classes or generic IDs

```html
<button data-cy="create-experiment" class="btn btn-primary">Create</button>
```

### 4. Assert Functional Outcomes

**Data Persistence:**
```javascript
cy.get('[data-cy="name"]').type('Test');
cy.get('[data-cy="save"]').click();
cy.reload();
cy.get('[data-cy="name"]').should('have.value', 'Test');
```

**API Interactions:**
```javascript
cy.intercept('POST', '/api/experiments').as('create');
cy.get('[data-cy="submit"]').click();
cy.wait('@create').its('response.statusCode').should('eq', 201);
```

**Navigation:**
```javascript
cy.get('[data-cy="view-details"]').click();
cy.url().should('match', /\/experiments\/\d+/);
```

### 5. Test Isolation

Each test should be independent. Use `beforeEach` for setup and `afterEach` for cleanup:

```javascript
describe('Experiments', () => {
  beforeEach(() => {
    cy.login(Cypress.env('TEST_USER'), Cypress.env('TEST_PASSWORD'));
    cy.visit('/experiments');
  });

  afterEach(() => {
    cy.cleanupTestData();
  });

  it('creates an experiment', () => { /* ... */ });
  it('deletes an experiment', () => { /* ... */ });
});
```

### 6. Wait for APIs, Not Time

```javascript
// GOOD - Wait for specific API
cy.intercept('GET', '/api/experiments*').as('getExperiments');
cy.visit('/experiments');
cy.wait('@getExperiments');
cy.get('[data-cy="list"]').should('exist');

// BAD - Arbitrary timeout
cy.wait(3000); // Don't do this
```

### 7. What NOT to Test

- Visual styling (colors, fonts, margins)
- Responsive layout changes
- Third-party library functionality
- Performance metrics

### 8. Test Error Handling

```javascript
it('shows error when API fails', () => {
  cy.intercept('POST', '/api/experiments', { statusCode: 500 }).as('failed');
  cy.get('[data-cy="submit"]').click();
  cy.wait('@failed');
  cy.get('[data-cy="error"]').should('be.visible');
});

it('validates required fields', () => {
  cy.get('[data-cy="submit"]').click();
  cy.get('[data-cy="error"]').should('contain', 'Name is required');
});
```

## Configuration

The main configuration file is `cypress.config.ts` in the project root. You can customize:
- `baseUrl` - The base URL for your application
- `viewportWidth` / `viewportHeight` - Browser viewport size
- `defaultCommandTimeout` - How long to wait for commands to complete
- And more...

See [Cypress Configuration](https://docs.cypress.io/guides/references/configuration) for all options.
