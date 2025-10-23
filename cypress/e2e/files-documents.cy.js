describe('Files and collections Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Files Home Page', () => {
    it('loads files page successfully', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.url().should('include', '/files/')
      cy.contains('Files').should('be.visible')
    })

    it('displays files table', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has search functionality', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('input[type="search"], input[name="search"]').then(($search) => {
        if ($search.length > 0) {
          cy.wrap($search).type('test{enter}')
          cy.wait(500)
        }
      })
    })

    it('displays file information in table', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('table tbody tr').first().then(($row) => {
        if ($row.length > 0) {
          cy.log('File table row exists')
        }
      })
    })
  })
  describe('Edit File', () => {
    it('can access edit file page', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Edit/i).then(($edit) => {
            if ($edit.length > 0) {
              cy.wrap($edit).click()
              cy.get('form').should('exist')
            }
          })
        }
      })
    })

    it('edit form shows current file info', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Edit/i).then(($edit) => {
            if ($edit.length > 0) {
              cy.wrap($edit).click()
              cy.get('input[name="name"]').should('exist')
            }
          })
        }
      })
    })
  })

  describe('collections Home Page', () => {
    it('loads collections page successfully', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.url().should('include', '/collection/')
    })

    it('displays document collections', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.contains(/collections|collections/i).should('exist')
    })

    it('has create collection button', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.contains('button, a', /Add new/i).should('exist')
    })
  })

  describe('Collection Details', () => {
    it('navigates to collection details', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('a[href*="/collections/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.url().should('match', /documents\/collection\/\d+/)
        }
      })
    })


    it('shows add files button', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('a[href*="/documents/collection/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Add Files/i).then(($add) => {
            if ($add.length > 0) {
              cy.log('Add files functionality available')
            }
          })
        }
      })
    })
  })

  describe('Add Files to Collection', () => {
    it('can upload files to collection', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('a[href*="/documents/collection/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Add Files/i).then(($upload) => {
            if ($upload.length > 0) {
              cy.wrap($upload).click()
              cy.get('input[type="file"], form').should('exist')
            }
          })
        }
      })
    })
  })

  describe('Collection Processing', () => {
    it('shows processing status', () => {
      cy.visit(`/a/${teamSlug}/collections/`)
      cy.get('a[href*="/collections/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Status|Processing|Ready/i).then(($status) => {
            if ($status.length > 0) {
              cy.log('Collection status displayed')
            }
          })
        }
      })
    })

    it('has refresh/sync button', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('a[href*="/documents/collection/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button', /Refresh|Sync|Process/i).then(($sync) => {
            if ($sync.length > 0) {
              cy.log('Refresh/sync functionality available')
            }
          })
        }
      })
    })
  })

  describe('Delete Collection', () => {
    it('has delete collection button', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('a[href*="/documents/collection/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Delete|Remove/i).then(($delete) => {
            if ($delete.length > 0) {
              cy.log('Delete collection functionality available')
            }
          })
        }
      })
    })

    it('delete requires confirmation', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('a[href*="/documents/collection/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Delete/i).then(($delete) => {
            if ($delete.length > 0) {
              cy.wrap($delete).click()
              cy.contains(/confirm|warning|sure/i, { timeout: 3000 }).should('exist')
            }
          })
        }
      })
    })
  })
})
