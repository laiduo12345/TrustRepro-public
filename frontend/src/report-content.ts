import type { Json } from "./client";

export const imageTypes = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);
const anchor = /^[A-Za-z][A-Za-z0-9-]*$/;
const object = (value: Json): value is Record<string, Json> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

// Keep anchor allocation aligned with the Markdown export, including older blocks.
export function reportBlocks(blocks: Json[]) {
  const used = new Set<string>();
  const explicit = new Set(
    blocks.flatMap((b) =>
      object(b) && typeof b.id === "string" && anchor.test(b.id) ? [b.id] : [],
    ),
  );
  const counters = { image: 0, table: 0 };
  return blocks.map((block, index) => {
    const value = object(block) ? block : {};
    const kind = value.type;
    const number =
      kind === "image" || kind === "table" ? ++counters[kind] : null;
    const prefix =
      kind === "image" ? "figure" : kind === "table" ? "table" : "section";
    const fallback = `${prefix}-${number ?? index + 1}`;
    let id = typeof value.id === "string" ? value.id : "";
    if (!anchor.test(id) || used.has(id)) {
      id = fallback;
      let suffix = 2;
      while (used.has(id) || explicit.has(id)) id = `${fallback}-${suffix++}`;
    }
    used.add(id);
    let title = String(value.title || "");
    if (number !== null) {
      const label = kind === "image" ? "图" : "表";
      if (
        !new RegExp(`^${label}\\s*\\d|^(?:Figure|Table)\\s+\\d`, "i").test(
          title,
        )
      )
        title = `${label} ${number}${title ? ` · ${title}` : ""}`;
    }
    return { block, id, title };
  });
}
