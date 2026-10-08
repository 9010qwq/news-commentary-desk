"""Exercise an already-built portable EXE on Windows with fresh temporary data.

Dashboard browser access is restricted to localhost. A separate, optional
single public-article attempt uses the frozen application's normal checks and
retains metadata only. No API, SMTP, credentials, or user profiles are used.
"""
from __future__ import annotations
import argparse,json,os,socket,subprocess,tempfile,time,urllib.request,zipfile
from contextlib import nullcontext
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

def check(condition,message):
    if not condition:raise AssertionError(message)

def verified_public_capture(public):
    shot=public.get('screenshot',{})
    return (public.get('status')=='passed' and public.get('frozen') is True and public.get('bundled_browser') is True
            and shot.get('format')=='PNG' and isinstance(shot.get('width'),int) and shot['width']>=800
            and isinstance(shot.get('height'),int) and shot['height']>=600
            and isinstance(shot.get('bytes'),int) and shot['bytes']>=8000 and shot.get('retained') is False)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--public-source-once',action='store_true',help='Also try one fixed public article; never retain its screenshot or body')
    parser.add_argument('--require-public-source',action='store_true',help='Fail acceptance unless the frozen public-page PNG check passes')
    args=parser.parse_args();exe=args.exe.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if args.require_public_source:args.public_source_once=True
    check(exe.is_file(),'Portable executable is missing')
    report={'ok':False,'tests':[],'external_api_calls':0,'smtp_messages':0,'scheduled_news_collection':False,'public_source_attempted':False,'real_news_screenshots_verified':False}
    server=None;server_log=None;temporary=tempfile.TemporaryDirectory(prefix='newsdesk-windows-smoke-')
    try:
        # Cleanup must happen after terminating the EXE; Windows locks its live SQLite files.
        with nullcontext(temporary.name) as temp:
            temp=Path(temp);result_path=temp/'bundled-browser.json'
            # Do not inherit developer overrides: the frozen application must find its own browser.
            env=os.environ.copy();env.pop('NEWSDESK_BROWSER_PATH',None);env.pop('NEWSDESK_DATA_DIR',None);env.pop('PLAYWRIGHT_BROWSERS_PATH',None)
            result=subprocess.run([str(exe),'--smoke-browser-result',str(result_path)],env=env,timeout=120,capture_output=True,text=True,errors='replace')
            (output/'browser-launch.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
            check(result.returncode==0 and result_path.is_file(),'Frozen browser diagnostic failed; see browser-launch.log')
            browser_result=json.loads(result_path.read_text(encoding='utf-8'))
            check(browser_result.get('ok') and browser_result.get('frozen') and browser_result.get('bundled_browser'),'Chromium did not launch from the frozen bundle')
            browser_path=Path(browser_result['executable_path']).resolve()
            check(browser_path.is_file() and browser_path.is_relative_to(exe.parent),'Browser executable must be inside portable folder')
            report['browser']=browser_result;report['tests'].append('frozen EXE launches bundled Chromium with a blank page')
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            origin=f'http://127.0.0.1:{port}'
            server_log=(output/'application.log').open('w',encoding='utf-8')
            server=subprocess.Popen([str(exe),'--no-browser','--port',str(port),'--data-dir',str(temp/'data')],env=env,stdout=server_log,stderr=subprocess.STDOUT)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def get(path,token=None):
                req=urllib.request.Request(origin+path,headers={'X-Local-Token':token} if token else {})
                with opener.open(req,timeout=4) as response:return json.loads(response.read())
            deadline=time.monotonic()+90
            while True:
                check(server.poll() is None,'Portable EXE exited before health check; see application.log')
                try:health=get('/health');break
                except OSError:
                    if time.monotonic()>=deadline:raise AssertionError('Portable HTTP service did not become ready')
                    time.sleep(.25)
            check(health.get('ok') is True,'Health endpoint did not return OK');report['health']=health
            token=get('/api/bootstrap')['token'];initial=get('/api/state',token)
            check(not initial['settings']['auto_collect'] and not initial['settings']['weekly_email_enabled'] and not initial['settings']['api_enabled'],'Fresh app must disable all automated/external actions')
            check(not initial['articles'],'Fresh app must contain no news fixtures')
            report['tests'].append('portable localhost HTTP and fresh defaults')
            errors=[];blocked=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=str(browser_path))
                context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN',timezone_id='Asia/Shanghai',accept_downloads=True,service_workers='block')
                def local_only(route):
                    if route.request.url.startswith(origin+'/'):route.continue_()
                    else:blocked.append(route.request.url);route.abort()
                context.route('**/*',local_only);context.route_web_socket('**/*',lambda ws:ws.close())
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(origin,wait_until='networkidle',timeout=45000)
                page.locator('#connection.online').wait_for(timeout=15000)
                check(page.locator('#collect').is_enabled(),'Collect control did not become usable')
                check(page.locator('#export').is_enabled(),'Export control did not become usable')
                check(not page.locator('#auto-collect').is_checked() and not page.locator('#weekly-email').is_checked(),'UI must show automatic tasks off')
                page.locator('#settings-details > summary').click()
                page.locator('#target-count').fill('6');page.locator('#save-settings').click()
                expect(page.locator('#message')).to_contain_text('设置已保存',timeout=15000)
                check(get('/api/state',token)['settings']['target_count']==6,'UI settings did not persist in actual backend')
                page.reload(wait_until='networkidle');page.locator('#connection.online').wait_for()
                check(page.locator('#target-count').input_value()=='6','Settings did not survive page reload')
                page.locator('#settings-details > summary').click()
                page.locator('#target-count').fill('5');page.locator('#save-settings').click()
                expect(page.locator('#message')).to_contain_text('设置已保存',timeout=15000)
                check(get('/api/state',token)['settings']['target_count']==5,'Restoring test default failed')
                report['tests'].append('real dashboard controls save and reload local settings')
                # Export an empty report through the real UI. It must show missing items, never fabricated news.
                page.locator('#export').click();page.locator('#downloads a').first.wait_for(timeout=15000)
                links=page.locator('#downloads a');check(links.count()==2,'Empty export must offer XLSX and screenshot ZIP separately')
                downloads=[]
                for index in range(2):
                    with page.expect_download(timeout=15000) as waiting:links.nth(index).click()
                    download=waiting.value;target=temp/download.suggested_filename;download.save_as(str(target));downloads.append(target)
                screenshot_zip=next(f for f in downloads if f.suffix=='.zip')
                with zipfile.ZipFile(screenshot_zip) as archive:
                    manifest=json.loads(archive.read('manifest.json'))
                    check(manifest['items']==[],'Empty export fabricated news items')
                    check(sum(x['shortfall'] for x in manifest['daily'])==35,'Empty weekly export must report 35 missing screenshots')
                workbook=next(f for f in downloads if f.suffix=='.xlsx')
                with zipfile.ZipFile(workbook) as archive:check('xl/workbook.xml' in archive.namelist(),'Downloaded XLSX is not a workbook')
                report['tests'].append('native browser downloads real empty XLSX and ZIP with 35-item shortfall')
                page.screenshot(path=str(output/'empty-dashboard.png'),full_page=True)
                check(not errors,'Browser JavaScript errors: '+str(errors))
                check(not blocked,'UI attempted external requests: '+str(blocked))
                context.close();browser.close()
            final=get('/api/state',token)
            check(not final['articles'] and not final['settings']['auto_collect'] and not final['settings']['weekly_email_enabled'] and not final['settings']['api_enabled'],'Smoke changed external-action permissions')
            report['tests'].append('dashboard smoke made no news, API, email or external-browser requests and enabled no automatic tasks')
            if args.public_source_once:
                report['public_source_attempted']=True
                public_result=temp/'public-source.json'
                try:attempt=subprocess.run([str(exe),'--smoke-news-result',str(public_result)],env=env,timeout=180,capture_output=True,text=True,errors='replace')
                except subprocess.TimeoutExpired:attempt=None
                if attempt is not None and attempt.returncode==0 and public_result.is_file():
                    public=json.loads(public_result.read_text(encoding='utf-8'))
                    report['public_source']=public
                    report['real_news_screenshots_verified']=verified_public_capture(public)
                else:
                    report['public_source']={'status':'failed','safety_denial_confirmed':False,'attempts':1,'error':'Frozen public-source diagnostic timed out after 180 seconds' if attempt is None else 'Frozen public-source diagnostic did not return a report','screenshot_retained':False}
                report['tests'].append('one public article attempted through frozen app; inspect public_source status separately')
                if args.require_public_source:
                    check(report['real_news_screenshots_verified'],'Required frozen original-page PNG check failed or was blocked; inspect public_source; no release')
            report['ok']=True
    except Exception as error:
        report['error']=str(error);raise
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:server.wait(timeout=15)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
        if server_log:server_log.close()
        temporary.cleanup()
        (output/'smoke-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
