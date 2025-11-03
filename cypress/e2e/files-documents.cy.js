describe('Files and Collections Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  before(() => {
    cy.login()
  })

  describe('Files Home Page', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('body').should('be.visible')
      cy.wait(10) // Wait for HTMX
    })

    it('loads files page successfully', () => {
      cy.url().should('include', '/files/')
    })

    it('displays files table', () => {
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has search functionality', () => {
      cy.get('input[type="search"], input[name="search"]', { timeout: 10000 }).should('exist')
      cy.get('input[type="search"], input[name="search"]').type('test')
      cy.wait(10)
    })

    it('displays file information in table', () => {
      cy.get('table tbody tr', { timeout: 1000 }).should('exist')
      cy.get('table tbody tr').should('have.length.greaterThan', 0)
    })
  })

  describe('File Details and Edit', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('body').should('be.visible')
      cy.wait(10)
    })

    it('can navigate to file details', () => {
      cy.get('table tbody tr', { timeout: 1000 }).should('exist')
      cy.get('table tbody tr a').first().click({force: true})
      cy.url().should('include', '/files/')
      cy.get('body').should('be.visible')
    })

    it('can access edit file page', () => {
      cy.get('table tbody tr', { timeout: 1000 }).should('exist')
      cy.get('table tbody tr a').first().click({force: true})
      cy.wait(10)
      cy.contains('button, a', /Edit/i, { timeout: 10000 }).should('exist').click({force: true})
      cy.get('form').should('exist')
      cy.contains('h1', /Edit File/i).should('exist')
    })

    it('edit form shows file name field', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr a').first().click({force: true})
      cy.wait(10)
      cy.contains('button, a', /Edit/i, { timeout: 10000 }).click({force: true})
      cy.get('input#id_name[name="name"]').should('exist')
      cy.get('input#id_name').should('have.attr', 'required')
    })

    it('edit form has summary field', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr a').first().click({force: true})
      cy.wait(10)
      cy.contains('button, a', /Edit/i, { timeout: 10000 }).click({force: true})
      cy.get('textarea#id_summary[name="summary"]').should('exist')
    })

    it('edit form has update button', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr a').first().click({force: true})
      cy.wait(10)
      cy.contains('button, a', /Edit/i, { timeout: 10000 }).click({force: true})
      cy.get('input[type="submit"]').should('exist')
      cy.get('input[type="submit"]').should('have.value', 'Update')
    })

    it('edit form displays collections', () => {
      cy.get('table tbody tr', { timeout: 10000 }).should('exist')
      cy.get('table tbody tr a').first().click({force: true})
      cy.wait(10)
      cy.contains('button, a', /Edit/i, { timeout: 10000 }).click({force: true})
      cy.contains('h3', /Collections/i).should('exist')
    })
  })

  describe('Collections Home Page', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('body').should('be.visible')
      cy.wait(10)
    })

    it('loads collections page successfully', () => {
      cy.url().should('include', '/collection/')
      cy.get('h1, h2, h3').should('exist')
    })

    it('displays collections content', () => {
      // Page should have some content about collections
      cy.get('body').should('not.be.empty')
    })

    it('has create collection button', () => {
      cy.contains('button, a', /Add New/i, { timeout: 10000 }).should('exist')
    })
  })

  // Helper to open the first collection link
  const openFirstCollection = () => {
    cy.get('tbody tr').first().click()
    cy.url().should('match', /\/documents\/collections\/\d+/)
  }

  describe('Collection Details', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('body').should('be.visible')
      cy.wait(10)
    })

    it('navigates to collection details', () => {
      openFirstCollection()
    })

    it('collection detail page has title and content', () => {
      openFirstCollection()
      cy.get('h1.pg-title', { timeout: 10000 }).should('exist')
    })

    it('collection shows file count information', () => {
      openFirstCollection()
      cy.contains(/files remaining|files uploaded|remaining/i, { timeout: 10000 }).should('exist')
    })
  })

  describe('Collection Actions', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('body').should('be.visible')
      cy.wait(10)
    })

    it('collection has search functionality', () => {
      openFirstCollection()
      cy.get('input#search-input[type="search"]').should('exist')
      cy.get('input#search-input').should('have.attr', 'placeholder', 'Search files...')
    })

    it('collection has file upload modal', () => {
      openFirstCollection()
      cy.get('dialog#chooseFilesModal', { timeout: 10000 }).should('exist')
    })
  })

  describe('Collection Management', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('body').should('be.visible')
      cy.wait(10)
    })
    it('collection page has breadcrumb navigation', () => {
      openFirstCollection()
      cy.get('.breadcrumbs', { timeout: 10000 }).should('exist')
      cy.get('.breadcrumbs').within(() => {
        cy.contains('Collections').should('exist')
      })
    })

    it('collection displays files section', () => {
      openFirstCollection()
      cy.contains('h2', /Files/i).should('exist')
      cy.get('#collection-files-container').should('exist')
    })

    it('collection has query/search link', () => {
      openFirstCollection()
      cy.get('select, input[type="search"]', { timeout: 1000 }).should('exist')
    })
  })

  describe('File Details and Edit', () => {
    beforeEach(() => {
      cy.login()
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('body').should('be.visible')
      cy.wait(10)
    })

    it('can navigate to file details', () => {
      openFirstCollection()
      cy.wait(10)
      cy.get('#collection-files-container')
        .find('a.btn.btn-sm.btn-soft.btn-primary')
        .first()
        .click({ force: true })
      cy.url().should('match', /\/files\/file\/\d+/)
    })
  })
})
