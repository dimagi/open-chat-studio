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

  describe('Documents', () => {
    it('loads documents page', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.url().should('include', '/documents/')
    })
  })

  describe('Files', () => {
    it('loads files page', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.url().should('include', '/files/')
    })
  })

  describe('Analysis', () => {
    it('loads analysis page', () => {
      cy.visit(`/a/${teamSlug}/analysis/`)
      cy.url().should('include', '/analysis/')
    })
  })

  describe('Help', () => {
    it('loads help page', () => {
      cy.visit(`/a/${teamSlug}/help/`)
      cy.url().should('include', '/help/')
    })
  })
})
