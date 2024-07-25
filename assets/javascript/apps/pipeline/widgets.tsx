import React, { ChangeEventHandler } from "react";
import { InputParam } from "./types/nodeInputTypes";
import { NodeParameterValues } from "./types/nodeParameterValues";

const parameterValues: NodeParameterValues = JSON.parse(
  document.getElementById("parameter-values")?.textContent || "{}",
);
const options = parameterValues.LlmProviderId;

export function LlmProviderIdWidget({
  inputParam,
  value,
  onChange,
}: {
  inputParam: InputParam;
  value: string;
  onChange: ChangeEventHandler;
}) {
  return (
    <select
      className="select select-bordered w-full"
      name={inputParam.name}
      onChange={onChange}
      value={value}
    >
      {options.map((opt) => (
        <option value={opt.id}> {opt.name} </option>
      ))}
    </select>
  );
}
