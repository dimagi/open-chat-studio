// Import the necessary parts of the js-tiktoken library
const { encodingForModel } = require('js-tiktoken');

// Get the encoding details for gpt-3.5-turbo and gpt-4
const encoding = encodingForModel("gpt-3.5-turbo");

// Function to count tokens
function countTokens(string) {
    const tokens = encoding.encode(string);
    return tokens.length;
}

// Save the encoding object to the window for later use
window.countGPTTokens = countTokens;
