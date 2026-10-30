import { useState, useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Button,
  Input,
  Radio,
  Popover,
  Progress,
  Skeleton,
  Upload,
} from "antd";
import {
  ArrowUpOutlined,
  ClockCircleOutlined,
  PaperClipOutlined,
  WalletOutlined,
  PauseOutlined,
  CaretRightOutlined,
  AppstoreOutlined,
  ThunderboltOutlined,
  ExperimentOutlined,
} from "@ant-design/icons";
import { api, root, uuid, useIntent } from "./client";
import type { Chat, Question, ModelSelections, ConversationEvent, MessageDelta, ReasoningBlock } from "./client";
import { ErrorBox, Markdown } from "./ui";
import { mergeSnapshot, mergeDelta } from "./chat-state";
import { ModelSelectors } from "./settings";
import { CardPanel } from "./cards";
import { RunPanel } from "./runs";
import { ReportPanel } from "./reports";
import { ConversationWorkspace } from "./conversation-workspace";
import type { WorkspaceTab } from "./conversation-workspace";
import { useHeading } from "./shell-context";
import { Thinking, groupMessages, nativeMessage, reasoningMessage, thinkingParts } from "./thinking";
import { money } from "./money";

export function Conversation({
  id,
  changed,
}: {
  id?: string;
  changed: () => void;
}) {
  const navigate = useNavigate();
  const { hash } = useLocation();
  const draftKey = useRef(uuid());
  const draftId = useRef<string | undefined>(undefined);
  const messageIntent = useIntent();
  const controlIntent = useIntent();
  const [chat, setChat] = useState<Chat>();
  const [draftModels, setDraftModels] = useState<ModelSelections>();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [sending, setSending] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [revising, setRevising] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceFocus, setWorkspaceFocus] = useState(false);
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>("execution");
  const workspaceChoice = useRef(false);
  useHeading(chat?.title || "新对话", "chat");
  useEffect(() => {
    if (chat?.runs.length && !workspaceChoice.current) setWorkspaceOpen(true);
  }, [chat?.runs.length]);
  const applyChat = (c: Chat) => setChat((old) => mergeSnapshot(old, c));
  const working = sending || chat?.composerState === "working";
  const pausing = chat?.composerState === "pausing";
  const paused = chat?.composerState === "paused";
  const groups = groupMessages(chat?.messages ?? []);
  const progressLabel = pausing ? "正在暂停" : paused ? "已暂停" :
    chat?.automation?.state === "waiting_job" ? "等待作业" :
    chat?.automation?.state === "waiting_user" ? "等待输入" :
    chat?.automation?.state === "compacting" ? "整理上下文" :
    chat?.automation?.state === "recovering" ? "正在恢复" : undefined;
  const lastGroup = groups.at(-1);
  const progressInMessage = lastGroup?.role === "assistant" && (
    lastGroup.messages.some((m) => m.content && m.status === "streaming") ||
    lastGroup.native?.turnStatus !== "completed" && lastGroup.messages.some((m) => {
      const block = reasoningMessage(m);
      return block && thinkingParts(block).length;
    })
  );
  const history = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const task = useRef<HTMLDivElement>(null);
  const located = useRef(false);
  useEffect(() => {
    if (followBottom.current && history.current)
      history.current.scrollTop = history.current.scrollHeight;
  }, [chat]);
  useEffect(() => {
    const area = history.current;
    if (!area) return;
    const observer = new ResizeObserver(() => {
      if (followBottom.current) area.scrollTop = area.scrollHeight;
    });
    observer.observe(area);
    if (area.firstElementChild) observer.observe(area.firstElementChild);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (
      hash === "#task-card" &&
      chat?.card &&
      !revising &&
      task.current &&
      !located.current
    ) {
      setWorkspaceFocus(false);
      task.current.scrollIntoView({ block: "start" });
      followBottom.current = false;
      located.current = true;
    }
  }, [hash, chat?.card, revising]);
  const refresh = () =>
    id &&
    void api<Chat>("/conversations/" + id)
      .then((c) => {
        applyChat(c);
        setLoadError("");
      })
      .catch((e) => setLoadError(String(e)));
  useEffect(() => {
    setChat(undefined);
    setError("");
    if (!id) return;
    let disposed = false;
    let stream: EventSource | undefined;
    let loading: Promise<Chat | undefined> | undefined;
    let dirty = false;
    const fetchLatest = async () => {
      try {
        const value = await api<Chat>("/conversations/" + id);
        if (!disposed) {
          setLoadError("");
          applyChat(value);
        }
        return value;
      } catch (e) {
        if (!disposed) setLoadError(String(e));
      }
    };
    const load = () => {
      dirty = true;
      if (!loading)
        loading = (async () => {
          let latest: Chat | undefined;
          while (dirty && !disposed) {
            dirty = false;
            latest = await fetchLatest();
          }
          return latest;
        })().finally(() => {
          loading = undefined;
        });
      return loading;
    };
    void load().then((initial) => {
      if (disposed || !initial) return;
      stream = new EventSource(
        `${root}/conversations/${id}/events?after=${initial.eventCursor}`,
      );
      stream.onmessage = (event) => {
        const value = JSON.parse(event.data) as ConversationEvent;
        if (value.kind === "message.delta") {
          const m = value.payload as MessageDelta;
          setChat((c) => mergeDelta(c, value.id, { ...m, id: m.messageId }));
        } else void load();
      };
    });
    const timer = setInterval(() => void load(), 4000);
    return () => {
      disposed = true;
      stream?.close();
      clearInterval(timer);
    };
  }, [id]);
  async function ensure() {
    if (id) return id;
    if (draftId.current) return draftId.current;
    if (
      draftModels &&
      Object.values(draftModels).some((m) => !m.connectionId || !m.model)
    ) {
      throw new Error("请先在设置或新对话中选择研究与执行模型");
    }
    const c = await api<Chat>("/conversations", {
      clientConversationId: draftKey.current,
      models: draftModels,
    });
    draftId.current = c.id;
    return c.id;
  }
  async function send() {
    if (!text.trim() || sending || working || pausing) return;
    setSending(true);
    setRevising(!!chat?.card);
    setError("");
    try {
      const target = await ensure();
      const c = await api<Chat>(`/conversations/${target}/messages`, {
        clientMessageId: messageIntent.key({ content: text }),
        content: text,
      });
      messageIntent.done();
      applyChat(c);
      setText("");
      if (!id) navigate("/chats/" + c.id, { replace: true });
      changed();
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
      setRevising(false);
    }
  }
  async function control(action: "pause" | "resume") {
    if (!id || controlling) return;
    setControlling(true);
    setError("");
    try {
      applyChat(
        await api<Chat>(`/conversations/${id}/actions`, {
          action,
          clientRequestId: controlIntent.key({ action }),
        }),
      );
      controlIntent.done();
    } catch (e) {
      setError(String(e));
    } finally {
      setControlling(false);
    }
  }
  async function upload(file: File) {
    if (sending) return;
    setSending(true);
    setError("");
    try {
      const target = await ensure();
      const data = new FormData();
      data.append("file", file);
      const response = await fetch(
        `${root}/conversations/${target}/attachments`,
        { method: "POST", body: data },
      );
      if (!response.ok)
        throw new Error((await response.json()).message || "上传失败");
      changed();
      if (!id) navigate("/chats/" + target, { replace: true });
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  }
  return (
    <div
      className={`conversation-page content ${workspaceOpen ? "workspace-open" : ""} ${workspaceFocus ? "workspace-focus" : ""} ${!id ? "conversation-draft" : ""}`}
    >
      <section
        className="research-conversation chat-pane"
        aria-label="Agent 会话"
        inert={workspaceFocus}
      >
        <header className="conversation-topline">
          <strong className="conversation-title" title={chat?.title}>
            {chat?.title || "新对话"}
          </strong>
          {chat?.allowance && (
            <div className="inline-actions">
              <Popover
                trigger="click"
                placement="bottomRight"
                title="账户额度"
                content={
                  <div className="budget-popover">
                    <div className="info-row">
                      <span>估算费用</span>
                      <strong>
                        {money(chat.allowance.spentCost, 3)} / {money(chat.allowance.maxCost)}
                      </strong>
                    </div>
                    <Progress
                      percent={Math.min(
                        100,
                        ((chat.allowance.spentCost +
                          chat.allowance.reservedCost) /
                          chat.allowance.maxCost) *
                          100,
                      )}
                      showInfo={false}
                      size="small"
                    />
                    <div className="info-row">
                      <span>费用预留</span>
                      <span>{money(chat.allowance.reservedCost, 3)}</span>
                    </div>
                    <div className="info-row">
                      <span>
                        <ClockCircleOutlined /> 活跃时间
                      </span>
                      <span>
                        {(chat.allowance.elapsedSeconds / 60).toFixed(1)} /{" "}
                        {chat.allowance.maxHours * 60} 分钟
                      </span>
                    </div>
                  </div>
                }
              >
                <Button type="text" size="small" icon={<WalletOutlined />}>
                  额度
                </Button>
              </Popover>
            </div>
          )}
          <Button
            size="small"
            className="workspace-toggle"
            icon={<AppstoreOutlined />}
            aria-expanded={workspaceOpen}
            onClick={() => {
              workspaceChoice.current = true;
              setWorkspaceOpen(!workspaceOpen);
              setWorkspaceFocus(false);
            }}
          >
            工作区
          </Button>
        </header>
        <div
          className="conversation-history"
          ref={history}
          tabIndex={0}
          aria-label="会话记录"
          onScroll={(e) => {
            const area = e.currentTarget;
            followBottom.current =
              area.scrollHeight - area.scrollTop - area.clientHeight < 40;
          }}
        >
          <div className="messages">
            {!id && (
              <div className="conversation-draft-title">
                <span className="empty-mark">
                  <ExperimentOutlined />
                </span>
                <h1>你好！我是你的科研小助手，我可以帮你什么？</h1>
              </div>
            )}
            {id && !chat && !loadError && (
              <Skeleton active paragraph={{ rows: 5 }} />
            )}
            <ErrorBox error={loadError || chat?.error || ""} />
            {groups.map((group, groupIndex) => {
              const m = group.messages[0];
              const thoughts = group.messages.map(reasoningMessage).filter(
                (value): value is ReasoningBlock => !!value && thinkingParts(value).length > 0,
              );
              const complete = group.messages.every((message) =>
                nativeMessage(message)?.turnStatus === "completed");
              const interrupted = group.messages.some((message) =>
                nativeMessage(message)?.turnStatus === "interrupted");
              const state = groupIndex === groups.length - 1 ? progressLabel : undefined;
              return <article className={"chat-message role-" + group.role} key={group.id}>
                  <div className="chat-message-meta">
                    {m.role !== "user" && (
                      <span className="agent-avatar">
                        <ThunderboltOutlined />
                      </span>
                    )}
                    <strong>{m.role === "user" ? "你" : "Agent"}</strong>
                    {group.native?.model && <span className="message-model" title={group.native.model}>{group.native.model}</span>}
                    {m.createdAt && (
                      <time>
                        {new Date(m.createdAt).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </time>
                    )}
                  </div>
                  {thoughts.length > 0 && <Thinking blocks={thoughts} complete={complete}
                    status={complete ? undefined : state || (interrupted ? "已中断" : undefined)} />}
                  {group.messages.filter((message) => message.content).map((message) =>
                    <Markdown key={message.id} artifacts={chat?.reports.flatMap((r) => r.artifacts)}>
                      {message.content}
                    </Markdown>)}
                  {group.messages.some((message) => message.content && message.status === "streaming") && (
                    <span className="streaming-state">
                      <span className="status-dot pulse" />
                      正在回复
                    </span>
                  )}
                </article>;
            })}
            {(working || pausing || paused) &&
              !progressInMessage && (
                <article
                  className="chat-message role-assistant"
                  aria-live="polite"
                >
                  <div className="chat-message-meta">
                    <strong>Agent</strong>
                  </div>
                  <div className="thinking-state">
                    {working && (
                      <span className="thinking-dots" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    )}
                    {progressLabel || "思考中"}
                  </div>
                </article>
              )}
            {chat?.card &&
              !revising &&
              !(chat.card.status === "ready" && text.trim()) && (
                <div id="task-card" ref={task}>
                  <CardPanel
                    key={chat.card.id}
                    card={chat.card}
                    chatId={chat.id}
                    onChange={applyChat}
                  />
                </div>
              )}
            <div className="inline-cards">
              {chat?.runs
                .slice()
                .sort((a, b) => b.createdAt - a.createdAt)
                .slice(0, 1)
                .map((r) => (
                  <RunPanel key={r.id} run={r} onChange={refresh} compact />
                ))}
              {chat?.reports
                .slice()
                .sort((a, b) => b.createdAt - a.createdAt)
                .slice(0, 1)
                .map((r) => (
                  <ReportPanel key={r.id} report={r} reload={refresh} compact />
                ))}
            </div>
            {chat?.questions?.map((question) => (
              <QuestionPanel
                key={question.id}
                value={question}
                changed={refresh}
              />
            ))}
          </div>
        </div>
        <form
          className="chat-composer composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <ErrorBox error={error} />
          <Input.TextArea
            aria-label="消息"
            placeholder={
              id ? "继续讨论，或调整任务" : "输入论文、链接或复现目标"
            }
            autoSize={{ minRows: 2, maxRows: 8 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="chat-composer-actions">
            <div className="inline-actions">
              <Upload
                accept=".pdf,application/pdf"
                showUploadList={false}
                disabled={working || pausing}
                beforeUpload={(file) => {
                  void upload(file);
                  return false;
                }}
              >
                <Button
                  type="text"
                  icon={<PaperClipOutlined />}
                  disabled={working || pausing}
                  aria-label="添加论文"
                  title="添加论文"
                ></Button>
              </Upload>
              {(!id || chat) && (
                <ModelSelectors
                  chat={chat}
                  draft={draftModels}
                  onDraftChange={setDraftModels}
                  disabled={sending || pausing}
                  changed={applyChat}
                />
              )}
            </div>
            {working || pausing ? (
              <Button
                type="primary"
                shape="circle"
                aria-label="暂停"
                title="暂停"
                icon={<PauseOutlined />}
                loading={controlling || pausing}
                disabled={sending || pausing}
                onClick={() => void control("pause")}
              />
            ) : (paused ||
                chat?.automation?.allowedActions.includes("resume")) &&
              !text.trim() ? (
              <Button
                type="primary"
                icon={<CaretRightOutlined />}
                shape="circle"
                aria-label="继续"
                loading={controlling}
                onClick={() => void control("resume")}
              ></Button>
            ) : (
              <Button
                type="primary"
                htmlType="submit"
                icon={<ArrowUpOutlined />}
                shape="circle"
                aria-label="发送"
                loading={sending}
                disabled={!text.trim()}
              ></Button>
            )}
          </div>
        </form>
      </section>
      <ConversationWorkspace
        chat={chat}
        open={workspaceOpen}
        focus={workspaceFocus}
        tab={workspaceTab}
        onTab={setWorkspaceTab}
        onClose={() => {
          workspaceChoice.current = true;
          setWorkspaceOpen(false);
          setWorkspaceFocus(false);
        }}
        onFocus={() => setWorkspaceFocus(!workspaceFocus)}
      />
    </div>
  );
}

function QuestionPanel({
  value,
  changed,
}: {
  value: Question;
  changed: () => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const intent = useIntent();
  async function submit() {
    setBusy(true);
    setError("");
    try {
      const body = {
        answers: Object.fromEntries(
          value.questions.map((q) => [q.id, { answers: [answers[q.id]] }]),
        ),
      };
      await api(`/questions/${value.id}/answer`, {
        ...body,
        clientRequestId: intent.key(body),
      });
      intent.done();
      changed();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="panel chat-task-card">
      <div className="panel-heading">
        <h2>{value.blocking ? "需要你的决定" : "补充信息"}</h2>
      </div>
      <div className="panel-body">
        {value.questions.map((q) => (
          <div key={q.id} className="recovery-form">
            <label>{q.question}</label>
            {q.options && (
              <Radio.Group
                value={answers[q.id]}
                onChange={(e) =>
                  setAnswers({ ...answers, [q.id]: e.target.value })
                }
              >
                {q.options.map((o) => (
                  <Radio key={o.label} value={o.label} title={o.description}>
                    {o.label}
                  </Radio>
                ))}
              </Radio.Group>
            )}
            <Input.TextArea
              aria-label={q.header || q.question}
              placeholder="输入你的回答"
              value={answers[q.id] || ""}
              onChange={(e) =>
                setAnswers({ ...answers, [q.id]: e.target.value })
              }
              autoSize={{ minRows: 1, maxRows: 4 }}
            />
          </div>
        ))}
        <ErrorBox error={error} />
        <Button
          type="primary"
          loading={busy}
          aria-label="提交回答"
          aria-busy={busy}
          disabled={!value.questions.every((q) => answers[q.id]?.trim())}
          onClick={() => void submit()}
        >
          提交回答
        </Button>
      </div>
    </article>
  );
}
