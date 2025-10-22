describe('Files and Documents Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Files Home Page', () => {
    it('loads files page successfully', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.url().should('include', '/files/')
      cy.contains('Files').should('be.visible')
    })

    it('displays files table', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has search functionality', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('input[type="search"], input[name="search"]').then(($search) => {
        if ($search.length > 0) {
          cy.wrap($search).type('test{enter}')
          cy.wait(500)
        }
      })
    })

    it('displays file information in table', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('table tbody tr').first().then(($row) => {
        if ($row.length > 0) {
          cy.log('File table row exists')
        }
      })
    })
  })

  describe('File Upload', () => {
    it('has upload file functionality', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.contains('button, a', /Upload|Add|New/i).then(($upload) => {
        if ($upload.length > 0) {
          cy.log('Upload functionality available')
        }
      })
    })

    it('upload form accepts file input', () => {
      cy.visit(`/a/${teamSlug}/files/new/`)
      cy.get('input[type="file"]').should('exist')
    })
  })

  describe('File Details', () => {
    it('can view file details', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.url().should('match', /files\/\d+/)
        }
      })
    })

    it('displays file metadata', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Name|Size|Type|Created/i).should('exist')
        }
      })
    })
  })

  describe('Edit File', () => {
    it('can access edit file page', () => {
      cy.visit(`/a/${teamSlug}/files/`)
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
      cy.visit(`/a/${teamSlug}/files/`)
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

  describe('Delete File', () => {
    it('has delete functionality', () => {
      cy.visit(`/a/${teamSlug}/files/`)
      cy.get('table tbody tr').first().then(($row) => {
        if ($row.length > 0) {
          cy.wrap($row).find('button, a').contains(/Delete|Remove/i).then(($delete) => {
            if ($delete.length > 0) {
              cy.log('Delete functionality available')
            }
          })
        }
      })
    })
  })

  describe('Documents Home Page', () => {
    it('loads documents page successfully', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.url().should('include', '/documents/')
    })

    it('displays document collections', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.contains(/Collections|Documents/i).should('exist')
    })

    it('has create collection button', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.contains('button, a', /Create|New|Add/i).should('exist')
    })
  })

  describe('Create Document Collection', () => {
    it('opens create collection form', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.contains('button, a', /Create.*Collection|New.*Collection/i).then(($btn) => {
        if ($btn.length > 0) {
          cy.wrap($btn).click()
          cy.get('form, [role="dialog"]').should('exist')
        }
      })
    })

    it('create collection form has required fields', () => {
      cy.visit(`/a/${teamSlug}/documents/new/`)
      cy.get('input[name="name"], #id_name').should('exist')
    })

    it('validates collection name', () => {
      cy.visit(`/a/${teamSlug}/documents/new/`)
      cy.get('button[type="submit"]').click()
      cy.contains(/required|field|error/i).should('exist')
    })
  })

  describe('Collection Details', () => {
    it('navigates to collection details', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.url().should('match', /documents\/\d+/)
        }
      })
    })

    it('displays collection files', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Files|Documents/i).should('exist')
        }
      })
    })

    it('shows add files button', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Add.*File|Upload/i).then(($add) => {
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
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Add|Upload/i).then(($upload) => {
            if ($upload.length > 0) {
              cy.wrap($upload).click()
              cy.get('input[type="file"], form').should('exist')
            }
          })
        }
      })
    })

    it('supports drag and drop', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.get('[data-dropzone], .dropzone').then(($dropzone) => {
            if ($dropzone.length > 0) {
              cy.log('Drag and drop upload available')
            }
          })
        }
      })
    })
  })

  describe('Document Sources', () => {
    it('displays document sources tab', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Sources|Integrations/i).then(($tab) => {
            if ($tab.length > 0) {
              cy.log('Document sources available')
            }
          })
        }
      })
    })

    it('can add document source', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Add.*Source|Connect/i).then(($add) => {
            if ($add.length > 0) {
              cy.log('Add document source functionality available')
            }
          })
        }
      })
    })

    it('supports GitHub integration', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/GitHub|github/i).then(($github) => {
            if ($github.length > 0) {
              cy.log('GitHub integration available')
            }
          })
        }
      })
    })

    it('supports Confluence integration', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Confluence/i).then(($confluence) => {
            if ($confluence.length > 0) {
              cy.log('Confluence integration available')
            }
          })
        }
      })
    })
  })

  describe('Collection Processing', () => {
    it('shows processing status', () => {
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
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
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
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
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
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
      cy.visit(`/a/${teamSlug}/documents/`)
      cy.get('a[href*="/documents/"]').first().then(($link) => {
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
