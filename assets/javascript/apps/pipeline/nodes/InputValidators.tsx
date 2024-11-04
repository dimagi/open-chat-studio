function greaterThan(inputValue: number, validatorParams: Record<string, any>): string | void {
    const value = validatorParams.value;
    if (inputValue && inputValue < value) {
        return `Value must be greater than ${value}`;
    }
}

function lesserThan(inputValue: number, validatorParams: Record<string, any>): string | void {
    const value = validatorParams.value;
    if (inputValue && inputValue > value) {
        return `Value must be less than ${value}`;
    }
}

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function required(inputValue: number, validatorParams: Record<string, any>): string {
    return inputValue ? "" : "This field is required"
}

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function checkJson(inputValue: any, validatorParams: Record<string, any>): string | void {
    if (!inputValue) {
        return
    }
    try {
        JSON.parse(inputValue);
    } catch {
        return "Invalid JSON format";
    }
}

function variableRequired(inputValue: any, validatorParams: Record<string, any>): string | void {
    if (!inputValue) {
        return
    }
    const variablePresent = inputValue.includes(validatorParams.variable);
    return variablePresent ? "" : `Missing variable ${validatorParams.variable}`;
}

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function commaSeparatedEmails(inputValue: string, validatorParams: Record<string, any>): string | void {
    if (!inputValue) {
        return
    }

    const validateEmail = (email: string) => {
        return String(email)
          .toLowerCase()
          .match(
            /^(([^<>()[\]\\.,;:\s@"]+(\.[^<>()[\]\\.,;:\s@"]+)*)|.(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/
          );
      };

      const items = inputValue.split(",").map(item => item.trim());
      for (const item of items) {
          const isValid = validateEmail(item);
          if (!isValid) {
              return "Invalid recipient list";
          }
      }
}

export type ValidatorSpec = {
    name: string;
    params: Record<string, any>
}

type ValidatorMethod = (inputValue: any, validatorParams: Record<string, any>) => string | void;

export const validators: Record<string, ValidatorMethod> = {
    "required": required,
    "greater_than": greaterThan,
    "lesser_than": lesserThan,
    "valid_json": checkJson,
    "variable_required": variableRequired,
    "comma_separated_emails": commaSeparatedEmails
  }




