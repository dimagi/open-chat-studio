describe('Chatbots Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Chatbots Home Page', () => {
    it('loads chatbots home page successfully', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.url().should('include', '/chatbots/')
      cy.contains('Chatbots').should('be.visible')
    })

    it('displays chatbots table', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      // Wait for table to load
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has add new chatbot button', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.contains('button, a', /Add New|Create|New Chatbot/i, { timeout: 5000 }).should('exist')
    })

    it('search functionality works', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      // Check if search input exists
      cy.get('input[type="search"], input[name="search"]').then(($search) => {
        if ($search.length > 0) {
          cy.wrap($search).first().type('test search{enter}')
          cy.url().should('include', 'search')
        }
      })
    })
  })

  describe('Create Chatbot', () => {
    it('opens create chatbot form', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      // Try to click the create button
      cy.contains('button, a', /Add New|Create|New Chatbot/i).then(($btn) => {
        if ($btn.length > 0) {
          cy.wrap($btn).first().click()
          // Either modal or new page should appear
          cy.get('form, [role="dialog"], .modal', { timeout: 5000 }).should('exist')
        }
      })
    })

    it('create chatbot form has required fields', () => {
      cy.visit(`/a/${teamSlug}/chatbots/new/`)
      // Check for common form fields
      cy.get('input[name="name"], #id_name').should('exist')
      cy.get('textarea[name="description"], #id_description').should('exist')
    })

    it('validates required fields on submit', () => {
      cy.visit(`/a/${teamSlug}/chatbots/new/`)
      // Try to submit empty form
      cy.get('button[type="submit"]').click()
      // Should show validation errors
      cy.contains(/required|field|error/i).should('exist')
    })
  })

  describe('Chatbot Details', () => {
    it('navigates to chatbot details from table', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      // Click on first chatbot link if exists
      cy.get('table tbody tr a, .chatbot-link').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.url().should('match', /chatbots\/[^/]+\/$/)
        }
      })
    })

    it('chatbot detail page displays tabs', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.get('table tbody tr a, .chatbot-link').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Check for tabs
          cy.get('[role="tablist"], .nav-tabs, .tabs').should('exist')
        }
      })
    })
  })

  describe('Chatbot Settings', () => {
    it('can access chatbot settings', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.get('table tbody tr').first().then(($row) => {
        if ($row.length > 0) {
          // Find and click settings link
          cy.wrap($row).find('a').first().click()
          // Look for settings tab or button
          cy.contains('button, a', /Settings/i, { timeout: 5000 }).then(($settings) => {
            if ($settings.length > 0) {
              cy.wrap($settings).first().click()
              cy.url().should('match', /settings|config/)
            }
          })
        }
      })
    })

    it('settings form has cancel button', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Settings/i).then(($settings) => {
            if ($settings.length > 0) {
              cy.wrap($settings).first().click()
              cy.contains('button', /Cancel|Close/i).should('exist')
            }
          })
        }
      })
    })
  })

  describe('Chatbot Sessions', () => {
    it('displays sessions for chatbot', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Look for sessions tab or section
          cy.contains('Sessions, Chat History').then(($tab) => {
            if ($tab.length > 0) {
              cy.wrap($tab).click()
              cy.get('table, .session-list').should('exist')
            }
          })
        }
      })
    })
  })
})
