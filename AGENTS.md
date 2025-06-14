# AGENTS.md - AI 代理开发指南

本文档为参与 xiaomai-bot 项目开发的 AI 代理提供核心指导。

## 项目概览

xiaomai-bot 是基于 Graia Ariadne 框架的 QQ 机器人，采用模块化插件架构。

### 核心技术栈
- **框架**: Graia Ariadne (QQ 机器人框架)
- **Python**: >=3.10, <3.13
- **数据库**: SQLite + SQLAlchemy (异步 ORM)
- **依赖管理**: uv
- **插件系统**: Saya

### 项目结构
```
xiaomai-bot/
├── core/                   # 核心框架代码
│   ├── bot.py             # 主控制器 (Umaru 类)
│   ├── config.py          # 配置模型
│   ├── control.py         # 权限控制
│   └── orm/               # 数据库模型
├── modules/               # 插件目录
│   ├── required/          # 必需插件 (权限管理、帮助等)
│   ├── self_contained/    # 内置插件 (AI聊天、战地一等)
│   └── third_party/       # 第三方插件
├── utils/                 # 工具库
├── config/               # 配置文件
└── main.py               # 启动入口
```

## 开发规范

### 代码风格
- **格式化**: 使用 Ruff (88字符行长度，双引号)
- **规范检查**: Ruff Linter (E, F, W, UP 规则集)
- **提交前**: 运行 `pre-commit run --all-files`

### 依赖管理
- **安装**: `uv sync`
- **添加**: `uv add <package>`
- **移除**: `uv remove <package>`

### 分支与提交
- **分支命名**: `type/description` (如 `feat/ai-chat`, `fix/bf1-bug`)
- **提交格式**: `type(scope): description` (遵循 Conventional Commits)
- **类型**: feat, fix, docs, style, refactor, test, chore

## 插件开发

### 插件结构
每个插件需要：
1. **metadata.json**: 插件元信息
2. **__init__.py**: 主要逻辑
3. **事件监听**: 使用 `@channel.use(ListenerSchema(...))`

### 核心 API
- **配置访问**: `config = create(GlobalConfig)`
- **数据库操作**: `await orm.fetch_one()`, `await orm.insert()`
- **消息发送**: `await app.send_message(target, MessageChain(...))`
- **权限检查**: `Permission.require()`, `Function.require()`

### 主要功能模块
- **AI 聊天**: 支持 OpenAI/DeepSeek，工具调用，多模态
- **战地一**: 玩家查询、服务器管理、统计分析
- **图片功能**: 识图、随机图片、头像趣图、风格生成
- **管理功能**: 权限管理、插件管理、群管理

## 数据库
- **模型定义**: `core/orm/tables.py`
- **迁移**: 使用 Alembic (`alembic revision --autogenerate`)
- **操作**: 通过 `core.orm.orm` 封装的异步方法

## 测试与部署
- **本地运行**: `uv run main.py`
- **Docker**: `docker build -t xiaomai-bot .`
- **配置**: 复制 config/config_demo.yaml 为 config/config.yaml

## 注意事项
- 新功能优先考虑插件形式实现
- 重大变更前先创建 Issue 讨论
- 确保 CI 检查通过后再合并 PR
- 遵循项目的权限和功能开关机制
