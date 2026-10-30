import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ArrowDownOutlined, RightOutlined } from "@ant-design/icons";
import type { Message, NativeBlock, ReasoningBlock } from "./client";
import { MathText } from "./math";
import "./thinking.css";

export function nativeMessage(message: Message) {
  return message.blocks?.find((b) => b.type === "native") as NativeBlock | undefined;
}
export function reasoningMessage(message: Message) {
  return message.blocks?.find((b) => b.type === "reasoning") as ReasoningBlock | undefined;
}
export function thinkingParts(value: ReasoningBlock) {
  return (value.text.some((s) => s.trim()) ? value.text : value.summary).filter((s) => s.trim());
}
export type MessageGroup = { id: string; role: string; messages: Message[]; native?: NativeBlock };
export function groupMessages(messages: Message[]): MessageGroup[] {
  const groups: MessageGroup[] = [];
  for (const message of messages) {
    if (message.role === "assistant" && !message.content && !reasoningMessage(message)) continue;
    const meta = nativeMessage(message), previous = groups.at(-1);
    if (message.role === "assistant" && meta?.turnId && previous?.role === "assistant" &&
      previous.native?.sessionId === meta.sessionId && previous.native.turnId === meta.turnId) {
      previous.messages.push(message);
    } else groups.push({ id: message.id, role: message.role, native: meta, messages: [message] });
  }
  return groups.filter((g) => g.messages.some((m) => m.content ||
    (reasoningMessage(m) && thinkingParts(reasoningMessage(m)!).length)));
}

export function elapsedThinking(blocks: ReasoningBlock[], stamp: number, active: boolean) {
  const intervals = blocks.flatMap((b) => b.intervals).map((v) => [
    v.startedAt, v.finishedAt ?? (active ? stamp : v.startedAt),
  ]).sort((a, b) => a[0] - b[0]);
  let total = 0, end = 0;
  for (const [start, finish] of intervals) {
    total += Math.max(0, finish - Math.max(start, end));
    end = Math.max(end, finish);
  }
  return Math.floor(total / 1000);
}

export function Thinking({ blocks, complete, status }: {
  blocks: ReasoningBlock[]; complete: boolean; status?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [following, setFollowing] = useState(true);
  const [stamp, setStamp] = useState(Date.now());
  const view = useRef<HTMLDivElement>(null);
  const follow = useRef(true), frame = useRef(0), programmatic = useRef(false);
  const manual = useRef(false), lastScroll = useRef(0);
  const id = useId();
  const text = blocks.flatMap(thinkingParts);
  const signature = text.join("\n\n");
  const active = !complete && !status && blocks.some((b) => b.status === "streaming" &&
    b.intervals.some((i) => i.finishedAt === null));
  const seconds = elapsedThinking(blocks, stamp, active);
  const source = blocks.some((b) => b.text.some((t) => t.trim())) ? "思考文本" : "思考摘要";
  const reduced = useRef(false);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => { reduced.current = preference.matches; };
    change(); preference.addEventListener("change", change);
    return () => preference.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    if (!active) return;
    setStamp(Date.now());
    const timer = window.setInterval(() => setStamp(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  function setFollow(value: boolean) {
    follow.current = value; setFollowing(value);
  }
  function tail(instant = false) {
    const element = view.current;
    if (!element || !follow.current) return;
    cancelAnimationFrame(frame.current);
    const target = () => Math.max(0, element.scrollHeight - element.clientHeight);
    if (instant || reduced.current) { element.scrollTop = target(); return; }
    const from = element.scrollTop, start = performance.now();
    programmatic.current = true;
    const move = (now: number) => {
      if (!follow.current) { programmatic.current = false; return; }
      const fraction = Math.min(1, (now - start) / 145);
      element.scrollTop = from + (target() - from) * (1 - (1 - fraction) ** 3);
      if (fraction < 1) frame.current = requestAnimationFrame(move);
      else programmatic.current = false;
    };
    frame.current = requestAnimationFrame(move);
  }
  useLayoutEffect(() => { tail(); }, [signature]);
  useLayoutEffect(() => { tail(true); }, [expanded]);
  useEffect(() => {
    const element = view.current;
    if (!element) return;
    const observer = new ResizeObserver(() => tail(true));
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => { observer.disconnect(); cancelAnimationFrame(frame.current); };
  }, []);
  function browse() {
    if (!expanded) return;
    manual.current = true; lastScroll.current = view.current?.scrollTop ?? 0;
    cancelAnimationFrame(frame.current); programmatic.current = false; setFollow(false);
  }
  const collapsed = complete && !expanded;
  const label = complete ? (seconds ? `已思考 ${seconds} 秒` : "已思考") :
    status || (active ? "思考中" : "处理中");
  return <section className="thinking-block" data-status={complete ? "complete" : active ? "streaming" : "paused"}
    data-expanded={expanded} aria-label={source}>
    <div className="thinking-top">
      <button type="button" className="thinking-toggle" aria-label={`${expanded ? "收起" : "展开"}${source}`}
        aria-expanded={expanded} aria-controls={id} title={source}
        onClick={() => { manual.current = false; if (expanded) setFollow(true); setExpanded(!expanded); }}>
        <span className="thought-glyph" aria-hidden="true"><i /><i /><i /></span>
        <span>{label}</span>
        {!complete && seconds > 0 && <time className="thought-time">{String(Math.floor(seconds / 60)).padStart(2, "0")}:{String(seconds % 60).padStart(2, "0")}</time>}
        <span className="thought-chevron"><RightOutlined /></span>
      </button>
      {expanded && !following && <button type="button" className="follow-thought" aria-label="回到最新" onClick={() => {
        manual.current = false; setFollow(true); tail();
      }}><ArrowDownOutlined aria-hidden="true" /> 回到最新</button>}
    </div>
    <div className="thinking-frame">
      <div id={id} ref={view} className="thinking-window" role="region" aria-label={source + "内容"}
        aria-hidden={collapsed} inert={collapsed} tabIndex={expanded ? 0 : -1}
        onWheel={(event) => { if (event.deltaY < 0) browse(); }}
        onPointerDown={browse}
        onKeyDown={(event) => {
          if (["ArrowUp", "PageUp", "Home"].includes(event.key)) browse();
          else if (expanded && ["ArrowDown", "PageDown", "End"].includes(event.key)) {
            manual.current = true; lastScroll.current = view.current?.scrollTop ?? 0;
          }
        }}
        onScroll={(event) => {
          if (!expanded || programmatic.current || !manual.current) return;
          const el = event.currentTarget, delta = el.scrollTop - lastScroll.current;
          if (delta < -.5) setFollow(false);
          else if (delta > .5 && el.scrollHeight - el.clientHeight - el.scrollTop < 15) setFollow(true);
          lastScroll.current = el.scrollTop;
          event.stopPropagation();
        }}>
        <div className="thinking-log">{text.map((part, index) => <div className="thinking-part" key={index}><MathText>{part}</MathText></div>)}</div>
      </div>
    </div>
  </section>;
}
