import os
import time
import requests
from github import Github
import openai
from notion_client import Client
import telegram
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Header, Request, Body
import uvicorn
import shutil
from pathlib import Path
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
import secrets
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from config import CONFIG  # 添加这行在文件开头
from bs4 import BeautifulSoup  # 添加到导入部分
import html2text
from contextlib import asynccontextmanager

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 设置 httpx 日志级别为 WARNING，隐藏请求日志
logging.getLogger("httpx").setLevel(logging.WARNING)

# 定义全局变量
handler = None
UPLOAD_DIR = Path("uploads")

# 配置限制
MAX_FILE_SIZE = CONFIG.get('max_file_size', 10 * 1024 * 1024)  # 从配置中获取最大文件大小
ALLOWED_EXTENSIONS = set(CONFIG.get('allowed_extensions', ['.html', '.htm']))
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

# 替换原来的 API_KEY_NAME 和 api_key_header
security = HTTPBearer()

# 添加 lifespan 函数定义
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用程序生命周期管理器
    """
    # 启动时执行
    global handler
    handler = WebClipperHandler(CONFIG)
    UPLOAD_DIR.mkdir(exist_ok=True)
    
    # 如果配置中没有 API key，生成一个
    if 'api_key' not in CONFIG:
        CONFIG['api_key'] = secrets.token_urlsafe(32)
        logger.info(f"Generated new API key: {CONFIG['api_key']}")
    
    yield
    
    # 关闭时执行的清理代码（如果需要的话）
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)

# 创建应用和限速器
app = FastAPI(lifespan=lifespan)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """验证 Bearer 令牌"""
    token = credentials.credentials
    if token != CONFIG.get('api_key'):
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token

def verify_file(file: UploadFile):
    """验证文件"""
    # 检查文件扩展名
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # 检查文件大小
    file.file.seek(0, 2)  # 移到文件末尾
    size = file.file.tell()  # 获取文件大小
    file.file.seek(0)  # 重置文件指针
    
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size allowed: {MAX_FILE_SIZE/1024/1024}MB"
        )

def parse_filename(filename):
    """从文件名解析URL
    filename format: {random_prefix}_url.html (其中url中的/被替换为$)
    """
    try:
        # 移除 .html 后缀
        name_without_ext = filename.rsplit('.', 1)[0]
        
        # 移除随机前缀（如果存在）
        if '_' in name_without_ext:
            name_without_ext = name_without_ext.split('_', 1)[1]
        
        # 恢复URL中的斜杠
        original_url = name_without_ext.replace('$', '/')
        
        logger.info(f"从文件名解析出原始URL: {original_url}")
        return {
            'original_url': original_url
        }
    except Exception as e:
        logger.error(f"解析文件名失败: {str(e)}")
        return {
            'original_url': ''
        }

class WebClipperHandler:
    def __init__(self, config):
        self.config = config
        self.github_client = Github(config['github_token'])
        self.notion_client = Client(auth=config['notion_token'])
        self.telegram_bot = telegram.Bot(token=config['telegram_token'])
        
        # 配置 AI 服务
        self.ai_provider = config.get('ai_provider', 'openai').lower()
        
        if self.ai_provider == 'azure':
            # Azure OpenAI 配置
            openai.api_type = "azure"
            openai.api_key = config['azure_api_key']
            openai.api_base = config['azure_api_base']
            openai.api_version = config.get('azure_api_version', '2024-02-15-preview')
            logger.info(f"使用 Azure OpenAI API: {config['azure_api_base']}")
        else:
            # 标准 OpenAI 配置
            openai.api_key = config['openai_api_key']
            if 'openai_base_url' in config:
                openai.base_url = config['openai_base_url']
                logger.info(f"使用自定义 OpenAI API URL: {config['openai_base_url']}")

    async def process_file(self, file_path: Path, original_url: str = ''):
        """处理上传的文件"""
        try:
            logger.info("🔄 开始处理新的网页剪藏...")
            
            # 1. 上传到 GitHub Pages
            filename, github_url = self.upload_to_github(str(file_path))
            logger.info(f"📤 GitHub 上传成功: {github_url}")

            # Github URL 转换为 Markdown
            md_content = self.url2md(github_url)
            
            # 2. 获取页面标题
            title = self.get_page_content_by_md(md_content)
            logger.info(f"📑 页面标题: {title}")
            
            # 如果没有提供原始 URL，则从文件名解析
            if not original_url:
                file_info = parse_filename(filename)
                original_url = file_info['original_url']
            
            # 3. 生成摘要和标签
            summary, tags = self.generate_summary_tags(md_content)
            logger.info(f"📝 摘要: {summary[:100]}...")
            logger.info(f"🏷️ 标签: {', '.join(tags)}")
            
            # 4. 保存到 Notion
            notion_url = self.save_to_notion({
                'title': title,
                'original_url': original_url,
                'snapshot_url': github_url,
                'summary': summary,
                'tags': tags,
                'created_at': time.time()
            })
            logger.info(f"📓 Notion 保存成功")
            
            # 5. 发送 Telegram 通知
            notification = (
                f"✨ 新的网页剪藏\n\n"
                f"📑 {title}\n\n"
                f"📝 {summary}\n\n"
                f"🔗 原始链接：{original_url}\n"
                f"📚 快照链接：{github_url}"
            )
            await self.send_telegram_notification(notification)
            
            logger.info("=" * 50)
            logger.info("✨ 网页剪藏处理完成!")
            logger.info(f"📍 原始链接: {original_url}")
            logger.info(f"🔗 GitHub预览: {github_url}")
            logger.info(f"📚 Notion笔记: {notion_url}")
            logger.info("=" * 50)
            
            return {
                "status": "success",
                "github_url": github_url,
                "notion_url": notion_url
            }
            
        except Exception as e:
            error_msg = f"❌ 处理失败: {str(e)}"
            logger.error(error_msg)
            logger.error("=" * 50)
            await self.send_telegram_notification(error_msg)
            raise

    def upload_to_github(self, html_path):
        """上传 HTML 文件到 GitHub Pages"""
        filename = os.path.basename(html_path)
        max_retries = 5
        retry_delay = 3  # 秒
        
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for attempt in range(max_retries):
            try:
                repo = self.github_client.get_repo(self.config['github_repo'])
                file_path = f"clips/{filename}"
                
                try:
                    # 检查文件是否已存在
                    existing_file = repo.get_contents(file_path)
                    logger.info(f"文件已存在，更新内容: {file_path}")
                    repo.update_file(
                        file_path,
                        f"Update web clip: {filename}",
                        content,
                        existing_file.sha,
                        branch="main"
                    )
                except Exception:
                    logger.info(f"创建新文件: {file_path}")
                    repo.create_file(
                        file_path,
                        f"Add web clip: {filename}",
                        content,
                        branch="main"
                    )
                
                github_url = f"https://{self.config['github_pages_domain']}/{self.config['github_repo'].split('/')[1]}/clips/{filename}"
                logger.info(f"📑 GitHub URL: {github_url}")
                
                # 等待 GitHub Pages 部署
                max_deploy_retries = self.config.get('github_pages_max_retries', 60)
                deploy_retry_interval = 5  # 秒
                
                logger.info(f"等待 GitHub Pages 部署 (最多 {max_deploy_retries * deploy_retry_interval} 秒)...")
                start_time = time.time()
                
                for deploy_attempt in range(max_deploy_retries):
                    try:
                        response = requests.get(
                            github_url,
                            timeout=10,
                            verify=True,
                            headers={'Cache-Control': 'no-cache'}
                        )
                        
                        if response.status_code == 200:
                            elapsed_time = time.time() - start_time
                            logger.info(f"✅ GitHub Pages 部署完成! 耗时: {elapsed_time:.1f} 秒")
                            return filename, github_url
                        
                        if deploy_attempt % 6 == 0:  # 每30秒输出一次等待信息
                            elapsed_time = time.time() - start_time
                            logger.info(f"⏳ 正在等待部署... ({elapsed_time:.1f} 秒)")
                        
                        time.sleep(deploy_retry_interval)
                        
                    except requests.RequestException as e:
                        if deploy_attempt % 6 == 0:
                            logger.warning(f"部署检查失败 ({deploy_attempt + 1}/{max_deploy_retries}): {str(e)}")
                        time.sleep(deploy_retry_interval)
                        continue
                
                logger.warning("⚠️ GitHub Pages 部署超时，但继续处理...")
                return filename, github_url
                
            except Exception as e:
                logger.warning(f"GitHub 上传尝试 {attempt + 1}/{max_retries} 失败: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"❌ GitHub 上传最终失败: {str(e)}")
                    raise

    def url2md(self, url, max_retries=30):
        """将 URL 转换为 Markdown"""
        try:
            for attempt in range(max_retries):
                try:
                    md_url = f"https://r.jina.ai/{url}"
                    response = requests.get(md_url)
                    if response.status_code == 200:
                        md_content = response.text
                        return md_content
                except Exception:
                    time.sleep(10)
        except Exception:
            md_content = self.get_page_content_by_bs(url)
            return md_content

    def generate_summary_tags(self, content):
        """使用 AI 生成摘要和标签"""
        try:
            messages = [{
                "role": "user",
                "content": """请为以下网页内容生成简短摘要和相关标签。

要求：
1. 无论原文是中文还是英文，都必须用中文回复
2. 摘要控制在100字以内
3. 生成3-5个中文标签
4. 严格按照以下格式返回：

摘要：[100字以内的中文摘要]
标签：tag1，tag2，tag3，tag4，tag5

网页内容：
""" + content[:5000] + "..."
            }]

            if self.ai_provider == 'azure':
                client = openai.AzureOpenAI(
                    api_key=self.config['azure_api_key'],
                    api_version=self.config.get('azure_api_version', '2024-02-15-preview'),
                    azure_endpoint=self.config['azure_api_base']
                )
                response = client.chat.completions.create(
                    model=self.config['azure_deployment_name'],
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1000
                )
            else:
                client = openai.OpenAI(
                    api_key=self.config['openai_api_key'],
                    base_url=self.config.get('openai_base_url')
                )
                response = client.chat.completions.create(
                    model=self.config.get('openai_model', 'gpt-3.5-turbo'),
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1000
                )

            result = response.choices[0].message.content
            
            try:
                # 使用更严格的解析逻辑
                parts = result.split('\n')
                summary_part = next(p for p in parts if '摘要：' in p)
                tags_part = next(p for p in parts if '标签：' in p)
                
                summary = summary_part.split('摘要：', 1)[1].strip()
                tags_str = tags_part.split('标签：', 1)[1].strip()
                
                # 处理标签
                tags = [
                    tag.strip()
                    for tag in tags_str.replace('，', ',').split(',')
                    if tag.strip() and len(tag.strip()) <= 20  # 限制标签长度
                ]
                
                # 确保至少有一个标签
                if not tags:
                    tags = ["未分类"]
                
                # 记录生成的结果
                logger.info("AI 生成结果:")
                logger.info(f"摘要: {summary}")
                logger.info(f"标签: {', '.join(tags)}")
                
                return summary, tags
                
            except Exception as e:
                logger.error(f"解析 AI 响应失败: {str(e)}")
                logger.error(f"AI 原始响应: {result}")
                return "无法解析摘要", ["未分类"]
            
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"API 响应: {e.response}")
            return "无法生成摘要", ["未分类"]

    def save_to_notion(self, data):
        """保存到 Notion 数据库"""
        try:
            tags = data.get('tags', [])
            if not tags:
                tags = ["未分类"]
            
            current_time = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', 
                                       time.gmtime(data['created_at']))
            
            properties = {
                "Title": {"title": [{"text": {"content": data['title']}}]},
                "OriginalURL": {"url": data['original_url'] if data['original_url'] else None},
                "SnapshotURL": {"url": data['snapshot_url']},
                "Summary": {"rich_text": [{"text": {"content": data['summary']}}]},
                "Tags": {"multi_select": [{"name": tag} for tag in tags if tag.strip()]},
                "Created": {"date": {"start": current_time}}
            }
            
            response = self.notion_client.pages.create(
                parent={"database_id": self.config['notion_database_id']},
                properties=properties
            )
            
            return response['url']
            
        except Exception as e:
            logger.error(f"保存到 Notion 失败: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"Notion API 响应: {e.response.text}")
            raise

    def get_page_content_by_md(self, md_content):
        """从 markdown 获取标题"""
        lines = md_content.splitlines()
        for line in lines:
            if line.startswith("Title:"):
                return line.replace("Title:", "").strip()
        return "未知标题"

    def get_page_content_by_bs(self, url, max_retries=60):
        """从部署的页面获取标题和内容"""
        for attempt in range(max_retries):
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # 获取标题
                    title = None
                    if soup.title:
                        title = soup.title.string
                    if not title and soup.h1:
                        title = soup.h1.get_text(strip=True)
                    if not title:
                        for tag in ['h2', 'h3', 'h4', 'h5', 'h6']:
                            if soup.find(tag):
                                title = soup.find(tag).get_text(strip=True)
                                break
                    
                    # 清理标题
                    # if title:
                    #     title = ' '.join(title.split())
                    #     title = re.sub(r'\s*[-|]\s*.*$', '', title)
                    # else:
                    #     title = os.path.basename(url)
                    
                    # 提取正文内容
                    # for script in soup(["script", "style"]):
                    #     script.decompose()
                    
                    # text = soup.get_text()
                    # lines = (line.strip() for line in text.splitlines())
                    # chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                    # text = ' '.join(chunk for chunk in chunks if chunk)

                    # 提取正文内容
                    html2markdown = html2text.HTML2Text()
                    html2markdown.ignore_links = True
                    html2markdown.ignore_images = True
                    content = html2markdown.handle(soup.prettify())
                    
                    return f"Title: {title} \n\n {content}"
                    
                time.sleep(5)
                
            except Exception:
                time.sleep(5)
        
        return os.path.basename(url), ""

    async def send_telegram_notification(self, message):
        """发送 Telegram 通知"""
        await self.telegram_bot.send_message(
            chat_id=self.config['telegram_chat_id'],
            text=message
        )

@app.post("/")  # 支持根路径
@app.post("/upload")  # 支持不带斜杠的 /upload
@app.post("/upload/")  # 保持原有的 /upload/
@limiter.limit("10/minute", key_func=get_remote_address)
async def upload_file(
    request: Request,
    token: str = Depends(verify_token)
):
    """文件上传接口"""
    try:
        form = await request.form()
        original_url = form.get('url', '')
        
        # 获取文件内容
        file = None
        for field_name, field_value in form.items():
            if hasattr(field_value, 'filename') and hasattr(field_value, 'read'):
                file = field_value
                break
        
        if not file:
            raise HTTPException(
                status_code=400,
                detail="No file content found in form data"
            )
        
        filename = file.filename
        content = await file.read()
        
        # 验证和保存文件
        file_ext = Path(filename).suffix.lower()
        if not file_ext:
            filename += '.html'
        elif file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
            )
        
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size allowed: {MAX_FILE_SIZE/1024/1024}MB"
            )
        
        # 保存文件
        safe_filename = f"{secrets.token_hex(8)}_{filename}"
        file_path = UPLOAD_DIR / safe_filename
        
        with open(file_path, "wb") as f:
            f.write(content)
        
        try:
            result = await handler.process_file(file_path, original_url)
            return result
        finally:
            if file_path.exists():
                file_path.unlink()
                
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"上传失败: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

def start_server(host="0.0.0.0", port=8000):
    """启动服务器"""
    logger.info(f"Starting server on {host}:{port}")
    logger.info(f"Upload endpoints: /, /upload, /upload/")
    logger.info(f"API Key required in Bearer token")
    uvicorn.run(app, host=host, port=port)