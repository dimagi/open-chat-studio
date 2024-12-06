// Import the necessary parts of the js-tiktoken library
import {encodingForModel} from "js-tiktoken";

// Get the encoding details for gpt-3.5-turbo and gpt-4
const encoding = encodingForModel("gpt-3.5-turbo");

// Function to count tokens
export function countGPTTokens(stringToCount) {
    const tokens = encoding.encode(stringToCount);
    return tokens.length;
}
