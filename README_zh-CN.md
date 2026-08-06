# Open Chat Studio

<!-- hy-mt2-i18n:start -->
[English](./README.md) | **中文** | [日本語](./README_ja.md) | [Español](./README_es.md)
<!-- hy-mt2-i18n:end -->

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio) [![codecov](https://codecov.io/github/dimagi/open-chat-studio/graph/badge.svg?token=SUKZMAWM3O)](https://codecov.io/github/dimagi/open-chat-studio)

Open Chat Studio 是一个用于构建、部署和评估人工智能驱动聊天应用的平台。它提供了处理各类大型语言模型、创建聊天机器人、管理对话以及与不同消息传递平台进行集成的工具。

[用户文档](https://docs.openchatstudio.com) | [开发者文档](https://developers.openchatstudio.com/)

## 贡献方式

我们欢迎大家为 Open Chat Studio 做出贡献！如果您有兴趣参与，欢迎查看我们的[贡献指南](https://developers.openchatstudio.com/contributing/)，以获取更多入门信息。

## 技术栈
- **后端：** Python 3.13+、Django、Django REST Framework、Celery
- **数据库：** PostgreSQL（支持pgvector扩展）
- **缓存/消息中间件：** Redis
- **前端：** TypeScript、CSS（[Tailwind](http://tailwindcss.com/) + [DaisyUI](https://daisyui.com/)）、HTMX、Alpine.js、webpack，以及部分组件使用的[ReactJS](https://react.dev/)与[React Flow](https://reactflow.dev/)
- **大语言模型集成：** OpenAI、Anthropic、Groq、Gemini、Azure等
- **部署：** Docker、Heroku

## 快速启动设置

Open Chat Studio 使用 [UV](https://docs.astral.sh/uv/getting-started/installation/) 和 [Invoke](https://www.pyinvoke.org/) 来实现开发自动化。

### 先决条件

- Python 3.13（推荐版本）
- Node.js >= 24.0.0
- Docker 及 Docker Compose

### 设置

```bash
git clone https://github.com/dimagi/open-chat-studio.git
cd open-chat-studio
uv venv --python 3.13
source.venv/bin/activate
uv sync
inv setup-dev-env   # 安装钩子脚本、启动服务、迁移数据库、构建前端界面、创建超级用户
./manage.py runserver
```

在另一个终端中运行 Celery——这是进行 LLM 交互所必需的：

```bash
inv celery
```

如需包含手动操作步骤、环境配置及故障排除在内的完整设置指南，请参阅[本地开发环境搭建指南](https://developers.openchatstudio.com/getting-started/local-setup/)。

## 仅基于 Docker 的开发环境

作为在主机上运行 Django 和 Celery 的替代方案，您可以将整个技术栈都放在 Docker 中运行——无需安装本地的 Python 或 Node。

```bash
cp.env.example.env   # 至少需设置SECRET_KEY
docker compose build
docker compose up
```

如需完整的设置指南、可用服务、实用命令及故障排除方法，请参阅[Docker 开发环境设置指南](https://developers.openchatstudio.com/getting-started/docker-setup/)。

## 部署

要将您自己的生产环境实例部署到 Heroku：

[![部署](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/dimagi/open-chat-studio)

## 获取帮助

- **错误报告与功能需求：**[如何提供反馈](https://developers.openchatstudio.com/contributing/)
- **开发者文档：**[developers.openchatstudio.com](https://developers.openchatstudio.com/)
- **用户文档：**[docs.openchatstudio.com](https://docs.openchatstudio.com)
