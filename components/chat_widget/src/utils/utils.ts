/**
 * Convert a CSS percentage (60%) or pixel (10px) value to pixels.
 * @param value The CSS string value
 * @param maxValue The max value to use when converting from a percentage
 * @param defaultValue The default value if the CSS value is neither a percentage nor a pixel value.
 */
export const varToPixels = (value: string, maxValue: number, defaultValue: number) => {
  value = value.trim()
  if (value.includes("%")) {
      const percent = percentToFloat(value);
      if (!isNaN(percent)) {
        return maxValue * percent;
      }
    } else if (value.includes("px")) {
      const pixels = parseFloat(value);
      if (!isNaN(pixels)) {
        return pixels;
      }
    }
    return defaultValue;
}

const percentToFloat = (percentageString: string) => {
  const numericValue = parseFloat(percentageString);
  if (isNaN(numericValue)) {
    return NaN;
  }
  return numericValue / 100;
};
