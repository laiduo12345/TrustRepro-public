import prices from "./cc-switch-pricing.json";
import { fromUsd, DEFAULT_USD_TO_CNY } from "./money";

// CC Switch's local built-in USD / million token prices, captured 2026-10-26.
// Convert new defaults to CNY; saved provider prices remain authoritative.
export function defaultModelPrice(model: string, exchangeRate: string | number = DEFAULT_USD_TO_CNY) {
  const identifier = model
    .trim()
    .split("/")
    .pop()!
    .toLowerCase()
    .split(":")[0]
    .replace(/\[1m\]$/, "")
    .replaceAll("@", "-");
  const candidates = [
    identifier,
    identifier.replace(/-\d{4}-?\d{2}-?\d{2}$/, ""),
  ];
  for (const candidate of candidates) {
    if (!Object.hasOwn(prices, candidate)) continue;
    const price = (prices as Record<string, string[]>)[candidate];
    if (price) return { inputRate: fromUsd(price[0], exchangeRate), outputRate: fromUsd(price[1], exchangeRate) };
  }
  return undefined;
}
