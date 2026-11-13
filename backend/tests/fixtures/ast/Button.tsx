import React, { useState } from "react";
import type { ButtonProps } from "./types";

export const Button = ({ label, onPress }: ButtonProps) => (
  <button onClick={onPress}>{label}</button>
);

export default function Counter() {
  const [count, setCount] = useState(0);
  return <Button label={String(count)} onPress={() => setCount(count + 1)} />;
}
