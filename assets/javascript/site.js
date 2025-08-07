// put site-wide dependencies here.
// HTMX setup: https://htmx.org/docs/#installing
import 'htmx.org';
import './htmx';
import './alpine';
import './alertify';
import './tom-select';
import './theme-toggle';
import './tables'
import 'open-chat-studio-widget';

document.addEventListener('DOMContentLoaded', () => {
  const element = document.querySelector('open-chat-studio-widget ');
  if (element) {
    let welcomeMessages = ['Hi! Welcome to our support chat.', 'How can we help you today?'];
    let starterQuestions = ['How do I create a bot?', 'How do I connect my bot to WhatsApp?']
    element.welcomeMessages = JSON.stringify(welcomeMessages)
    element.starterQuestions = JSON.stringify(starterQuestions)
  }
})
