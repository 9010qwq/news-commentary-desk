"""Optional CI checks: blank browser plus a separate fixed-public-page diagnostic; no user data."""
import sys,tempfile,struct
from pathlib import Path
from .browser import Screenshots,failure_category
from .network import Fetcher
from .sources import parse_article

PUBLIC_SMOKE_URL='https://www.bjnews.com.cn/detail/1791448580169401.html'

def browser_smoke():
    def no_network(url):raise ValueError('Smoke test does not permit external navigation')
    with Screenshots(policy=no_network) as browser:
        executable=Path(browser.pw.chromium.executable_path).resolve()
        bundle_root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent)).resolve()
        page=browser.context.new_page()
        try:
            page.set_content('<!doctype html><html><body><h1>NewsDesk browser smoke</h1></body></html>')
            if page.locator('h1').inner_text()!='NewsDesk browser smoke':raise ValueError('Blank browser DOM check failed')
            return {'ok':True,'frozen':bool(getattr(sys,'frozen',False)),
                    'bundled_browser':executable.is_relative_to(bundle_root),
                    'executable_path':str(executable),'browser_version':browser.browser.version,
                    'external_navigation':False,'news_capture_verified':False}
        finally:page.close()

def public_source_smoke(scratch_dir=None):
    """One attempt using production fetch/parser/capture; never retain article bytes or image."""
    report={'ok':False,'status':'failed','url':PUBLIC_SMOKE_URL,'attempts':1,
            'frozen':bool(getattr(sys,'frozen',False)),'api_calls':0,'smtp_messages':0,
            'article_html_retained':False,'article_body_retained':False,'screenshot_retained':False,
            'screenshot_visually_reviewed':False,'full_daily_or_email_workflow_verified':False}
    fetcher=Fetcher()
    try:
        data,final=fetcher.get(PUBLIC_SMOKE_URL)
        article=parse_article(data,final,'Asia/Shanghai')
        if not (article['date']=='2026-10-08' and '陈广江' in article['author'] and '离加油站20米' in article['title']):
            raise ValueError('原页标题、作者或日期已变化，未确认是指定验收文章')
        report['article']={k:article[k] for k in ('url','title','author','media','published_at')}
        with tempfile.TemporaryDirectory(prefix='newsdesk-one-public-page-',dir=scratch_dir) as temp:
            image=Path(temp)/'original-page.png'
            with Screenshots(policy=fetcher.allowed) as browser:
                executable=Path(browser.pw.chromium.executable_path).resolve()
                bundle_root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent)).resolve()
                report['bundled_browser']=executable.is_relative_to(bundle_root)
                browser.capture(article,image)
            raw=image.read_bytes()
            if len(raw)<8000 or raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('原页截图不是有效的非空PNG')
            width,height=struct.unpack('>II',raw[16:24])
            if width<800 or height<600:raise ValueError('原页截图尺寸不足')
            report['screenshot']={'format':'PNG','width':width,'height':height,'bytes':len(raw),'retained':False}
        # TemporaryDirectory has removed the original-page screenshot before returning metadata.
        report.update(ok=True,status='passed')
    except Exception as error:
        report['error']=str(error)[:800] if isinstance(error,ValueError) else type(error).__name__+' during the one public-source attempt'
        if hasattr(error,'diagnostics'):report['diagnostics']=error.diagnostics
        diagnostic=report.get('diagnostics',{})
        category=diagnostic.get('error_category') or failure_category(error)
        report['error_category']=category
        safety_denial=(diagnostic.get('main_http_status') in {401,403,429}
                       or diagnostic.get('navigation_denial')=='ROBOTS_DENIED'
                       or category in {'SITE_ACCESS_CHALLENGE','ERR_BLOCKED_BY_ADMINISTRATOR','DESTINATION_POLICY_REFUSAL'}
                       or str(category).startswith('ERR_CERT')
                       or 'robots 规则禁止' in str(error)
                       or '媒体站点拒绝或限流' in str(error))
        report['safety_denial_confirmed']=safety_denial
        if safety_denial:report['status']='blocked'
    finally:fetcher.close()
    return report
