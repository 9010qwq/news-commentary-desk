from __future__ import annotations
import ipaddress,socket,time
from urllib.parse import urljoin,urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from .config import source_of

USER_AGENT='NewsDesk/0.2 (personal commentary archive; low-frequency)'

class NetworkError(ValueError):
    def __init__(self,message,category,stage='http_request'):
        super().__init__(message);self.diagnostics={'stage':stage,'error_category':category}

def network_category(error):
    text=str(error).upper()
    if 'CERTIFICATE_VERIFY_FAILED' in text or 'CERTIFICATE VERIFY FAILED' in text:return 'TLS_VERIFICATION_FAILED'
    if isinstance(error,httpx.ConnectTimeout):return 'HTTP_CONNECT_TIMEOUT'
    if isinstance(error,httpx.ReadTimeout):return 'HTTP_READ_TIMEOUT'
    if isinstance(error,httpx.TimeoutException):return 'HTTP_TIMEOUT'
    return type(error).__name__

def public_url(url):
    p=urlsplit(url)
    if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password:
        raise ValueError('不安全的网络地址')
    try:
        addresses=socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('拒绝本机、局域网及保留网络地址')
    except socket.gaierror:raise NetworkError('域名暂时无法解析','DNS_LOOKUP_FAILED','dns') from None
    return url

class Fetcher:
    def __init__(self,transport=None,check_dns=True):
        self.client=httpx.Client(timeout=httpx.Timeout(18,connect=8),follow_redirects=False,trust_env=False,
                                 headers={'User-Agent':USER_AGENT},transport=transport)
        self.check_dns=check_dns;self.robots={};self.last_request={}
    def close(self):self.client.close()
    def raw(self,url,limit=5_000_000,check_robots=False):
        for _ in range(5):
            source_of(url)
            if check_robots:self.allowed(url)
            if self.check_dns:public_url(url)
            host=urlsplit(url).hostname
            elapsed=time.monotonic()-self.last_request.get(host,0)
            if elapsed<0.6:time.sleep(0.6-elapsed)
            self.last_request[host]=time.monotonic()
            try:
                with self.client.stream('GET',url) as r:
                    if r.status_code in {301,302,303,307,308}:
                        url=urljoin(url,r.headers.get('location',''));continue
                    if r.status_code in {401,403,429}:raise ValueError(f'媒体站点拒绝或限流（HTTP {r.status_code}），未尝试绕过')
                    r.raise_for_status();data=b''
                    for chunk in r.iter_bytes():
                        data+=chunk
                        if len(data)>limit:raise ValueError('网页超过5MB读取上限')
                    # Let lxml honor declared HTML encoding; JSON callers decode explicitly.
                    return data,str(r.url)
            except httpx.HTTPStatusError as e:raise ValueError(f'媒体网页 HTTP {e.response.status_code}') from None
            except httpx.RequestError as error:
                category=network_category(error)
                raise NetworkError('媒体网页连接失败（'+category+'）；未关闭证书校验或自动重试',category) from None
        raise ValueError('网页重定向过多')
    def allowed(self,url):
        p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            parser=RobotFileParser();parser.set_url(origin+'/robots.txt')
            try:
                data,_=self.raw(origin+'/robots.txt',500000)
                parser.parse(data.decode('utf-8','replace').splitlines())
            except ValueError as e:
                if '404' in str(e) or '410' in str(e):parser.parse([])
                else:
                    category=getattr(e,'diagnostics',{}).get('error_category','ROBOTS_UNAVAILABLE')
                    raise NetworkError('无法确认站点 robots 规则（'+str(e)+'）；已停止此站点访问，请稍后重试',category,'robots') from None
            self.robots[origin]=parser
        if not self.robots[origin].can_fetch(USER_AGENT,url):raise ValueError('站点 robots 规则禁止此路径自动采集')
    def get(self,url):
        source_of(url);return self.raw(url,check_robots=True)
