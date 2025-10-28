describe('Chatbots Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
    cy.visit(`/a/${teamSlug}/chatbots/`, { failOnStatusCode: false })
    cy.get('body').should('be.visible')
    cy.get('h1', { timeout: 10000 }).should('exist')
    cy.wait(2000)
  })

  describe('Chatbots Home Page', () => {
    it('loads chatbots home page successfully', () => {
      cy.url().should('include', '/chatbots/')
      cy.get('body').should('be.visible')
    })

    it('page has title or heading', () => {
      cy.get('h1').should('exist')
      cy.contains('h1, h2, h3', /Chatbots/i).should('exist')
    })

    it('page has interactive elements', () => {
      cy.get('a, button, nav').should('have.length.greaterThan', 0)
    })
  })

  describe('Create Chatbot', () => {
    it('opens create chatbot form', () => {
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})

      // Modal dialog should appear
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')
      cy.get('dialog.modal h3').should('contain', 'Create a new Chatbot')
    })

    it('create chatbot form has required fields', () => {
      // Open the modal
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})

      // Wait for modal to open
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')

      // Check for the form with correct ID
      cy.get('form#new_chatbot_form').should('exist')

      // Check for required fields
      cy.get('input#id_name[name="name"]').should('exist')
      cy.get('textarea#id_description[name="description"]').should('exist')
    })

    it('validates required fields on submit', () => {
      // Open the modal
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})

      // Wait for modal to open
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')

      // Try to submit without filling required fields
      cy.get('button[type="submit"][form="new_chatbot_form"]').click({force: true})

      // Check for HTML5 validation (name field is required)
      cy.get('input#id_name:invalid').should('exist')
    })

    it('can fill out and submit create form', () => {
      // Open the modal
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})

      // Wait for modal to open
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')

      // Fill out the form
      const chatbotName = `Test Chatbot ${Date.now()}`
      cy.get('input#id_name').type(chatbotName)
      cy.get('textarea#id_description').type('This is a test chatbot created by Cypress')

      // Submit the form
      cy.get('button[type="submit"][form="new_chatbot_form"]').click({force: true})

      // Should redirect to chatbot details or close modal
      cy.url().should('match', /chatbots\/\d+\//, { timeout: 10000 })
    })
  })

  describe('Chatbot Details', () => {
    it('navigates to chatbot details from table', () => {
      // Wait for table to load via HTMX
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')

      // Click on first chatbot link in the table
      cy.get('table tbody tr a[href*="/chatbots/"]').first().click({force: true})

      // Should navigate to chatbot detail page
      cy.url().should('match', /chatbots\/\d+\//)
    })

    it('chatbot detail page has content', () => {
      // Wait for table to load
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')

      // Click on first chatbot
      cy.get('table tbody tr a[href*="/chatbots/"]').first().click({force: true})

      // Page should have loaded with content
      cy.get('body').should('be.visible')
      cy.get('h1, h2, h3').should('exist')
    })
  })

  describe('Chatbot Actions', () => {
    it('can access chatbot edit page', () => {
      // Wait for table to load
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')

      // Find and click the edit button (pencil icon) in first row
      cy.get('table tbody tr').first().within(() => {
        cy.get('a[href*="/edit/"]').click({force: true})
      })

      // Should navigate to edit page
      cy.url().should('include', '/edit/')
    })

    it('can start a new session', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr').first().within(() => {
        cy.get('button[title="New Session"]').click({force: true})
      })
      cy.url().should('include', '/session/')
    })
  })

  describe('Chatbot Table', () => {
    it('displays chatbot table with data', () => {
      // Table should exist - wait for HTMX to load it
      cy.get('table', { timeout: 10000 }).should('exist')

      // Table should have headers
      cy.get('table thead th').should('have.length.greaterThan', 0)

      // Table should have at least one row (if chatbots exist)
      cy.get('table tbody tr').should('exist')
    })

    it('table has correct columns', () => {
      // Wait for table to load
      cy.get('table', { timeout: 10000 }).should('exist')

      // Check for expected column headers
      cy.get('table thead th').should('contain', 'Name')
      cy.get('table thead th').should('contain', 'Total Participants')
      cy.get('table thead th').should('contain', 'Total Sessions')
      cy.get('table thead th').should('contain', 'Total Messages')
      cy.get('table thead th').should('contain', 'Actions')
    })

    it('can search chatbots', () => {
      // Search input should exist
      cy.get('input[type="search"][name="search"]', { timeout: 10000 }).should('exist')

      // Wait for table to initially load
      cy.get('table', { timeout: 10000 }).should('exist')

      // Type in search box
      cy.get('input[type="search"][name="search"]').type('test')

      // Wait for HTMX to update the table
      cy.wait(1500)

      // Table should still be visible
      cy.get('table').should('exist')
    })

    it('can toggle show archived', () => {
      // Show archived toggle should exist
      cy.get('input[type="checkbox"][name="show_archived"]', { timeout: 10000 }).should('exist')

      // Wait for table to initially load
      cy.get('table', { timeout: 10000 }).should('exist')

      // Click the toggle
      cy.get('input[type="checkbox"][name="show_archived"]').click({force: true})

      // Wait for HTMX to update
      cy.wait(1500)

      // Table should still be visible
      cy.get('table').should('exist')
    })
  })

  describe('Chatbot Row Interactions', () => {
    it('chatbot row has redirect functionality', () => {
      // Wait for table to load
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')

      // Rows should have data-redirect-url attribute
      cy.get('table tbody tr[data-redirect-url]').should('exist')
    })

    it('chatbot name link works', () => {
      // Wait for table to load
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')

      // Get the first chatbot name and URL
      cy.get('table tbody tr').first().within(() => {
        cy.get('a[href*="/chatbots/"]').should('have.attr', 'href').and('match', /\/chatbots\/\d+\/$/)
      })
    })
  })
})
