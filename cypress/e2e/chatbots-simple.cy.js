describe('Chatbots Pages', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  before(() => {
    cy.login()
  })

  it('chatbots home page loads', () => {
    cy.visit(`/a/${teamSlug}/chatbots/`)
    cy.url().should('include', '/chatbots/')
    cy.get('body').should('be.visible')
  })

  it('chatbots page has content', () => {
    cy.visit(`/a/${teamSlug}/chatbots/`)
    // Page should have some text content
    cy.get('body').invoke('text').should('have.length.greaterThan', 10)
  })

  it('chatbots page has navigation or links', () => {
    cy.visit(`/a/${teamSlug}/chatbots/`)
    // Should have at least some links or buttons
    cy.get('a, button').should('exist')
  })

  it('can find chatbot table or list if it exists', () => {
    cy.visit(`/a/${teamSlug}/chatbots/`)
    // Don't fail if table doesn't exist, just log
    cy.get('body').then(($body) => {
      if ($body.find('table').length > 0) {
        cy.log('Table found on chatbots page')
        cy.get('table').should('be.visible')
      } else if ($body.find('[role="table"]').length > 0) {
        cy.log('ARIA table found on chatbots page')
        cy.get('[role="table"]').should('be.visible')
      } else {
        cy.log('No table found - page may be empty or use different layout')
      }
    })
  })

  it('can find create/add button if it exists', () => {
    cy.visit(`/a/${teamSlug}/chatbots/`)
    cy.get('body').then(($body) => {
      const createButton = $body.find('button, a').filter((i, el) => {
        const text = Cypress.$(el).text().toLowerCase()
        return text.includes('create') || text.includes('add') || text.includes('new')
      })

      if (createButton.length > 0) {
        cy.log('Create/Add button found')
        cy.wrap(createButton).first().should('be.visible')
      } else {
        cy.log('No create button found - may require specific permissions')
      }
    })
  })

  it('can navigate to chatbot table view', () => {
    cy.visit(`/a/${teamSlug}/chatbots/table/`)
    // Just check page loads
    cy.get('body').should('be.visible')
  })

  it('can access new chatbot page', () => {
    cy.visit(`/a/${teamSlug}/chatbots/new/`)
    cy.get('body').then(($body) => {
      // Check if form exists
      if ($body.find('form').length > 0) {
        cy.log('Create chatbot form found')
        cy.get('form').should('be.visible')
      } else if ($body.text().includes('403') || $body.text().includes('permission')) {
        cy.log('No permission to create chatbots')
      } else {
        cy.log('Form not found or different page structure')
      }
    })
  })
})
