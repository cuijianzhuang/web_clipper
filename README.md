# Web Clipper Backend

一个强大的网页剪藏后端服务，支持将网页保存到 GitHub Pages 并同步到 Notion 数据库，同时通过 Telegram 发送通知。

## 特性

- 🚀 支持上传 HTML 文件到 GitHub Pages
- 📚 自动同步到 Notion 数据库
- 🤖 使用 AI (GPT) 自动生成摘要和标签
- 📱 通过 Telegram 发送剪藏通知
- 🔒 API 密钥认证
- ⚡ FastAPI 高性能后端
- 🔄 自动重试机制
- 📝 详细的日志记录

## 安装

1. 克隆仓库：

```bash
git clone https://github.com/yourusername/web-clipper-backend.git
cd web-clipper-backend
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```
3. 配置服务：

复制 `config.example.py` 到 `config.py` 并填写配置：

```python
CONFIG = {
'github_repo': 'username/repo', # GitHub 仓库
'github_token': 'your-github-token', # GitHub 访问令牌
'github_pages_domain': 'https://username.github.io', # GitHub Pages 域名
'notion_database_id': 'your-database-id', # Notion 数据库 ID
'notion_token': 'your-notion-token', # Notion 集成令牌
'telegram_token': 'your-telegram-bot-token', # Telegram Bot 令牌
'telegram_chat_id': 'your-chat-id', # Telegram 聊天 ID
'api_key': 'your-api-key', # API 访问密钥
'port': 8000, # 服务端口
# AI 服务配置（二选一）
# OpenAI 配置
'ai_provider': 'openai',
'openai_api_key': 'your-openai-key',
'openai_model': 'gpt-3.5-turbo',
# 或 Azure OpenAI 配置
'ai_provider': 'azure',
'azure_api_key': 'your-azure-key',
'azure_api_base': 'https://your-resource.openai.azure.com/',
'azure_deployment_name': 'your-deployment-name',
}
```

## 配置说明

### GitHub 配置
1. 创建一个 GitHub 仓库（可以是私有的）
2. 开启 GitHub Pages（设置为从 main 分支构建）
3. 生成 GitHub 访问令牌（需要 repo 权限）

### Notion 配置
1. 创建一个新的 Notion 数据库，包含以下字段：
   - Title (标题)
   - OriginalURL (URL)
   - SnapshotURL (URL)
   - Summary (Text)
   - Tags (Multi-select)
   - Created (Date)
2. 创建 Notion 集成并获取令牌
3. 将集成添加到数据库

### Telegram 配置
1. 通过 @BotFather 创建新的 Bot
2. 获取 Bot Token
3. 获取聊天 ID

## 使用方法

1. 启动服务：

```bash
python web_clipper.py
```

2. 发送请求：

```bash
curl -X POST "http://localhost:8000/upload" \
     -H "Authorization: Bearer your-api-key" \
     -F "file=@webpage.html" \
     -F "url=https://original-url.com"
```

## API 文档

### 上传接口

- 端点：`/`, `/upload`, `/upload/`
- 方法：POST
- 认证：Bearer Token
- 参数：
  - file: HTML 文件
  - url: 原始网页 URL（可选）
- 响应：
```json
{
    "status": "success",
    "github_url": "https://...",
    "notion_url": "https://..."
}
```

## 本地操作
1. 浏览器安装 singlefile 插件 https://chromewebstore.google.com/detail/singlefile/mpiodijhokgodhhofbcjdecpffjipkle
2. 配置插件（只需要配置一次，支持云端同步）： 
	1. 文件名-模版： {url-host}{url-pathname-flat}.{filename-extension}   
	2. 文件名-最大长度：   384字符
	3. 文件名-替换字符：$
	4. 保存位置-保存到 REST 表单 API-网址: 你的服务器 ip，自行解决端口访问问题
	5. 保存位置-保存到 REST 表单 API-授权令牌： 第二步里面配置的 Bearer Key
	6. 保存位置-保存到 REST 表单 API-文件字段名称： singlehtmlfile
	7. 保存位置-保存到 REST 表单 API-网址字段名称： url
3. 保存
4. [Notion 模板](https://www.notion.so/cuiplus/19f32fd5f34e805a9001f2e38fc4ac74?v=19f32fd5f34e810eb20f000c0956c3b9&pvs=4)

## 注意事项

1. 确保 GitHub Pages 已正确配置
2. Notion 数据库需要包含所有必需字段
3. Telegram Bot 需要先与用户建立对话
4. API 密钥需要在请求头中使用 Bearer 认证

## 错误处理

服务会自动处理常见错误：
- GitHub 上传失败会自动重试
- 部署等待超时会继续处理
- AI 生成失败会使用默认值

## 日志

服务会记录详细的操作日志，包括：
- 上传进度
- GitHub Pages 部署状态
- AI 生成结果
- Notion 同步状态
- Telegram 通知发送

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！