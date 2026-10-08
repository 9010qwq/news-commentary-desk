from __future__ import annotations
from pathlib import Path
import os
from urllib.parse import urlsplit
from .network import public_url,Fetcher
from .config import source_of
from .sources import clean

class Screenshots:
    """Original website browser captures only. No HTML replacement or synthetic article cards."""
    def __init__(self,policy=None):self.policy=policy;self.fetcher=None
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
        except Exception:route.abort()
    def capture(self,article,path):
        source_of(article['url']);public_url(article['url'])
        page=self.context.new_page()
        try:
            response=page.goto(article['url'],wait_until='domcontentloaded',timeout=35000)
            if response is None or response.status>=400:raise ValueError('原页访问失败，未生成截图')
            page.wait_for_timeout(2200)
            title=clean(article['title']);short=title[:14]
            visible=clean(page.locator('body').inner_text(timeout=6000))
            if len(visible)<150 or (short not in visible and title[-14:] not in visible):raise ValueError('原页标题/正文未正常显示，未把空壳当截图')
            selectors={'cctv':'#text_area','bjnews':'#contentStr','people':'#ozoom','xinhua':'#detailContent','thepaper':'[class*="cententWrap"]'}
            matches=page.locator(selectors.get(article['source_id'],'article, main, [role="main"]'));visible_body=False
            if matches.count():
                for i in range(min(matches.count(),10)):
                    loc=matches.nth(i)
                    if loc.is_visible() and len(clean(loc.inner_text()))>=100:visible_body=True;break
            if not visible_body:raise ValueError('未识别到可见的原页正文；未把标题/加载壳当截图')
            # Scroll naturally to load lazy images without changing page content.
            height=page.evaluate('document.documentElement.scrollHeight')
            if height>40000:raise ValueError('原页过长，超过安全截图上限；请人工核对原文')
            for y in range(0,min(height,20000),900):page.evaluate('(y)=>window.scrollTo(0,y)',y);page.wait_for_timeout(70)
            page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(300)
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(path),full_page=True,timeout=30000)
            if path.stat().st_size<8000:raise ValueError('截图文件过小，需人工复核')
            return str(page.url)
        except ValueError:raise
        except Exception:raise ValueError('浏览器截图失败或超时；未尝试绕过站点验证或安全限制') from None
        finally:page.close()
    def __exit__(self,*args):
        self.context.close();self.browser.close();self.pw.stop()
        if self.fetcher:self.fetcher.close()
