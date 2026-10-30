import { Fragment, memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import type { Plugin } from "unified";
import type { Element, ElementContent, Root } from "hast";
import { mathParts, remarkSafeMath } from "./math-syntax";
import "katex/dist/katex.min.css";
import "./math.css";

// Only KaTeX-generated trees are cached. Agent HTML is never parsed into this tree.
const cache = new Map<string, ElementContent[] | null>();
function renderFormula(tex: string, display: boolean, file: Parameters<ReturnType<typeof rehypeKatex>>[1]) {
  const key = JSON.stringify([tex, display]);
  if (cache.has(key)) return cache.get(key)!;
  let result: ElementContent[] | null = null;
  if (!/\\(?:href|url|includegraphics|htmlClass|htmlId|htmlStyle|htmlData|html@mathml)\b/.test(tex)) {
    const tree: Root = { type: "root", children: [{ type: "element", tagName: "code",
      properties: { className: [display ? "math-display" : "math-inline"] },
      children: [{ type: "text", value: tex }] }] };
    const count = file.messages.length;
    try {
      rehypeKatex({ trust: false, maxExpand: 1000, maxSize: 20, strict: "ignore", macros: {} })(tree, file);
      if (file.messages.length === count) result = tree.children as ElementContent[];
    } catch { /* Invalid math stays model text. */ }
  }
  if (cache.size >= 128) cache.delete(cache.keys().next().value!);
  cache.set(key, result);
  return result;
}

/** Render only our marked math nodes; fenced code and Agent HTML stay untouched. */
export const rehypeSafeMath: Plugin<[], Root> = function () {
  return (tree, file) => {
    function visit(parent: Root | Element) {
      parent.children = parent.children.map((node) => {
        if (node.type !== "element") return node;
        // MDAST code-flow nodes get a <pre> wrapper. Math must not inherit code styles.
        const child = node.children.length === 1 ? node.children[0] : undefined;
        if (node.tagName === "pre" && child?.type === "element" &&
          (child.properties.dataTrMath === true ||
            (child.tagName === "span" && Array.isArray(child.properties.className) && child.properties.className.includes("math-pending")))) {
          node = child;
        }
        if (node.tagName === "code" && node.properties.dataTrMath === true) {
          const tex = String(node.properties.dataTrMathTex), raw = String(node.properties.dataTrMathSource);
          const display = node.properties.dataTrMathDisplay === true;
          const rendered = renderFormula(tex, display, file);
          return { type: "element", tagName: "span",
            properties: { className: rendered ? ["formula", display ? "formula-display" : "formula-inline"] : ["math-source"] },
            children: rendered ? structuredClone(rendered) : [{ type: "text", value: raw }],
          } as Element;
        }
        visit(node);
        return node;
      }) as typeof parent.children;
    }
    visit(tree);
  };
};

const Formula = memo(function Formula({ raw, inlineOnly }: { raw: string; inlineOnly: boolean }) {
  return <ReactMarkdown
    remarkPlugins={[remarkMath, [remarkSafeMath, { source: raw, inlineOnly }]]}
    rehypePlugins={[rehypeSafeMath]}
    components={{ p: ({ children }) => <>{children}</> }}
  >{raw}</ReactMarkdown>;
});

/** Thinking and structured cells retain plain text apart from explicitly delimited math. */
export function MathText({ children, inlineOnly = false }: { children: string; inlineOnly?: boolean }) {
  const parts = useMemo(() => mathParts(children), [children]);
  return <span className="math-text">{parts.map((part) => part.kind === "math"
    ? <Formula key={part.start} raw={part.raw} inlineOnly={inlineOnly} />
    : <Fragment key={part.start}>{part.raw}</Fragment>)}</span>;
}

/** Do not copy both the hidden MathML annotation and visible glyphs into the outline. */
export function mathHeadingText(heading: HTMLElement) {
  const copy = heading.cloneNode(true) as HTMLElement;
  copy.querySelectorAll(".formula").forEach((formula) => {
    const tex = formula.querySelector("annotation")?.textContent;
    if (tex !== undefined && tex !== null) formula.replaceWith(document.createTextNode(`\\(${tex}\\)`));
  });
  return copy.textContent || "";
}
