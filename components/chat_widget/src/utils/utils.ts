export const percentToFloat = (percentageString) => {
  const numericValue = parseFloat(percentageString);
  if (isNaN(numericValue)) {
    return NaN;
  }
  return numericValue / 100;
};
