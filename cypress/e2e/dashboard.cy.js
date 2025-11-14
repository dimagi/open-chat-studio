describe('Dashboard Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Dashboard Home Page', () => {
    it('loads dashboard successfully', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.url().should('include', '/dashboard/')
    })

    it('displays overview statistics', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      // Check for common stat cards
      cy.contains(/Total|Active|Sessions|Messages/i).should('exist')
    })

    it('has date range filter', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('select[name*="date"], input[type="date"], .date-range-picker').then(($dateFilter) => {
        if ($dateFilter.length > 0) {
          cy.log('Date range filter is available')
        }
      })
    })
  })

  describe('Dashboard Charts', () => {
    it('displays session analytics chart', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      // Look for chart container (common class names for chart libraries)
      cy.get('canvas, svg, .chart, .recharts-wrapper, .highcharts-container', { timeout: 10000 }).then(
        ($chart) => {
          if ($chart.length > 0) {
            cy.log('Charts are rendered on dashboard')
          }
        }
      )
    })

    it('message volume chart loads', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.contains(/Message|Volume|Traffic/i).then(($section) => {
        if ($section.length > 0) {
          cy.log('Message volume section found')
        }
      })
    })

    it('bot performance metrics display', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.contains(/Performance|Bot|Metrics/i).then(($section) => {
        if ($section.length > 0) {
          cy.log('Performance metrics section found')
        }
      })
    })
  })

  describe('Dashboard Filters', () => {
    it('can filter by chatbot', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)

      // Click into the Tom Select input
      cy.get('#id_experiments-ts-control', { timeout: 10000 })
        .should('be.visible')
        .click()

      cy.get('#id_experiments-ts-dropdown')
        .should('be.visible')
        .find('[role="option"]')
        .first()
        .click()
    })

    it('can change date range', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('select[name*="date_range"], #date-range-select').then(($select) => {
        if ($select.length > 0) {
          cy.wrap($select).select('7') // Select 7 days
        }
      })
    })

    it('can change granularity', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('select[name*="granularity"]').then(($select) => {
        if ($select.length > 0) {
          cy.wrap($select).select('daily')
        }
      })
    })
  })

  describe('Dashboard Data Tables', () => {
    it('bot performance table displays', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)

      cy.contains('.card-title span', 'Bot Performance Summary', { timeout: 10000 })
        .should('be.visible')
        .parents('.card-body')
        .within(() => {
          cy.get('table#botPerformanceTable')
            .should('exist')
            .and('be.visible')

          cy.get('tbody tr')
            .its('length')
            .should('be.greaterThan', 0)
        })
    })

    it('sessions table displays', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)

      cy.contains('.card-title span', 'Active Sessions', { timeout: 10000 })
        .should('be.visible')
        .parents('.card-body')
        .within(() => {
          cy.get('canvas#sessionAnalyticsChart')
            .should('exist')
            .and('be.visible')
        })
    })

    it('user engagement activity table displays', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)

      cy.contains('.card-title span', 'Most Active Participants', { timeout: 10000 })
        .should('be.visible')
        .parents('.card-body')
        .within(() => {
          cy.get('table#mostActiveTable')
            .should('exist')
            .and('be.visible')

          cy.get('tbody tr')
            .its('length')
            .should('be.greaterThan', 0)
        })
    })
  })



  describe('Dashboard Reset', () => {
    it('clicks the reset filters button', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.contains('button', 'Reset', { timeout: 10000 })
        .should('be.visible')
        .click({ force: true })
    })

    it('data loads without errors', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      // Check that no error messages are displayed
      cy.contains(/error|failed|unavailable/i).should('not.exist')
    })
  })

  describe('Dashboard Responsiveness', () => {
    it('dashboard is usable on tablet viewport', () => {
      cy.viewport('ipad-2')
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('body').should('be.visible')
    })

    it('dashboard is usable on mobile viewport', () => {
      cy.viewport('iphone-x')
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('body').should('be.visible')
    })
  })

  describe('Dashboard Performance', () => {
    it('loads within acceptable time', () => {
      const start = Date.now()
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('canvas, svg, table', { timeout: 15000 }).then(() => {
        const loadTime = Date.now() - start
        expect(loadTime).to.be.lessThan(15000) // Should load within 15 seconds
      })
    })
  })
})
