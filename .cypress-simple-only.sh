#!/bin/bash
# Script to run only the simplified Cypress tests (recommended)

echo "Running simplified Cypress E2E tests..."
echo "========================================"
echo ""
echo "These tests are robust and gracefully handle:"
echo "  ✓ Empty pages (no data)"
echo "  ✓ Missing permissions"
echo "  ✓ Different page structures"
echo ""

bunx cypress run --spec "cypress/e2e/*-simple.cy.js,cypress/e2e/core-pages.cy.js"

echo ""
echo "========================================"
echo "Test run complete!"
echo ""
echo "To run in interactive mode:"
echo "  bunx cypress open"
echo ""
echo "Then select only the *-simple.cy.js files"
