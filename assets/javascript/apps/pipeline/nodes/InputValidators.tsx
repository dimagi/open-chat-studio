function greaterThan(inputValue: number, validatorParams: Record<string, any>) {
    const value = validatorParams.value;
    if (!inputValue || inputValue > value) {
        return "";
    }
    return `Value must be greater than ${value}`;
}

function lesserThan(inputValue: number, validatorParams: Record<string, any>) {
    const value = validatorParams.value;
    if (!inputValue || inputValue < value) {
        return "";
    }
    return `Value must be less than ${value}`;
}

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function required(inputValue: number, validatorParams: Record<string, any>) {
    return inputValue ? "" : "This field is required"
}

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function checkJson(inputValue: any, validatorParams: Record<string, any>) {
    try {
        JSON.parse(inputValue);
        return "";
    } catch {
        return "Invalid JSON format";
    }
}

export type Validator = {
    name: string;
    params: Record<string, any>
}

export const validators = {
    "required": required,
    "greater_than": greaterThan,
    "lesser_than": lesserThan,
    "valid_json": checkJson
  }




