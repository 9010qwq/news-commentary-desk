"""Offline regression tests; no real API charges, browser fetches, or email."""
import json,tempfile,threading,unittest,zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from newsdesk.config import Settings,source_of
from newsdesk.store import Store
from newsdesk.vault import Vault
from newsdesk.scheduler import due_jobs,week_range,next_schedule
from newsdesk.sources import parse_article,links,title_hash,canonical
from newsdesk.network import Fetcher
from newsdesk.exporter import export_package,days,safe_cell,safe_filename
from newsdesk.mailer import send_package
from newsdesk.main import create_app
from newsdesk.collector import collect_day
from newsdesk.smoke import browser_smoke,public_source_smoke

def article(url='https://www.bjnews.com.cn/detail/123.html',title='测试新闻 | 新京报快评',author=''):
    return ('<html><head><meta charset="utf-8"></head><body><div class="bodyTitle"><h1>'+title+'</h1></div><div class="timer">2026-10-08 16:36</div><div class="reporter">编辑：甲乙</div><div id="contentStr">'+('<p>这是一段关于公共事件的评论正文，并对公共政策展开分析，明确表达观点。</p>'*8)+(f'<p>撰稿 / {author}</p>' if author else '')+'</div></body></html>').encode()

class SourceTests(unittest.TestCase):
    def test_allowlist(self):self.assertEqual(source_of('https://paper.people.com.cn/rmrb/a.html'),'people')
    def test_lookalike_not_builtin(self):
        self.assertEqual(source_of('https://news.cn.evil.example/x'),'custom')
    def test_userinfo_block(self):
        with self.assertRaises(ValueError):source_of('https://me:password@news.cn/x')
    def test_private_block(self):
        with self.assertRaises(ValueError):source_of('http://127.0.0.1/x')
    def test_editor_not_author(self):self.assertEqual(parse_article(article(),'https://www.bjnews.com.cn/detail/123.html')['author'],'未署名')
    def test_empty_page_controlled(self):
        with self.assertRaises(ValueError):parse_article(b' ','https://www.example.org/a')
    def test_explicit_author(self):self.assertEqual(parse_article(article(author='陈某（媒体人）'),'https://www.bjnews.com.cn/detail/123.html')['author'],'陈某（媒体人）')
    def test_publication_date(self):self.assertEqual(parse_article(article(),'https://www.bjnews.com.cn/detail/123.html')['published_at'],'2026-10-08T16:36:00+08:00')
    def test_date_missing(self):
        with self.assertRaises(ValueError):parse_article(article().replace(b'2026-10-08 16:36',b''),'https://www.bjnews.com.cn/detail/123.html')
    def test_news_not_labelled(self):self.assertEqual(parse_article(article(title='今天一项新闻报道已经发布'),'https://www.bjnews.com.cn/detail/123.html')['classification'],'')
    def test_tracking_removed(self):self.assertEqual(canonical('https://news.cn/a?utm_source=x&id=2#x'),'https://news.cn/a?id=2')
    def test_labels_dedup(self):self.assertEqual(title_hash('马上评｜同一标题'),title_hash('新华时评丨同一标题'))
    def test_robots_denied(self):
        def handler(r):return httpx.Response(200,text='User-agent: *\nDisallow: /private')
        f=Fetcher(httpx.MockTransport(handler),False)
        with self.assertRaises(ValueError):f.get('https://www.news.cn/private/a')
        f.close()
    def test_redirect_robots_denied(self):
        def handler(r):
            if r.url.path=='/robots.txt':return httpx.Response(200,text='User-agent: *\nDisallow: /private')
            return httpx.Response(302,headers={'Location':'/private/a'})
        f=Fetcher(httpx.MockTransport(handler),False)
        with self.assertRaises(ValueError):f.get('https://www.news.cn/start')
        f.close()
    def test_redirect_domain_block(self):
        def handler(r):return httpx.Response(404) if r.url.path=='/robots.txt' else httpx.Response(302,headers={'Location':'http://localhost/private'})
        f=Fetcher(httpx.MockTransport(handler),False)
        with self.assertRaises(ValueError):f.get('https://www.news.cn/start')
        f.close()
    def test_custom_public_media(self):self.assertEqual(source_of('https://www.example.org/opinion/123'),'custom')
    def test_custom_local_block(self):
        with self.assertRaises(ValueError):source_of('https://printer.local/a')
    def test_custom_site_name_not_trusted(self):
        data=article().replace(b'<head>',b'<head><meta property="og:site_name" content="../../evil">').replace(b'id="contentStr"',b'role="main"').replace(b'<div role="main">',b'<article>').replace(b'</div></body>',b'</article></body>')
        a=parse_article(data,'https://www.example.org/article/1');self.assertEqual(a['media'],'用户来源 · www.example.org');self.assertIn('页面自述',a['original_source'])
    def test_malformed_next_data_ignored(self):
        data=article().replace(b'</body>',b'<script id="__NEXT_DATA__">{"props":{"pageProps":"bad"}}</script></body>')
        self.assertEqual(parse_article(data,'https://www.bjnews.com.cn/detail/1.html')['date'],'2026-10-08')
    def test_malformed_jsonld_controlled(self):
        data=article().replace(b'id="contentStr"',b'role="main"').replace(b'<div role="main">',b'<article>').replace(b'</div></body>',b'</article></body>')
        data=data.replace(b'</body>',b'<script type="application/ld+json">{"@type":"NewsArticle","datePublished":{},"author":{"name":{}}}</script></body>')
        self.assertEqual(parse_article(data,'https://www.example.org/article/1')['author'],'未署名')

class TimeTests(unittest.TestCase):
    def settings(self):return Settings(auto_collect=True,weekly_email_enabled=True,smtp_host='smtp.example.com',smtp_username='a',smtp_from_addr='a@example.com',smtp_to_addr='b@example.com')
    def test_week_boundaries(self):self.assertEqual(week_range(datetime(2026,10,8,6,tzinfo=timezone.utc)),{'start':'2026-10-01','end':'2026-10-07','delivery_date':'2026-10-08'})
    def test_before_weekly(self):self.assertFalse(due_jobs(self.settings(),datetime(2026,10,8,5,59,59,tzinfo=timezone.utc)))
    def test_at_weekly(self):self.assertEqual(due_jobs(self.settings(),datetime(2026,10,8,6,tzinfo=timezone.utc))[0][1]['kind'],'weekly')
    def test_weekly_always_beijing(self):
        s=self.settings();s.timezone='America/New_York';self.assertTrue(any(j['kind']=='weekly' for _,j in due_jobs(s,datetime(2026,10,8,6,tzinfo=timezone.utc))))
    def test_no_friday_catchup_mail(self):self.assertFalse(any(j['kind']=='weekly' for _,j in due_jobs(self.settings(),datetime(2026,10,9,6,tzinfo=timezone.utc))))
    def test_future_schedule(self):self.assertTrue(next_schedule(self.settings(),datetime(2026,10,8,6,tzinfo=timezone.utc))['weekly'].startswith('2026-10-15'))
    def test_range_reject(self):
        with self.assertRaises(ValueError):days('2026-10-08','2026-10-01')

class StoreTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def record(self,status='failed'):
        a=parse_article(article(author='陈某'),'https://www.bjnews.com.cn/detail/123.html');a.pop('body')
        return self.s.add(a|{'captured_at':'','screenshot_status':status,'screenshot_path':'','status':'截图失败','error':'测试错误'})
    def test_persistence(self):
        self.record();other=Store(self.tmp.name);self.assertEqual(len(other.articles('2026-10-08')),1);other.db.close()
    def test_week_dedup(self):
        a=self.record();self.assertTrue(self.s.duplicate('https://www.bjnews.com.cn/detail/456.html',a['title_hash'],'2026-10-13'))
    def test_body_dedup(self):
        a=self.record();self.assertTrue(self.s.duplicate('https://www.bjnews.com.cn/detail/456.html','different','2026-10-13',a['body_hash']))
    def test_formula_injection(self):self.assertEqual(safe_cell('=CMD()'),"'=CMD()")
    def test_zip_component_sanitized(self):self.assertNotIn('/',safe_filename('../../evil\\name:stuff'))
    def test_export_shortfall(self):
        self.record();p=export_package(self.s,'2026-10-08','2026-10-08');self.assertEqual(p['shortfalls'][0]['shortfall'],5)
        with zipfile.ZipFile(p['files'][1]['path']) as z:
            manifest=json.loads(z.read('manifest.json'));self.assertEqual(manifest['items'][0]['screenshot_status'],'failed');self.assertFalse(any(n.endswith('.png') for n in z.namelist()))
        wb=load_workbook(p['files'][0]['path']);self.assertEqual(wb['新闻时评']['C2'].value,'陈某');self.assertTrue(wb['新闻时评']['E2'].hyperlink)
    def test_missing_file_not_success(self):
        self.record('ok');p=export_package(self.s,'2026-10-08','2026-10-08');self.assertEqual(p['counts']['screenshots'],0)
    def test_api_cap(self):
        self.s.consume_api('2026-10-08',1)
        with self.assertRaises(ValueError):self.s.consume_api('2026-10-08',1)
    def test_atomic_mail_claim(self):
        result=[];threads=[threading.Thread(target=lambda:result.append(self.s.claim_send('same'))) for _ in range(8)]
        [t.start() for t in threads];[t.join() for t in threads];self.assertEqual(sum(result),1)
    def test_secret_binding(self):
        v=Vault(self.tmp.name);s=Settings();v.put('api','test-secret',s);self.assertEqual(v.get('api',s),'test-secret');s.api_base_url='https://other.example.com';self.assertFalse(v.get('api',s))
    def test_email_defaults_off(self):
        with self.assertRaises(ValueError):send_package(self.s,Vault(self.tmp.name),'2026-10-01','2026-10-07',{})
    def test_mock_send_once(self):
        s=Settings(smtp_host='smtp.example.com',smtp_username='a',smtp_from_addr='a@example.com',smtp_to_addr='b@example.com');self.s.save_settings(s);v=Vault(self.tmp.name);v.put('smtp','test-secret',s)
        calls=[]
        class SMTP:
            def __init__(self,**kw):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def ehlo(self):pass
            def login(self,*a):pass
            def sendmail(self,*a):calls.append(a);return {}
        p=export_package(self.s,'2026-10-01','2026-10-07')
        send_package(self.s,v,'2026-10-01','2026-10-07',p,SMTP,True)
        with self.assertRaises(ValueError):send_package(self.s,v,'2026-10-01','2026-10-07',p,SMTP,True)
        self.assertEqual(len(calls),1)

class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,False);self.client=TestClient(self.app,base_url='http://127.0.0.1')
        self.token=self.client.get('/api/bootstrap').json()['token'];self.headers={'X-Local-Token':self.token}
    def tearDown(self):self.client.close();self.app.state.store.db.close();self.tmp.cleanup()
    def test_requires_token(self):self.assertEqual(self.client.get('/api/state').status_code,403)
    def test_cross_origin(self):self.assertEqual(self.client.get('/api/state',headers=self.headers|{'Origin':'https://evil.example'}).status_code,403)
    def test_cross_host(self):self.assertEqual(self.client.get('/api/bootstrap',headers={'Host':'evil.example'}).status_code,403)
    def test_defaults(self):
        d=self.client.get('/api/state',headers=self.headers).json();self.assertFalse(d['settings']['weekly_email_enabled']);self.assertEqual(d['settings']['target_count'],5)
    def test_secret_not_returned_or_stored(self):
        r=self.client.post('/api/settings',headers=self.headers,json={'settings':Settings().model_dump(),'api_key':'TOP_SECRET'});self.assertEqual(r.status_code,200)
        self.assertNotIn('TOP_SECRET',self.client.get('/api/state',headers=self.headers).text)
        self.assertNotIn(b'TOP_SECRET',(Path(self.tmp.name)/'newsdesk.sqlite3').read_bytes())
    def test_manual_requires_confirmation(self):self.assertEqual(self.client.post('/api/send',headers=self.headers,json={'start':'2026-10-01','end':'2026-10-07'}).status_code,400)
    def test_bad_custom_url_rejected(self):
        r=self.client.post('/api/settings',headers=self.headers,json={'settings':Settings().model_dump()|{'custom_source_urls':['http://127.0.0.1']}});self.assertEqual(r.status_code,422)
    def test_export_empty_real_gap(self):
        r=self.client.post('/api/export',headers=self.headers,json={'start':'2026-10-01','end':'2026-10-07'});self.assertEqual(r.status_code,200);self.assertEqual(sum(d['shortfall'] for d in r.json()['shortfalls']),35)
    def test_private_file_not_exposed(self):self.assertEqual(self.client.get('/files/newsdesk.sqlite3').status_code,404)
    def test_ui_assets(self):
        for path in ['/','/static/app.js','/static/style.css']:self.assertEqual(self.client.get(path).status_code,200)

class CollectorTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);self.s.save_settings(Settings(sources=['bjnews']));self.v=Vault(self.tmp.name)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def run_collect(self,fail=False,same_body=False):
        urls=[f'https://www.bjnews.com.cn/detail/{i}.html' for i in range(6)]
        class Fetch:
            def get(self,url):
                number=url.rsplit('/',1)[1]
                data=article(title='新闻'+number+' | 新京报快评',author='测试署名')
                if not same_body:data=data.replace('这是一段'.encode(),('编号'+number+'这是一段').encode())
                return data,url
            def allowed(self,url):pass
        class Shot:
            def capture(self,a,path):
                if fail:raise ValueError('模拟截图失败；不是真实截图')
                Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_bytes(b'MOCK SCREENSHOT NOT AN IMAGE')
        with patch('newsdesk.collector.discover',return_value=(urls,[])):
            return collect_day(self.s,self.v,'2026-10-08',fetcher=Fetch(),screenshotter=Shot())
    def test_daily_limit_five_distinct(self):
        r=self.run_collect();self.assertEqual((r['articles'],r['screenshots'],r['shortfall']),(5,5,0))
    def test_repeat_run_no_duplicates(self):
        self.run_collect();r=self.run_collect();self.assertEqual(r['articles'],5)
    def test_duplicate_content_shortfall(self):
        r=self.run_collect(same_body=True);self.assertEqual((r['articles'],r['shortfall']),(1,4))
    def test_screenshot_failure_not_complete(self):
        r=self.run_collect(fail=True);self.assertEqual((r['articles'],r['screenshots'],r['shortfall']),(5,0,5))
    def test_retry_only_failed_screenshots(self):
        self.run_collect(fail=True);r=self.run_collect();self.assertEqual((r['articles'],r['screenshots']),(5,5))

class BrowserSmokeTests(unittest.TestCase):
    def test_dashboard_smoke_has_no_csp_sensitive_eval(self):
        source=(Path(__file__).resolve().parent.parent/'tools'/'windows_smoke.py').read_text(encoding='utf-8')
        for unsafe_call in ('.wait_for_function(','.evaluate(','.evaluate_handle('):self.assertNotIn(unsafe_call,source)
    def test_smoke_stdout_safe_for_windows_cp1252(self):
        source=(Path(__file__).resolve().parent.parent/'tools'/'windows_smoke.py').read_text(encoding='utf-8')
        self.assertIn('print(json.dumps(report,ensure_ascii=True,indent=2))',source)
        self.assertIn("write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')",source)
        self.assertTrue(json.dumps({'message':'中文验证'},ensure_ascii=True).encode('cp1252'))
    def test_public_source_blocked_no_retry(self):
        with patch('newsdesk.smoke.Fetcher') as factory:
            fetcher=factory.return_value;fetcher.get.side_effect=ValueError('robots denies test')
            result=public_source_smoke();self.assertEqual(result['status'],'blocked');self.assertIn('robots',result['error']);self.assertEqual(result['attempts'],1)
            fetcher.get.assert_called_once();fetcher.close.assert_called_once()
    def test_public_source_does_not_retain_article_or_image(self):
        import struct
        with tempfile.TemporaryDirectory() as root,patch('newsdesk.smoke.Fetcher') as fetch,patch('newsdesk.smoke.Screenshots') as shot,patch('newsdesk.smoke.parse_article') as parse:
            fetch.return_value.get.return_value=(b'<article>test fixture only</article>','https://www.bjnews.com.cn/detail/1791448580169401.html')
            parse.return_value={'date':'2026-10-08','title':'离加油站20米 测试快评','author':'陈广江','media':'新京报','published_at':'2026-10-08','url':'https://www.bjnews.com.cn/detail/1791448580169401.html','body':'TEST_BODY_NEVER_RETAIN'}
            browser=shot.return_value.__enter__.return_value;browser.pw.chromium.executable_path='/tmp/chrome'
            def fake_capture(a,path):Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+b'\x00\x00\x00\rIHDR'+struct.pack('>II',1440,3000)+b'X'*9000)
            browser.capture.side_effect=fake_capture
            result=public_source_smoke(root);self.assertEqual(result['status'],'passed');self.assertNotIn('TEST_BODY_NEVER_RETAIN',json.dumps(result));self.assertEqual(list(Path(root).iterdir()),[])
    def test_blank_diagnostic_only(self):
        with patch('newsdesk.smoke.Screenshots') as factory:
            browser=factory.return_value.__enter__.return_value
            browser.pw.chromium.executable_path='/tmp/newsdesk-smoke/chrome'
            browser.browser.version='test-version'
            page=browser.context.new_page.return_value;page.locator.return_value.inner_text.return_value='NewsDesk browser smoke'
            result=browser_smoke();self.assertTrue(result['ok']);self.assertFalse(result['external_navigation']);self.assertFalse(result['news_capture_verified'])
            page.goto.assert_not_called();page.close.assert_called_once()
    def test_wrong_blank_dom_fails(self):
        with patch('newsdesk.smoke.Screenshots') as factory:
            browser=factory.return_value.__enter__.return_value;browser.pw.chromium.executable_path='/tmp/chrome'
            browser.context.new_page.return_value.locator.return_value.inner_text.return_value='wrong'
            with self.assertRaises(ValueError):browser_smoke()
    def test_frozen_browser_path_reported(self):
        with patch('newsdesk.smoke.Screenshots') as factory,patch('sys.frozen',True,create=True),patch('sys._MEIPASS','/tmp/newsdesk-smoke',create=True):
            browser=factory.return_value.__enter__.return_value;browser.pw.chromium.executable_path='/tmp/newsdesk-smoke/browser/chrome';browser.browser.version='test-version'
            browser.context.new_page.return_value.locator.return_value.inner_text.return_value='NewsDesk browser smoke'
            result=browser_smoke();self.assertTrue(result['frozen']);self.assertTrue(result['bundled_browser'])

if __name__=='__main__':unittest.main()
