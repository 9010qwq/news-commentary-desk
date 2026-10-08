from __future__ import annotations
import re,ipaddress
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator, model_validator

SOURCES = {
    'cctv': {'name':'央视新闻', 'url':'https://news.cctv.com/yskp/index.shtml', 'domains':['cctv.com','cctv.cn']},
    'people': {'name':'人民日报', 'url':'https://opinion.people.com.cn/', 'domains':['people.com.cn']},
    'bjnews': {'name':'新京报', 'url':'https://www.bjnews.com.cn/point', 'domains':['bjnews.com.cn']},
    'xinhua': {'name':'新华网', 'url':'https://www.news.cn/comments/', 'domains':['news.cn','xinhuanet.com']},
    'thepaper': {'name':'澎湃新闻', 'url':'https://www.thepaper.cn/list_27224', 'domains':['thepaper.cn']},
}

def source_of(url: str) -> str:
    if not isinstance(url,str) or len(url)>3000 or any(c in url for c in '\r\n\x00'):raise ValueError('媒体链接格式无效')
    p=urlsplit(url)
    if p.scheme not in {'https','http'} or not p.hostname or p.username or p.password or p.port not in (None,80,443):
        raise ValueError('只接受无账号信息的普通 HTTP/HTTPS 公开媒体原文或栏目地址')
    host=p.hostname.lower().rstrip('.')
    for key, source in SOURCES.items():
        if any(host==d or host.endswith('.'+d) for d in source['domains']): return key
    try:ipaddress.ip_address(host)
    except ValueError:pass
    else:raise ValueError('媒体链接必须使用公开域名，不能使用IP地址')
    if '.' not in host or host.endswith(('.local','.localhost','.internal','.lan','.home','.test','.invalid')) or host in {'localhost','metadata.google.internal'}:
        raise ValueError('拒绝本机、局域网和保留域名')
    return 'custom'

class Settings(BaseModel):
    sources: list[str] = Field(default_factory=lambda:list(SOURCES))
    target_count: int = Field(default=5,ge=1,le=20)
    timezone: str = 'Asia/Shanghai'
    daily_time: str = '20:00'
    auto_collect: bool = False
    media_balance: bool = True
    include_keywords: list[str] = Field(default_factory=list,max_length=30)
    exclude_keywords: list[str] = Field(default_factory=list,max_length=30)
    custom_source_urls: list[str] = Field(default_factory=list,max_length=15)
    weekly_email_enabled: bool = False
    smtp_host: str = ''
    smtp_port: int = Field(default=465,ge=1,le=65535)
    smtp_mode: str = 'ssl'
    smtp_username: str = ''
    smtp_from_addr: str = ''
    smtp_to_addr: str = ''
    max_message_mb: int = Field(default=20,ge=1,le=100)
    api_enabled: bool = False
    api_base_url: str = 'https://api.deepseek.com'
    api_model: str = ''
    api_daily_limit: int = Field(default=30,ge=1,le=200)
    remember_secrets: bool = False

    @field_validator('sources')
    @classmethod
    def valid_sources(cls,v):
        if not v or any(x not in SOURCES for x in v): raise ValueError('至少选择一家支持的媒体')
        return list(dict.fromkeys(v))
    @field_validator('timezone')
    @classmethod
    def valid_zone(cls,v):
        try: ZoneInfo(v)
        except Exception: raise ValueError('时区无效；推荐 Asia/Shanghai') from None
        return v
    @field_validator('daily_time')
    @classmethod
    def valid_time(cls,v):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v): raise ValueError('时间使用 HH:MM')
        return v
    @field_validator('smtp_mode')
    @classmethod
    def valid_tls(cls,v):
        if v not in {'ssl','starttls'}: raise ValueError('邮件必须使用 SSL 或 STARTTLS')
        return v
    @field_validator('smtp_host','smtp_username','smtp_from_addr','smtp_to_addr','api_model')
    @classmethod
    def no_newline(cls,v):
        if any(c in v for c in '\r\n\x00') or len(v)>300: raise ValueError('配置含非法字符或过长')
        return v.strip()
    @field_validator('api_base_url')
    @classmethod
    def valid_api(cls,v):
        p=urlsplit(v)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError('API 地址须为无账号、查询参数的 HTTPS Base URL')
        return v.rstrip('/')
    @field_validator('custom_source_urls')
    @classmethod
    def valid_urls(cls,v):
        for url in v: source_of(url)
        return list(dict.fromkeys(v))
    @field_validator('include_keywords','exclude_keywords')
    @classmethod
    def valid_keywords(cls,v):
        if any(not x.strip() or len(x)>50 for x in v): raise ValueError('关键词需为1至50字')
        return list(dict.fromkeys(x.strip() for x in v))
    @model_validator(mode='after')
    def complete(self):
        if self.weekly_email_enabled:
            if not self.smtp_host or not self.smtp_username: raise ValueError('启用每周邮件前请填写 SMTP 主机与账号')
            for addr in (self.smtp_from_addr,self.smtp_to_addr):
                if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+',addr):
                    raise ValueError('请填写一个完整的发件地址和一个收件地址')
        if self.api_enabled and not self.api_model: raise ValueError('启用 API 前请填供应商实际可用的模型名称')
        return self
