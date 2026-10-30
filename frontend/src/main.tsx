import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import {
  AppstoreOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  MessageOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RightOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { Conversation } from "./chat";
import { Pages } from "./pages";
import { SettingsPage } from "./settings";
import { ErrorBox } from "./ui";
import { changed, useRead } from "./workspace-client";
import { HeadingContext } from "./shell-context";
import type { Heading } from "./shell-context";
import "./style.css";
import "./workspace.css";
import "./workspace-adapters.css";

const navigation = [
  { path: "/", key: "chat", label: "对话", icon: <MessageOutlined /> },
  {
    path: "/projects",
    key: "projects",
    label: "项目工作台",
    icon: <AppstoreOutlined />,
  },
  {
    path: "/runs",
    key: "runs",
    label: "运行中心",
    icon: <PlayCircleOutlined />,
  },
  {
    path: "/reports",
    key: "reports",
    label: "报告中心",
    icon: <FileTextOutlined />,
  },
];
function App() {
  const { pathname } = useLocation(),
    navigate = useNavigate();
  const [heading, setHeading] = useState<Heading>({
    path: "",
    title: "",
    section: "",
  });
  const chats =
    useRead<{ id: string; title: string; projectId: string | null }[]>(
      "/conversations",
    );
  const id = pathname.startsWith("/chats/")
    ? pathname.split("/")[2]
    : undefined;
  const isChat = pathname === "/" || pathname === "/chats" || !!id;
  const fallback = isChat
    ? "新对话"
    : pathname === "/settings"
      ? "设置"
      : navigation.find(
          (item) => item.path !== "/" && pathname.startsWith(item.path),
        )?.label || "TrustRepro";
  const current =
    heading.path === pathname
      ? heading
      : {
          title: fallback,
          section: isChat ? "chat" : pathname.split("/")[1],
          parent: undefined,
        };
  useEffect(() => {
    document.querySelector(".page-content")?.scrollTo(0, 0);
  }, [pathname]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        navigate("/");
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [navigate]);
  const projectCount = new Set(
    (chats.data || []).map((chat) => chat.projectId).filter(Boolean),
  ).size;
  return (
    <HeadingContext.Provider value={setHeading}>
      <div className="app-layout app-v2">
        <aside className="sidebar" aria-label="侧栏">
          <Link className="brand" to="/">
            <span className="brand-mark">
              <ExperimentOutlined />
            </span>
            <span>TrustRepro</span>
          </Link>
          <Link className="new-chat" to="/">
            <PlusOutlined />
            新对话<span className="key-hint">Ctrl K</span>
          </Link>
          <nav className="main-nav" aria-label="主要导航">
            {navigation.map((item) => (
              <Link
                key={item.key}
                to={item.path}
                className={`nav-item ${current.section === item.key ? "active" : ""}`}
                aria-current={current.section === item.key ? "page" : undefined}
              >
                {item.icon}
                <span>{item.label}</span>
                {item.key === "projects" && (
                  <span className="nav-count">{projectCount}</span>
                )}
              </Link>
            ))}
          </nav>
          <div className="recent-heading">
            <span>最近会话</span>
            <ClockCircleOutlined />
          </div>
          <nav className="recent-list" aria-label="最近会话">
            {chats.data?.map((chat) => (
              <Link
                key={chat.id}
                className={`recent ${id === chat.id ? "active" : ""}`}
                title={chat.title}
                to={`/chats/${chat.id}`}
              >
                <span className="dot" />
                <span>{chat.title}</span>
              </Link>
            ))}
            {chats.data?.length === 0 && (
              <span className="conversation-nav-empty">暂无会话</span>
            )}
          </nav>
          <ErrorBox error={chats.error} />
          <div className="sidebar-bottom">
            <Link
              className={`nav-item ${pathname === "/settings" ? "active" : ""}`}
              to="/settings"
            >
              <SettingOutlined />
              设置
            </Link>
          </div>
        </aside>
        <div className="main-shell shell">
          <header className="topbar">
            <div className="breadcrumbs">
              <span>{isChat ? "论文复现" : current.parent || "工作空间"}</span>
              <RightOutlined />
              <strong title={current.title}>{current.title}</strong>
            </div>
          </header>
          <main
            className={`page-content ${isChat ? "chat-content" : "management-page"}`}
          >
            {isChat ? (
              <Conversation
                key={id || "new"}
                id={id}
                changed={() => {
                  chats.reload();
                  changed();
                }}
              />
            ) : pathname === "/settings" ? (
              <SettingsPage />
            ) : (
              <div className="management-inner">
                <Pages key={pathname} path={pathname} />
              </div>
            )}
          </main>
        </div>
      </div>
    </HeadingContext.Provider>
  );
}
// Only TrustRepro's obsolete persisted demo/workspace state is removed.
for (const key of Object.keys(localStorage))
  if (/^trustrepro[.:-]/i.test(key)) localStorage.removeItem(key);
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#5e6ad2",
          colorText: "#394255",
          colorTextSecondary: "#8a94a6",
          colorBorder: "#e1e5ef",
          colorBgLayout: "#fafafa",
          borderRadius: 8,
          fontSize: 12,
          fontFamily:
            'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
          controlHeight: 32,
        },
        components: {
          Button: { primaryShadow: "none", fontWeight: 500 },
          Table: {
            headerBg: "#fafbfe",
            headerColor: "#9ca5b7",
            rowHoverBg: "#fcfcff",
            cellPaddingBlock: 17,
          },
          Tabs: { horizontalItemGutter: 26 },
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
