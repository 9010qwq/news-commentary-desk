"""Local-only launcher; never installs startup tasks or changes security settings."""
import argparse,json,os,socket,sys,threading,time,urllib.request,webbrowser
from pathlib import Path

def main():
    if getattr(sys,'frozen',False):os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH','0')
    parser=argparse.ArgumentParser(description='小栈·新闻时评 本地版')
    parser.add_argument('--port',type=int,default=8768)
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--smoke-browser-result',type=Path,help='Run a blank bundled-browser diagnostic, save JSON and exit; no external website')
    parser.add_argument('--smoke-news-result',type=Path,help='Attempt one fixed public article with normal checks; retain metadata only')
    args=parser.parse_args()
    if args.smoke_news_result:
        from newsdesk.smoke import public_source_smoke
        args.smoke_news_result.parent.mkdir(parents=True,exist_ok=True)
        result=public_source_smoke(args.smoke_news_result.parent)
        args.smoke_news_result.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0
    if args.smoke_browser_result:
        from newsdesk.smoke import browser_smoke
        args.smoke_browser_result.parent.mkdir(parents=True,exist_ok=True)
        try:result=browser_smoke()
        except Exception as error:result={'ok':False,'error':str(error),'external_navigation':False}
        args.smoke_browser_result.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0 if result['ok'] else 1
    if not 1024<=args.port<=65535:parser.error('端口需为1024至65535')
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    root=args.data_dir or Path(os.environ.get('NEWSDESK_DATA_DIR',base/'data'))
    root.mkdir(parents=True,exist_ok=True)
    lock=(root/'.instance.lock').open('a+b')
    try:
        if sys.platform=='win32':
            import msvcrt
            lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        print('This data folder is already open. Please use its existing window.');return 1
    try:
        import uvicorn
        from newsdesk.main import create_app
        with socket.socket() as s:s.bind(('127.0.0.1',args.port))
    except ImportError:
        print('Dependencies missing. Double-click the setup/start script.');return 1
    except OSError:
        print(f'Port {args.port} is occupied. Try launch.py --port {args.port+1}. No process was stopped.');return 1
    url=f'http://127.0.0.1:{args.port}'
    print(f'\nNewsDesk 0.2 | {url}\nKeep this window open for daily collection and Thursday emails.\nNo OS startup task was installed. Ctrl+C stops the app.\n')
    def open_browser():
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            try:
                with opener.open(url+'/health',timeout=1):webbrowser.open(url);return
            except OSError:time.sleep(.3)
    if not args.no_browser:threading.Thread(target=open_browser,daemon=True).start()
    uvicorn.run(create_app(root),host='127.0.0.1',port=args.port,access_log=False,log_level='warning',timeout_graceful_shutdown=5)
    lock.close();return 0
if __name__=='__main__':raise SystemExit(main())
