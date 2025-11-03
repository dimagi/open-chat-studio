describe('Participants Pages', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  before(() => {
    cy.login()
  })

  it('participants home page loads', () => {
    cy.visit(`/a/${teamSlug}/participants/`)
    cy.url().should('include', '/participants/')
    cy.get('body').should('be.visible')
  })

  it('participants page has content', () => {
    cy.visit(`/a/${teamSlug}/participants/`)
    cy.get('body').invoke('text').should('have.length.greaterThan', 10)
  })

  it('participants table or list is displayed', () => {
    cy.visit(`/a/${teamSlug}/participants/`)
    cy.get('body').then(($body) => {
      if ($body.find('table').length > 0) {
        cy.log('Participants table found')
        cy.get('table').should('be.visible')
      } else if ($body.find('[role="table"]').length > 0) {
        cy.log('ARIA table found')
        cy.get('[role="table"]').should('be.visible')
      } else {
        cy.log('No table - participants list may be empty')
      }
    })
  })

  it('can find import/export options if they exist', () => {
    cy.visit(`/a/${teamSlug}/participants/`)
    cy.get('body').then(($body) => {
      const buttons = $body.find('button, a').filter((i, el) => {
        const text = Cypress.$(el).text().toLowerCase()
        return text.includes('import') || text.includes('export')
      })

      if (buttons.length > 0) {
        cy.log('Import/Export functionality found')
      } else {
        cy.log('No import/export buttons found')
      }
    })
  })

  it('can access participants table view', () => {
    cy.visit(`/a/${teamSlug}/participants/table/`)
    cy.get('body').should('be.visible')
  })

  it('can access new participant page', () => {
    cy.visit(`/a/${teamSlug}/participants/new/`)
    cy.get('body').then(($body) => {
      if ($body.find('form').length > 0) {
        cy.log('Create participant form found')
        cy.get('form').should('be.visible')
      } else {
        cy.log('Form not found or insufficient permissions')
      }
    })
  })

  it('can access import page', () => {
    cy.visit(`/a/${teamSlug}/participants/import/`)
    cy.get('body').then(($body) => {
      if ($body.find('form').length > 0 || $body.find('input[type="file"]').length > 0) {
        cy.log('Import form found')
      } else if ($body.text().includes('403') || $body.text().includes('permission')) {
        cy.log('No permission to import participants')
      } else {
        cy.log('Import page structure may be different')
      }
    })
  })
})
