describe('Home Page', () => {
  it('successfully loads', () => {
    cy.visit('/')
    cy.url().should('eq', Cypress.config().baseUrl + '/')
  })

  it('displays the main content', () => {
    cy.visit('/')
    cy.get('body').should('be.visible')
  })
})
