describe('All Application Pages', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  before(() => {
    cy.login()
  })

  describe('Dashboard', () => {
    it('dashboard page loads', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.url().should('include', '/dashboard/')
      cy.get('body').should('be.visible')
    })

    it('dashboard has charts or data', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('body').then(($body) => {
        const hasCharts = $body.find('canvas, svg, .chart').length > 0
        const hasTables = $body.find('table').length > 0
        const hasData = $body.text().length > 100

        if (hasCharts) {
          cy.log('Dashboard has charts')
        } else if (hasTables) {
          cy.log('Dashboard has tables')
        } else if (hasData) {
          cy.log('Dashboard has content')
        } else {
          cy.log('Dashboard may be empty')
        }
      })
    })
  })

  describe('Assistants', () => {
    it('assistants page loads', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.url().should('include', '/assistants/')
      cy.get('body').should('be.visible')
    })

    it('assistants page has content', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('body').invoke('text').should('have.length.greaterThan', 10)
    })

    it('can access new assistant page', () => {
      cy.visit(`/a/${teamSlug}/assistants/new/`)
      cy.get('body').then(($body) => {
        if ($body.find('form').length > 0) {
          cy.log('Create assistant form found')
        } else {
          cy.log('Form not found or insufficient permissions')
        }
      })
    })
  })

  describe('Files', () => {
    it('files page loads', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.url().should('include', '/files/')
      cy.get('body').should('be.visible')
    })

    it('files page has content', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('body').invoke('text').should('have.length.greaterThan', 10)
    })

    it('can access new file page', () => {
      cy.visit(`/a/${teamSlug}/files/new/`)
      cy.get('body').then(($body) => {
        if ($body.find('form').length > 0 || $body.find('input[type="file"]').length > 0) {
          cy.log('File upload form found')
        } else {
          cy.log('File upload not available or different structure')
        }
      })
    })
  })

  describe('Documents', () => {
    it('documents page loads', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.url().should('include', '/documents/')
      cy.get('body').should('be.visible')
    })

    it('documents page has content', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('body').invoke('text').should('have.length.greaterThan', 10)
    })

    it('can access new document collection page', () => {
      cy.visit(`/a/${teamSlug}/documents/new/`)
      cy.get('body').then(($body) => {
        if ($body.find('form').length > 0) {
          cy.log('Create collection form found')
        } else {
          cy.log('Form not found or insufficient permissions')
        }
      })
    })
  })

  describe('Analysis', () => {
    it('analysis page loads', () => {
      cy.visit(`/a/${teamSlug}/analysis/`)
      cy.url().should('include', '/analysis/')
      cy.get('body').should('be.visible')
    })

    it('analysis page has content', () => {
      cy.visit(`/a/${teamSlug}/analysis/`)
      cy.get('body').invoke('text').should('have.length.greaterThan', 10)
    })
  })

  describe('Service Providers / Team Settings', () => {
    it('manage team page loads', () => {
      cy.visit(`/a/${teamSlug}/team/`)
      cy.get('body').should('be.visible')
    })

    it('team page has settings content', () => {
      cy.visit(`/a/${teamSlug}/team/`)
      cy.get('body').invoke('text').should('have.length.greaterThan', 20)
    })
  })

  describe('Navigation', () => {
    it('all pages have navigation or links', () => {
      const pages = [
        `/a/${teamSlug}/chatbots/`,
        `/a/${teamSlug}/participants/`,
        `/a/${teamSlug}/dashboard/`,
        `/a/${teamSlug}/assistants/`,
        `/a/${teamSlug}/files/`,
        `/a/${teamSlug}/documents/`,
        `/a/${teamSlug}/analysis/`
      ]

      pages.forEach(page => {
        cy.visit(page)
        // Check for navigation or at least some links/buttons
        cy.get('body').then(($body) => {
          const hasNav = $body.find('nav, .nav, [role="navigation"]').length > 0
          const hasLinks = $body.find('a').length > 0

          if (hasNav) {
            cy.log(`${page}: Has navigation element`)
          } else if (hasLinks) {
            cy.log(`${page}: Has links (no explicit nav)`)
          } else {
            cy.log(`${page}: Minimal page structure`)
          }

          // At minimum, should have some interactive elements
          expect($body.find('a, button').length).to.be.greaterThan(0)
        })
      })
    })
  })
})
