from __future__ import annotations
from pathlib import Path
import os,re,time
from urllib.parse import urlsplit
from .network import public_url,Fetcher
from .config import source_of
from .sources import clean

def failure_category(error):
    if isinstance(getattr(error,'diagnostics',None),dict) and error.diagnostics.get('error_category'):return error.diagnostics['error_category']
    message=str(error)
    code=re.search(r'net::(ERR_[A-Z0-9_]+)',message)
    if code:return code.group(1)
    if any(marker in message for marker in ('拒绝本机','媒体链接必须使用公开域名','不安全的网络地址','只接受无账号信息')):return 'DESTINATION_POLICY_REFUSAL'
    if 'unsafe-eval' in message or 'Content Security Policy' in message:return 'CSP_EVALUATION_BLOCKED'
    if 'waiting for fonts' in message.lower() and 'fonts loaded' not in message.lower():return 'FONT_READINESS_TIMEOUT'
    if 'Timeout' in type(error).__name__ or 'Timeout ' in message:return 'TIMEOUT'
    return type(error).__name__

class CaptureError(ValueError):
    def __init__(self,message,diagnostics):
        super().__init__(message);self.diagnostics=dict(diagnostics)

BODY_SELECTORS={'cctv':'#text_area','bjnews':'#contentStr','people':'#ozoom','xinhua':'#detailContent','thepaper':'[class*="cententWrap"]'}

def visible_article_ready(page,article):
    body=page.locator('body')
    if not body.count():return False
    visible=clean(body.inner_text(timeout=1500));title=clean(article['title'])
    title_visible=title[:14] in visible or title[-14:] in visible
    if not title_visible and any(marker in visible for marker in ('验证码','请完成验证','验证您是人类','Access Denied','Checking your browser')):
        raise ValueError('原站显示访问验证或拒绝页面，已停止；未尝试完成验证')
    if not title_visible or len(visible)<150:return False
    matches=page.locator(BODY_SELECTORS.get(article['source_id'],'article, main, [role="main"]'))
    for i in range(min(matches.count(),10)):
        loc=matches.nth(i)
        if loc.is_visible() and len(clean(loc.inner_text(timeout=1500)))>=100:return True
    return False

def wait_for_visible_article(page,article,diagnostics,timeout_seconds=35):
    deadline=time.monotonic()+timeout_seconds
    while time.monotonic()<deadline:
        if isinstance(diagnostics.get('main_http_status'),int) and diagnostics['main_http_status']>=400:
            raise ValueError('原页访问被拒绝（HTTP '+str(diagnostics['main_http_status'])+'），已停止')
        try:
            if visible_article_ready(page,article):return
        except ValueError:
            diagnostics['error_category']='SITE_ACCESS_CHALLENGE';raise
        except Exception as error:
            if 'Timeout' not in type(error).__name__:raise
        page.wait_for_timeout(250)
    diagnostics['error_category']='ARTICLE_READINESS_TIMEOUT'
    raise ValueError('原页可见标题/正文在限定时间内未就绪；未把加载壳当截图')

class Screenshots:
    """Original website browser captures only. No HTML replacement or synthetic article cards."""
    def __init__(self,policy=None):self.policy=policy;self.fetcher=None;self.diagnostics={}
    def __enter__(self):
        try:
            if self.policy is None:self.fetcher=Fetcher();self.policy=self.fetcher.allowed
            from playwright.sync_api import sync_playwright
            self.pw=sync_playwright().start()
            executable=os.environ.get('NEWSDESK_BROWSER_PATH')
            self.browser=self.pw.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
            self.context=self.browser.new_context(viewport={'width':1440,'height':1000},device_scale_factor=1,
                                                  locale='zh-CN',timezone_id='Asia/Shanghai',accept_downloads=False,
                                                  service_workers='block')
            self.context.route('**/*',self.route)
            self.context.route_web_socket('**/*',lambda ws:ws.close())
            return self
        except Exception:
            if hasattr(self,'pw'):self.pw.stop()
            raise ValueError('截图浏览器未就绪。请运行“安装与启动.cmd”完成 Chromium 安装') from None
    def route(self,route):
        request=route.request
        try:
            if request.url.startswith(('data:','blob:')):return route.continue_()
            public_url(request.url)
            if request.is_navigation_request() and request.frame==request.frame.page.main_frame:
                source_of(request.url);self.policy(request.url)
            # No media playback or writes to publishers while archiving.
            if request.resource_type in {'media','websocket'} or request.method not in {'GET','HEAD'}:return route.abort()
            route.continue_()
        except Exception as error:
            self.diagnostics['route_denials']=self.diagnostics.get('route_denials',0)+1
            try:
                if request.is_navigation_request() and request.frame==request.frame.page.main_frame:
                    self.diagnostics['navigation_denial']='ROBOTS_DENIED' if 'robots' in str(error).lower() and '禁止' in str(error) else failure_category(error)
            except Exception:pass
            route.abort()
    def capture(self,article,path):
        source_of(article['url']);public_url(article['url'])
        started=time.monotonic();self.diagnostics={'stage':'navigate','main_http_status':None,'route_denials':0,'request_failures':{}}
        page=self.context.new_page()
        def response_status(response):
            try:
                if response.request.is_navigation_request() and response.request.frame==page.main_frame:self.diagnostics['main_http_status']=response.status
            except Exception:pass
        def request_failed(request):
            code=failure_category(RuntimeError(request.failure or 'request_failed'))
            failures=self.diagnostics['request_failures'];failures[code]=failures.get(code,0)+1
        page.on('response',response_status);page.on('requestfailed',request_failed)
        try:
            # A successful main response plus visible article content is sufficient.
            # Do not wait for unrelated advertising/tracking scripts to finish loading.
            response=page.goto(article['url'],wait_until='commit',timeout=35000)
            self.diagnostics['main_http_status']=response.status if response else None
            if response is None or response.status>=400:raise ValueError(f'原页访问失败（HTTP {response.status if response else "unknown"}），未生成截图')
            self.diagnostics['stage']='visible_article'
            wait_for_visible_article(page,article,self.diagnostics)
            page.wait_for_timeout(800)
            # Scroll naturally to load lazy images without changing page content.
            self.diagnostics['stage']='scroll_lazy_images'
            height=page.evaluate('document.documentElement.scrollHeight')
            if height>40000:raise ValueError('原页过长，超过安全截图上限；请人工核对原文')
            for y in range(0,min(height,20000),900):page.evaluate('(y)=>window.scrollTo(0,y)',y);page.wait_for_timeout(70)
            page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(300)
            self.diagnostics['stage']='final_visible_article'
            wait_for_visible_article(page,article,self.diagnostics,timeout_seconds=5)
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            self.diagnostics['stage']='screenshot'
            page.screenshot(path=str(path),full_page=True,timeout=30000)
            self.diagnostics['stage']='validate_png'
            if path.stat().st_size<8000:raise ValueError('截图文件过小，需人工复核')
            return str(page.url)
        except Exception as error:
            self.diagnostics.setdefault('error_category',failure_category(error))
            self.diagnostics['elapsed_seconds']=round(time.monotonic()-started,2)
            detail=str(error) if isinstance(error,ValueError) else '浏览器截图失败：'+self.diagnostics['stage']+' / '+self.diagnostics['error_category']
            raise CaptureError(detail+'；未绕过站点验证或安全限制',self.diagnostics) from None
        finally:page.close()
    def __exit__(self,*args):
        self.context.close();self.browser.close();self.pw.stop()
        if self.fetcher:self.fetcher.close()
