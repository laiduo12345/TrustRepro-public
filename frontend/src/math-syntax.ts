import type { Plugin } from "unified";
import type { Root, Code, InlineCode, Nodes } from "mdast";
import { fromMarkdown } from "mdast-util-from-markdown";
import type { CompileContext, Extension as FromMarkdownExtension } from "mdast-util-from-markdown";
import type { Construct, Extension, State, Token } from "micromark-util-types";

export type MathPart = {
  kind: "math" | "pending";
  start: number;
  end: number;
  raw: string;
  tex: string;
  display: boolean;
};
export type TextPart = { kind: "text"; start: number; end: number; raw: string };
type Range = { start: number; end: number; mathBoundary?: boolean };

function escaped(source: string, index: number) {
  let count = 0;
  while (index > 0 && source[--index] === "\\") count++;
  return count % 2 === 1;
}

function protectedRanges(source: string): Range[] {
  const ranges: Range[] = [];
  // CommonMark knows whether indentation is code or a list continuation.
  // It also handles nested fences, escaped backticks and literal HTML blocks.
  function visit(node: Nodes) {
    const start = node.position?.start.offset, end = node.position?.end.offset;
    if (start !== undefined && end !== undefined &&
      ["code", "inlineCode", "html", "image", "definition"].includes(node.type)) {
      ranges.push({ start, end, mathBoundary: node.type !== "code" || /^(?: {0,3})(?:`{3,}|~{3,})/.test(source.slice(start, end)) });
      return;
    }
    if ((node.type === "link" || node.type === "linkReference") && start !== undefined && end !== undefined) {
      // Preserve link syntax around pending math, while allowing math in its label.
      ranges.push({ start, end: start + 1, mathBoundary: true });
      const labelEnd = node.children.at(-1)?.position?.end.offset ?? start;
      const labelClose = source.indexOf("]", labelEnd);
      if (labelClose >= labelEnd && labelClose < end) ranges.push({ start: labelClose, end, mathBoundary: true });
    }
    if ("children" in node) node.children.forEach(visit);
  }
  visit(fromMarkdown(source));
  for (const match of source.matchAll(/\b(?:https?|ftp):\/\/[^\s<>]+/g)) {
    ranges.push({ start: match.index, end: match.index + match[0].length });
  }
  return ranges.sort((a, b) => a.start - b.start || b.end - a.end);
}

/** Read-only math spans. Their offsets always refer to the original model output. */
export function mathParts(source: string): (MathPart | TextPart)[] {
  const protectedSpans = protectedRanges(source);
  const parts: (MathPart | TextPart)[] = [];
  let cursor = 0, textStart = 0, protectedIndex = 0;
  while (cursor < source.length) {
    while (protectedIndex < protectedSpans.length && protectedSpans[protectedIndex].end <= cursor) protectedIndex++;
    const protectedSpan = protectedSpans[protectedIndex];
    if (protectedSpan && protectedSpan.start <= cursor) { cursor = protectedSpan.end; continue; }
    let open = "", close = "", display = false;
    if (!escaped(source, cursor)) {
      if (source.startsWith("\\(", cursor)) { open = "\\("; close = "\\)"; }
      else if (source.startsWith("\\[", cursor)) { open = "\\["; close = "\\]"; display = true; }
      else if (source[cursor] === "$" && source[cursor - 1] !== "$") {
        if (source.startsWith("$$", cursor) && source[cursor + 2] !== "$") {
          open = close = "$$"; display = true;
        } else if (source[cursor + 1] !== "$" && source[cursor + 1] && !/\s/.test(source[cursor + 1])) {
          // A numeric currency opener must not swallow a later, unrelated formula.
          const nearest = source.indexOf("$", cursor + 1);
          const currency = /[0-9]/.test(source[cursor + 1]) && (nearest === -1 ||
            /[0-9]/.test(source[nearest + 1] || "") || /\s/.test(source[nearest - 1]));
          if (!currency) open = close = "$";
        }
      }
    }
    if (!open) { cursor++; continue; }
    let end = cursor + open.length, closing = -1, boundaryIndex = protectedIndex;
    for (; end < source.length; end++) {
      if (!display && source[end] === "\n" && /^[ \t\r]*\n/.test(source.slice(end + 1))) break;
      while (boundaryIndex < protectedSpans.length && protectedSpans[boundaryIndex].end <= end) boundaryIndex++;
      const boundary = protectedSpans[boundaryIndex];
      if (boundary && boundary.start <= end && boundary.mathBoundary) break;
      if (!source.startsWith(close, end) || escaped(source, end)) continue;
      if (close[0] === "$" && (source[end - 1] === "$" || source[end + close.length] === "$")) continue;
      if (close === "$" && (/\s/.test(source[end - 1]) || /[0-9]/.test(source[end + 1] || ""))) continue;
      closing = end; break;
    }
    const stop = closing === -1 ? end : closing + close.length;
    const tex = source.slice(cursor + open.length, closing === -1 ? stop : closing);
    if (closing !== -1 && !tex.trim()) { cursor = stop; continue; }
    if (cursor > textStart) parts.push({ kind: "text", start: textStart, end: cursor, raw: source.slice(textStart, cursor) });
    // Container prefixes belong to Markdown, not to the TeX expression.
    const prefix = source.slice(source.lastIndexOf("\n", cursor - 1) + 1, cursor).match(/^(?: {0,3}>[ \t]?)+/)?.[0];
    const body = prefix ? tex.split("\n").map((line, i) => i && line.startsWith(prefix) ? line.slice(prefix.length) : line).join("\n") : tex;
    parts.push({ kind: closing === -1 ? "pending" : "math", start: cursor, end: stop,
      raw: source.slice(cursor, stop), tex: body, display });
    cursor = textStart = stop;
  }
  if (textStart < source.length) parts.push({ kind: "text", start: textStart, end: source.length, raw: source.slice(textStart) });
  return parts;
}

declare module "micromark-util-types" {
  interface TokenTypeMap {
    trustReproMath: "trustReproMath";
    trustReproMathFlow: "trustReproMathFlow";
    trustReproMathData: "trustReproMathData";
  }
}

/** A tokenizer, rather than rewriting Markdown source, preserves links and escapes. */
export const remarkSafeMath: Plugin<[{ source: string; inlineOnly?: boolean }], Root> = function (options) {
  const spans = new Map(mathParts(options.source).filter((p): p is MathPart => p.kind !== "text").map((p) => [p.start, p]));
  function construct(flow: boolean): Construct {
    return {
      name: flow ? "trustReproMathFlow" : "trustReproMath",
      concrete: flow,
      tokenize(effects, ok, nok) {
        const context = this;
        const span = spans.get(context.now().offset);
        const type = flow ? "trustReproMathFlow" : "trustReproMath";
        let entered = false;
        let dataOpen = false;
        const consume: State = (code) => {
          if (!span || flow && (!span.display || !/^[ \t\r]*(?:\n|$)/.test(options.source.slice(span.end)))) return nok(code);
          if (!entered) { effects.enter(type); entered = true; }
          if (context.now().offset >= span.end) {
            if (dataOpen) effects.exit("trustReproMathData");
            effects.exit(type); return ok(code);
          }
          if (code === null) return nok(code);
          if (code === -3 || code === -4 || code === -5) {
            if (dataOpen) { effects.exit("trustReproMathData"); dataOpen = false; }
            effects.enter("lineEnding");
            effects.consume(code);
            effects.exit("lineEnding");
          } else {
            if (!dataOpen) { effects.enter("trustReproMathData"); dataOpen = true; }
            effects.consume(code);
          }
          return consume;
        };
        return consume;
      },
    };
  }
  function enter(this: CompileContext, token: Token) {
    const span = spans.get(token.start.offset)!;
    const node: Code | InlineCode = {
      type: token.type === "trustReproMathFlow" ? "code" : "inlineCode",
      value: span.tex,
      data: {
        hName: span.kind === "math" ? "code" : "span",
        hProperties: span.kind === "math" ? {
          dataTrMath: true, dataTrMathTex: span.tex, dataTrMathSource: span.raw,
          dataTrMathDisplay: span.display && !options.inlineOnly,
        } : { className: ["math-pending"] },
        hChildren: [{ type: "text", value: span.kind === "math" ? span.tex : span.raw }],
      },
    };
    this.enter(node, token);
    this.buffer();
  }
  function exit(this: CompileContext, token: Token) { this.resume(); this.exit(token); }
  const extension: Extension = {
    disable: { null: ["mathText", "mathFlow"] },
    text: { 36: construct(false), 92: construct(false) },
    flow: { 36: construct(true), 92: construct(true) },
  };
  const fromMarkdown: FromMarkdownExtension = {
    enter: { trustReproMath: enter, trustReproMathFlow: enter },
    exit: { trustReproMath: exit, trustReproMathFlow: exit },
  };
  const data = this.data() as { micromarkExtensions?: Extension[]; fromMarkdownExtensions?: FromMarkdownExtension[] };
  (data.micromarkExtensions ||= []).push(extension);
  (data.fromMarkdownExtensions ||= []).push(fromMarkdown);
};
