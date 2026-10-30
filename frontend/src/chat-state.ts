import type { Chat, Message } from "./client";

export function mergeSnapshot(old: Chat | undefined, value: Chat): Chat {
  return !old || old.id !== value.id || value.eventCursor >= old.eventCursor
    ? value
    : old;
}

export function mergeDelta(
  chat: Chat | undefined,
  cursor: number,
  message: Message,
): Chat | undefined {
  if (!chat || cursor <= chat.eventCursor) return chat;
  return {
    ...chat,
    eventCursor: cursor,
    messages: chat.messages.some((m) => m.id === message.id)
      ? chat.messages.map((m) =>
          m.id === message.id ? { ...m, ...message } : m,
        )
      : [...chat.messages, message],
  };
}
