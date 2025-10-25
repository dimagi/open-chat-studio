describe('Core Application Pages', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    // Login before each test
    cy.login()
  })

  describe('Dashboard', () => {
    it('loads dashboard page', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.url().should('include', '/dashboard/')
    })
  })

  describe('Assistants', () => {
    it('loads assistants page', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.url().should('include', '/assistants/')
    })
  })

  describe('Chatbots', () => {
    it('loads chatbots page', () => {
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.url().should('include', '/chatbots/')
    })
  })

})
