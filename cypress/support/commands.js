// ***********************************************
// Custom commands for Open Chat Studio
// ***********************************************

/**
 * Login command - customize based on your authentication method
 * 
 * Usage:
 *   cy.login('username', 'password')
 *   or cy.login() to use default test credentials
 */
Cypress.Commands.add('login', (username, password) => {
  const user = username || Cypress.env('TEST_USER')
  const pass = password || Cypress.env('TEST_PASSWORD')
  
  // Skip login if credentials not provided
  if (!user || !pass) {
    cy.log('⚠️ No credentials provided. Set TEST_USER and TEST_PASSWORD in cypress.env.json')
    cy.log('Tests will attempt to run without authentication')
    return
  }
  
  cy.session([user, pass], () => {
    cy.visit('/accounts/login/')
    
    // Check if already logged in (redirected away from login)
    cy.url().then(url => {
      if (!url.includes('/accounts/login/')) {
        cy.log('✓ Already logged in')
        return
      }
    })
    
    cy.log(`Attempting login as: ${user}`)
    
    // Fill in login form
    cy.get('input[name="login"]', { timeout: 10000 }).should('be.visible').clear().type(user)
    cy.get('input[name="password"]').should('be.visible').clear().type(pass, { log: false })
    
    // Submit form
    cy.get('input[type="submit"]').click()

  }, {
    validate() {
      cy.request({url: '/users/profile/', followRedirect: false}).its('status').should('eq', 200);
    }
  })
})

Cypress.Commands.add('pageTitleEquals', (title) => {
  cy.get('[data-cy="title"]').contains(title)
});

/**
 * Logout command
 */
Cypress.Commands.add('logout', () => {
  cy.visit('/accounts/logout/')
})

/**
 * Create test data command
 * Customize based on your application's needs
 */
Cypress.Commands.add('seedTestData', () => {
  // This could call a custom test endpoint that seeds your database
  // cy.request('POST', '/test/seed/')
})

/**
 * Clean up test data command
 */
Cypress.Commands.add('cleanupTestData', () => {
  // This could call a custom test endpoint that cleans up test data
  // cy.request('POST', '/test/cleanup/')
})
