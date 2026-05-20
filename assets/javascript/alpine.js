import Alpine from 'alpinejs'
import fileUploads from './components/fileUploads'

window.Alpine = Alpine
Alpine.data('fileUploads', fileUploads)
window.onload = Alpine.start
