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
    
    // Wait for navigation
    cy.wait(2000)
    
    // Check result
    cy.url({ timeout: 15000 }).then(url => {
      if (url.includes('/accounts/login/')) {
        // Still on login page - check for errors
        cy.get('body').then($body => {
          const bodyText = $body.text()
          if (bodyText.includes('incorrect') || bodyText.includes('invalid') || bodyText.includes('does not exist')) {
            throw new Error(
              `❌ Login failed for user '${user}'.\n` +
              `Please check:\n` +
              `1. User exists in database\n` +
              `2. Password is correct\n` +
              `3. TEST_USER and TEST_PASSWORD are set in cypress.env.json`
            )
          } else if (bodyText.includes('2FA') || bodyText.includes('two-factor') || bodyText.includes('authentication')) {
            throw new Error('❌ 2FA is enabled. Please disable 2FA for test user or handle in tests')
          } else {
            throw new Error(`❌ Login failed for unknown reason. Check login page at /accounts/login/`)
          }
        })
      } else {
        cy.log('✓ Login successful')
      }
    })
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
