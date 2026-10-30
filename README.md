<div align="center">

# TrustRepro

一个可以在本机运行实验的 AI 论文复现工作台。

![Version](https://img.shields.io/badge/version-0.2.7-7864D9?style=flat-square)
![Windows](https://img.shields.io/badge/desktop-Windows%2011%20x64-0078D4?style=flat-square)
![CPU](https://img.shields.io/badge/compute-CPU-64748B?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-19-149ECA?style=flat-square&logo=react&logoColor=white)
![Tauri](https://img.shields.io/badge/Tauri-2-24C8D8?style=flat-square&logo=tauri&logoColor=white)

[项目介绍](#about) · [安装使用](#desktop) · [本地开发](#development) · [模型配置](#models) · [打包发布](#packaging)

</div>

<a id="about"></a>

## 从一篇论文开始

读完论文，想验证其中的方法，往往还要花时间找代码、配环境，再把结果整理成能看懂的图表。TrustRepro 把这些工作放进同一个对话里。你可以上传论文，也可以给出链接，先和 Agent 讨论想复现什么，以及手头的电脑适合跑多大的实验。

方案确定后，工作台会给出一张任务卡，列明实验参数、计算资源和预算。你确认开始，Agent 就会进入这个项目的 Linux 沙箱，编写和运行代码。执行过程中可以查看命令与输出，也可以暂停任务，稍后接着做。

每次运行都有自己的工作区。实验留下的代码、图表和日志可以随选定产物一起保存，报告则支持 Markdown、JSON 和 ZIP 产物包下载，方便继续阅读或交给别人查看。研究阶段和执行阶段还可以分别选择模型，沿用各自的对话上下文。

目前的版本主要用于 **CPU 小规模实验**，适合课程项目、论文方法验证和算法演示。工作台面向电脑浏览器，同时提供 Windows 安装版。具体能复现到什么程度，取决于论文提供的信息、所选模型和批准的计算资源。

<a id="desktop"></a>

## 在 Windows 上使用

拿到 `TrustRepro_*_x64-setup.exe` 后，运行安装程序，再打开 TrustRepro。第一次启动会准备本地运行环境，接着进入设置页；填好模型服务地址、密钥，并选定研究和执行模型，就可以开始第一段对话。安装包的生成方式见[打包发布](#packaging)。

安装版面向启用硬件虚拟化的 **Windows 11 x64**。它带有专属 WSL2 环境所需的运行资源，Docker Engine、数据库和 WebView2 也由程序负责准备。如果电脑还缺少 WSL，启动页会引导安装系统组件；遇到重启提示，按提示重启后再打开应用即可继续。

> [!TIP]
> 想把项目数据放到容量更大的磁盘，可以在“设置 → 数据目录”里选择新位置并迁移。默认位置是 `%LOCALAPPDATA%\TrustReproData`。

关闭窗口会收起到托盘，正在执行的任务仍会继续。需要结束本次使用时，从托盘选择“暂停任务并退出”。升级前也请先停止正在执行的任务，已有的模型配置和项目数据会随升级保留。

<a id="development"></a>

## 本地开发

下面的命令以 **PowerShell** 为例，都在仓库根目录执行。

### 准备环境

| 工具 | 要求 |
| --- | --- |
| Docker | Docker Desktop 的 Linux 容器模式，或具备 Compose 的 Linux Docker Engine |
| Python | 3.12，使用 uv 管理依赖 |
| Node.js | 24 |
| pnpm | 11.17.0 |

前端开发服务器运行在主机上，API、Worker 和实验沙箱运行在 Linux 容器中。PostgreSQL 17 与固定版本的 Codex app-server 已包含在镜像构建流程里。

先复制一份本地配置：

```powershell
Copy-Item .env.example .env
```

按[模型配置](#models)填写自己的服务信息。也可以先启动工作台，再到设置页保存连接。

### 启动服务

构建镜像并启动后端：

```powershell
docker compose --env-file .env -f infra/compose.yaml --profile backend --profile build build
docker compose --env-file .env -f infra/compose.yaml --profile backend up -d --no-build
```

第一次构建会下载镜像和依赖，耗时取决于网络。服务启动时会自动准备数据卷并执行数据库迁移。等后端就绪后，另开一个终端启动前端：

```powershell
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend dev
```

在浏览器打开 **http://127.0.0.1:5173**。API 地址是 `http://127.0.0.1:8000/api/v1`，接口文档位于 `http://127.0.0.1:8000/docs`。

## 模型配置与费用

TrustRepro 连接兼容 **Responses 协议**的模型服务。一个提供商可以保存多个模型，研究和执行各自选择默认项。只读的模型列表查询用于发现可用名称；点击兼容性测试或开始研究、实验后，会产生实际模型调用。

`.env` 中的连接信息用于首次初始化。工作台已有配置后，后续调整在设置页完成。

| 配置项 | 填写内容 |
| --- | --- |
| `TRUSTREPRO_MODEL_BASE_URL` | 模型服务地址，例如 `http://host.docker.internal:8317/v1` |
| `TRUSTREPRO_MODEL_API_KEY` | 你的模型服务密钥 |
| `TRUSTREPRO_MODEL_NAME` | 服务实际提供的模型名称 |
| `TRUSTREPRO_MODEL_PRICE_CURRENCY` | 填写 `CNY` |
| `TRUSTREPRO_MODEL_INPUT_COST_PER_MILLION` | 输入价格，单位为元／百万 token |
| `TRUSTREPRO_MODEL_OUTPUT_COST_PER_MILLION` | 输出价格，单位为元／百万 token |

开发版从 Docker Desktop 容器访问 Windows 主机服务时，地址使用 `host.docker.internal`。这里的 `localhost` 指向容器自身，与 Windows 安装版的自动地址转换有所区别。使用原生 Linux Docker 时，请填写容器能够访问到的服务地址。

## 修改代码

前端的生产构建命令如下，输出放在 `frontend/dist/`：

```powershell
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
```

修改后端接口后，可以从实际 API 定义重新生成前端类型，避免两边字段不同步：

```powershell
uv sync --project backend --frozen
uv run --project backend python scripts/export_openapi.py
pnpm --dir frontend generate:api
pnpm --dir frontend build
```

这一步会生成 `var/openapi.json`，并更新 `frontend/src/lib/api.d.ts`。后端静态检查使用 `uv run --project backend ruff check backend/src`；需要让容器运行新代码时，重新执行前面的 Compose 构建和启动命令。

<a id="packaging"></a>

## 打包发布

Windows 安装包在 Windows x64 机器上构建。在开发环境的基础上，还需要 Rust MSVC 工具链、Visual Studio Build Tools 的“使用 C++ 的桌面开发”组件，以及 Windows SDK。构建时保持 Docker Engine 开启，并使用 Linux 容器。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\windows\build.ps1
```

脚本会完成前端编译和镜像构建，把 WSL 根文件系统与应用资源一起打包，再用 Tauri 和 NSIS 生成安装程序。Microsoft WebView2 安装程序会在构建时下载并检查签名。首次打包需要联网，也要为镜像、Rust 编译和临时副本留出磁盘空间。

完成后可以在这里找到安装包与校验文件：

```text
var/windows/installer/TrustRepro_<版本>_x64-setup.exe
var/windows/installer/SHA256SUMS
```

<details>
<summary>复用已有构建结果</summary>

`-BundleOnly` 只生成运行环境资源。已有当前版本的镜像时，可以使用 `-SkipImages`；已有前端构建产物时，可以使用 `-SkipFrontend`。第一次从源码打包，直接执行上面的完整命令即可。

</details>

## 代码

| 路径 | 主要内容 |
| --- | --- |
| [`backend/src/trustrepro/`](backend/src/trustrepro/) | API、业务服务、Agent、Worker 与沙箱管理 |
| [`backend/migrations/`](backend/migrations/) | 数据库前向迁移 |
| [`frontend/src/`](frontend/src/) | React、TypeScript 与 Ant Design 工作台 |
| [`frontend/public/`](frontend/public/) | 前端静态资源 |
| [`infra/docker/`](infra/docker/) | 后端、科研沙箱和网络出口镜像 |
| [`infra/windows/`](infra/windows/) | Tauri 桌面入口、WSL 运行环境与打包脚本 |
| [`scripts/`](scripts/) | API 定义导出工具 |

科研镜像预装了常用的 CPU 科学计算库，具体版本记录在 [`scientific-requirements.txt`](infra/docker/scientific-requirements.txt)。Agent 可以直接使用兼容的环境；遇到论文指定版本或专用依赖时，再按批准的方案安装。每次运行的可写目录独立，数据库、任务状态和费用记录由后端管理。

这份源码包含开发运行和构建安装包需要的文件，依赖通过 uv 和 pnpm 安装，生成的文件统一放入忽略目录。Tauri 安装器随附的[第三方许可](infra/windows/src-tauri/installer.LICENSE-MIT)也会进入最终安装包。

## 反馈与改进

如果遇到问题，欢迎在仓库 Issue 中描述操作步骤，附上应用版本、所用模型和相关日志；分享前请去掉其中的密钥与个人信息。涉及实验结果时，论文链接、任务卡参数和实际输出能帮助更快定位原因。

想参与开发的话，可以从具体的使用问题开始。提交改动时写清楚它解决了什么、怎样验证，涉及配置或命令的变化也请一并更新这份 README。
