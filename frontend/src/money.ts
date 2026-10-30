export const DEFAULT_USD_TO_CNY = "6.7";

export const money = (value: number, digits = 2) => "¥" + value.toFixed(digits);

// Catalog rates arrive as decimal strings. Do not introduce binary float tails.
export function fromUsd(value: string, rate: string | number = DEFAULT_USD_TO_CNY) {
  function decimal(raw: string) {
    const match = raw.match(/^(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
    if (!match) throw new Error("无效的模型单价或汇率");
    return { digits: BigInt(match[1] + (match[2] || "")), scale: (match[2]?.length || 0) - Number(match[3] || 0) };
  }
  const a = decimal(value), b = decimal(String(rate));
  let digits = String(a.digits * b.digits);
  const scale = a.scale + b.scale;
  if (scale <= 0) return digits + "0".repeat(-scale);
  digits = digits.padStart(scale + 1, "0");
  return (digits.slice(0, -scale) + "." + digits.slice(-scale)).replace(/\.?0+$/, "");
}
