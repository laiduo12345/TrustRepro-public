import type { Nodes, Root, RootContent } from "mdast";
import type { Plugin } from "unified";

// Raw CJK sentence punctuation ends an implicit URL; Chinese path characters
// and percent-encoded punctuation remain valid. Explicit Markdown links win.
export function bareUrlEnd(value: string) {
  const index = value.search(/[，。；：！？、（）【】「」『』《》〈〉“”‘’]/u);
  return index < 0 ? value.length : index;
}

export const remarkChineseLinks: Plugin<[{ source: string }], Root> = function ({ source }) {
  return (tree) => {
    function visit(node: Nodes) {
      if (!("children" in node)) return;
      const children = node.children as RootContent[];
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        const start = child.position?.start.offset;
        const end = child.position?.end.offset;
        if (child.type === "link" && start !== undefined && end !== undefined) {
          const raw = source.slice(start, end);
          if (/^(?:https?:\/\/|www\.)/i.test(raw) &&
              child.children.length === 1 && child.children[0].type === "text") {
            const label = child.children[0].value;
            const stop = bareUrlEnd(label);
            if (stop < label.length) {
              const urlStop = bareUrlEnd(child.url);
              child.url = child.url.slice(0, urlStop);
              child.children[0].value = label.slice(0, stop);
              children.splice(i + 1, 0, { type: "text", value: label.slice(stop) });
              i++;
            }
          }
        }
        visit(child);
      }
    }
    visit(tree);
  };
};
