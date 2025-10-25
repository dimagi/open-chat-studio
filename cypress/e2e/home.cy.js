describe('Home Page', () => {
  it('successfully loads', () => {
    cy.visit('/')
    cy.url().should('eq', Cypress.config().baseUrl + '/')
  })

  it('displays the main content', () => {
    cy.visit('/')
    // Check for common elements that should be present
    cy.get('body').should('be.visible')
  })
})
