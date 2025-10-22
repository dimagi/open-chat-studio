describe('Experiments Page', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    // Login before each test
    cy.login()
  })

  it('loads experiments home page', () => {
    cy.visit(`/a/${teamSlug}/experiments/`)
    cy.url().should('include', '/experiments/')
  })

  it('loads prompt builder page', () => {
    cy.visit(`/a/${teamSlug}/experiments/prompt_builder`)
    cy.url().should('include', '/experiments/prompt_builder')
  })

  it('loads experiments table page', () => {
    cy.visit(`/a/${teamSlug}/experiments/table/`)
    cy.url().should('include', '/experiments/table')
  })

  it('loads new experiment page', () => {
    cy.visit(`/a/${teamSlug}/experiments/new/`)
    cy.url().should('include', '/experiments/new')
  })
})
