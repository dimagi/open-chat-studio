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
  // Option 1: Login via UI (slower but tests the login flow)
  const user = username || Cypress.env('TEST_USER') || 'testuser'
  const pass = password || Cypress.env('TEST_PASSWORD') || 'testpassword'
  
  cy.session([user, pass], () => {
    cy.visit('/accounts/login/')
    cy.get('input[name="login"]').type(user)
    cy.get('input[name="password"]').type(pass)
    cy.get('button[type="submit"]').click()
    // Wait for redirect after successful login
    cy.url().should('not.include', '/accounts/login/')
  })

  // Option 2: Login via API (faster, recommended for tests that don't test login)
  // Uncomment and customize this if you have a login API endpoint
  /*
  cy.request({
    method: 'POST',
    url: '/api/auth/login/',
    body: {
      username: user,
      password: pass,
    },
  }).then((response) => {
    // Save the auth token or session cookie
    window.localStorage.setItem('authToken', response.body.token)
  })
  */
})

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
