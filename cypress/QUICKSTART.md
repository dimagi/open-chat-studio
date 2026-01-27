# Cypress E2E Tests - Quick Start

## Getting Started in 3 Steps

### 1. Install Cypress
```bash
npm install cypress --save-dev
```

### 2. Create Test User and Seed Data
```bash
python manage.py seed_dev_data
```

This will output something like:
```
==================================================
Seeding development data...
==================================================
Default groups initialized
Created user: test@example.com
Created team: test-team
Added user to team as owner
...

Setup complete!

You can now log in with:
  Email: test@example.com
  Password: testpassword
  Team: test-team
```

### 3. Create `cypress.env.json`
Create a new file `cypress.env.json` in your project root:

```json
{
  "TEAM_SLUG": "test-team",
  "TEST_USER": "test@example.com",
  "TEST_PASSWORD": "testpassword"
}
```

## Run Tests

### Quick Run (Simplified Tests Only)
```bash
./.cypress-simple-only.sh
```

This runs only the robust, simplified tests that gracefully handle empty pages and missing data.

### Interactive Mode (Recommended for development)
```bash
npx cypress open
```

Then select only the `*-simple.cy.js` files from the list.

### Headless Mode (All Tests)
```bash
npx cypress run
```

**Warning:** This runs ALL tests including detailed ones that may fail if pages are empty.

### Run Specific Tests
```bash
# Run simplified tests only (RECOMMENDED - these are more robust)
npx cypress run --spec "cypress/e2e/*-simple.cy.js"

# Run just chatbots tests
npx cypress run --spec "cypress/e2e/chatbots-simple.cy.js"

# Run all apps test
npx cypress run --spec "cypress/e2e/all-apps-simple.cy.js"
```

**Note:** The detailed tests (`assistants.cy.js`, `dashboard.cy.js`, etc.) make more assumptions about page structure and may fail if:
- Tables are empty (no data exists)
- Elements are hidden or in dropdowns
- Page structure differs from expectations

Use the simplified tests for CI/CD and general testing.

## What Gets Tested

The simplified tests check that:
- Pages load successfully
- Pages have content
- Basic navigation works
- Forms exist where expected
- Tables/lists display when data exists

Tests are **graceful** - they won't fail if:
- Pages are empty (no data yet)
- Features require special permissions
- Optional elements don't exist

## Management Command Options

```bash
# Create user/team only (no sample data)
python manage.py seed_dev_data --skip-sample-data

# Custom email and team
python manage.py seed_dev_data --email dev@test.com --team-slug dev-team

# See all options
python manage.py seed_dev_data --help
```

## Troubleshooting

### Login Fails
```
Login failed for user 'test@example.com'
```

**Solution:** Run the setup command again:
```bash
python manage.py seed_dev_data
```

### Wrong Team Slug
```
AssertionError: expected 'http://localhost:8000/404' to include '/chatbots/'
```

**Solution:** Check your `cypress.env.json` has the correct `TEAM_SLUG`

### Tests Skip Login
```
No credentials provided. Tests will attempt to run without authentication
```

**Solution:** Make sure `cypress.env.json` exists and has TEST_USER and TEST_PASSWORD

### App Not Running
```
Error: Cypress failed to verify that your server is running
```

**Solution:** Start your Django development server:
```bash
python manage.py runserver
```

## Test Files

- `*-simple.cy.js` - Simplified, robust tests (recommended)
- `*.cy.js` - Detailed tests with more assumptions
- `core-pages.cy.js` - Basic page load tests
