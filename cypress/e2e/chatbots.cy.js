describe('Chatbots Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
    cy.visit(`/a/${teamSlug}/chatbots/`)
  })

  describe('Create Chatbot', () => {
    it('opens create chatbot form', () => {
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})

      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')
      cy.get('dialog.modal h3').should('contain', 'Create a new Chatbot')
    })

    it('create chatbot form has required fields', () => {
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')
      cy.get('form#new_chatbot_form').should('exist')
      cy.get('input#id_name[name="name"]').should('exist')
      cy.get('textarea#id_description[name="description"]').should('exist')
    })

    it('validates required fields on submit', () => {
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')
      cy.get('button[type="submit"][form="new_chatbot_form"]').click({force: true})
      cy.get('input#id_name:invalid').should('exist')
    })

    it('can fill out and submit create form', () => {
      cy.contains('button', /Add New/i, { timeout: 10000 }).should('be.visible').click({force: true})
      cy.get('dialog.modal[open]', { timeout: 5000 }).should('be.visible')
      const chatbotName = `Test Chatbot ${Date.now()}`
      cy.get('input#id_name').type(chatbotName)
      cy.get('textarea#id_description').type('This is a test chatbot created by Cypress')
      cy.get('button[type="submit"][form="new_chatbot_form"]').click({force: true})
      cy.url().should('match', /chatbots\/\d+\//, { timeout: 10000 })
    })
  })

  describe('Chatbot Details', () => {
    it('navigates to chatbot details from table', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr a[href*="/chatbots/"]').first().click({force: true})
      cy.url().should('match', /chatbots\/\d+\//)
    })

    it('chatbot detail page has content', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr a[href*="/chatbots/"]').first().click({force: true})
      cy.get('body').should('be.visible')
      cy.get('h1, h2, h3').should('exist')
    })
  })

  describe('Chatbot Actions', () => {
    it('can access chatbot edit page', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr').first().within(() => {
        cy.get('a[href*="/edit/"]').click({force: true})
      })
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
      cy.get('table', { timeout: 10000 }).should('exist')
      cy.get('table thead th').should('have.length.greaterThan', 0)
      cy.get('table tbody tr').should('exist')
    })

    it('table has correct columns', () => {
      cy.get('table', { timeout: 10000 }).should('exist')

      // Check for expected column headers
      cy.get('table thead th').should('contain', 'Name')
      cy.get('table thead th').should('contain', 'Total Participants')
      cy.get('table thead th').should('contain', 'Total Sessions')
      cy.get('table thead th').should('contain', 'Total Interactions')
      cy.get('table thead th').should('contain', 'Actions')
    })

    it('can search chatbots', () => {
      // Search input should exist
      cy.get('input[type="search"][name="search"]', { timeout: 10000 }).should('exist')
      cy.get('table', { timeout: 10000 }).should('exist')
      // Type in search box
      cy.get('input[type="search"][name="search"]').type('test')
      cy.get('table').should('exist')
    })

    it('can toggle show archived', () => {
      cy.get('input[type="checkbox"][name="show_archived"]', { timeout: 10000 }).should('exist')
      cy.get('table', { timeout: 10000 }).should('exist')
      cy.get('input[type="checkbox"][name="show_archived"]').click({force: true})
      // Table should still be visible
      cy.get('table').should('exist')
    })
  })

  describe('Chatbot Row Interactions', () => {
    it('chatbot row has redirect functionality', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr[data-redirect-url]').should('exist')
    })

    it('chatbot name link works', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr').first().within(() => {
        cy.get('a[href*="/chatbots/"]').should('have.attr', 'href').and('match', /\/chatbots\/\d+\/$/)
      })
    })
  })
})
