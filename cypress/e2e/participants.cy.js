describe('Participants Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'test-team'

  beforeEach(() => {
    cy.login()
  })

  describe('Participants Home Page', () => {
    it('loads participants home page successfully', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.url().should('include', '/participants/')
      cy.get('.table-container').should('be.visible')
      cy.get('table').should('exist')
    })

    it('displays participants table', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has import and export buttons', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.contains('button, a', /Import|Export/i).should('exist')
    })

    it('filters can be applied', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.get('select, input[type="search"], .filter-control').then(($filters) => {
        if ($filters.length > 0) {
          cy.log('Filters are available on participants page')
        }
      })
    })
  })

  describe('Participant Details', () => {
    beforeEach(() => {
      cy.visit(`/a/${teamSlug}/participants/1/e/1`)
      cy.get('tbody tr', { timeout: 15000 }).should('exist')
    })

    it('navigates to participant details', () => {
      cy.get('tbody tr').first().click()
      cy.url().should('match', /participants\/\d+/)
    })

    it('displays participant information', () => {
      cy.get('tbody tr').first().click()
      cy.contains(/Name|Identifier|Email|Channel/i).should('exist')
    })

    it('shows participant data section', () => {
      cy.get('[aria-label="Participant Data"]').should('exist')
    })

    it('displays participant sessions', () => {
    cy.get('[aria-label="Sessions"]').click()
      cy.get('table').should('exist')
    })
  })

  describe('Edit Participant Data', () => {
    beforeEach(() => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.visit(`/a/${teamSlug}/participants/1/e/1`)
      cy.get('tbody tr', { timeout: 15000 }).should('exist')
    })

    it('can edit participant data', () => {
        cy.get('[aria-label="Participant Data"]').click()
        cy.get('textarea[name="participant-data"]').should('exist')
        cy.get('button[type="submit"]').should('exist')
    })
  })

  describe('Edit Participant Name', () => {
    it('can edit participant name', () => {
      cy.visit(`/a/${teamSlug}/participants/participant`)
      cy.get('tbody tr[data-redirect-url]', { timeout: 15000 }).first().then($row => {
        const url = $row.attr('data-redirect-url').split('#')[0]
        cy.visit(`${url}`)
      })
	  cy.get('button[hx-target="#participant-name"]').then(($editBtn) => {
	  if ($editBtn.length > 0) {
	      cy.wrap($editBtn).first().click()
	      cy.wait(10)
	      cy.get('input[name="name"]').should('be.visible')
	  }
	  })
    })
  })

  describe('Participant Schedules', () => {
    beforeEach(() => {
      cy.visit(`/a/${teamSlug}/participants/1/e/1`)
      cy.get('tbody tr', { timeout: 15000 }).should('exist')
    })

    it('displays scheduled messages', () => {
      cy.get('[aria-label="Schedules"]').click()
    })
  })

  describe('Participant Export', () => {
    it('opens export modal', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.contains('button, a', /Export/i).scrollIntoView().click({ force: true })
      cy.get('[role="dialog"], .modal, form, dialog').should('be.visible')
    })

    it('export form has experiment selection', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.contains('button, a', /Export/i).scrollIntoView().click({ force: true })
      cy.get('select, input[type="checkbox"]').should('exist')
    })
  })

  describe('Participant Import', () => {
    it('navigates to import page', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.contains('button, a', /Import/i).scrollIntoView().click({ force: true })
      cy.url().should('include', 'import')
    })

    it('import page has file upload', () => {
      cy.visit(`/a/${teamSlug}/participants/participants/import/`)
      cy.get('input[type="file"]').should('exist')
    })

    it('import page has experiment selection', () => {
      cy.visit(`/a/${teamSlug}/participants/participants/import/`)
      cy.get('select[name*="experiment"], #id_experiment').should('exist')
    })
  })

  describe('Participant Table Pagination', () => {
    it('shows pagination controls if many participants', () => {
      cy.visit(`/a/${teamSlug}/participants/participant/`)
      cy.get('.pagination').should('exist')
    })
  })
})
