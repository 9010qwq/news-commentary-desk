from __future__ import annotations
import hashlib,json,re,unicodedata
from datetime import datetime,date
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode
from zoneinfo import ZoneInfo
from lxml import html,etree
from .config import SOURCES,source_of

LABELS=('央视快评','人民时评','人民锐评','人民论坛','今日谈','评论员文章','社论','新京报快评','新京报社论','新华时评','新华网评','新华每日电讯评论','马上评','澎湃评论','光明时评')
BODY_SELECTORS={'cctv':'//*[@id="text_area"]','bjnews':'//*[@id="contentStr"]','xinhua':'//*[@id="detailContent"]','people':'//*[@id="ozoom"] | //div[contains(@class,"article")]','thepaper':'//div[contains(@class,"index_centent")]'}

def clean(s):return re.sub(r'\s+',' ',s).strip() if isinstance(s,str) else ''
def node_text(n):return clean(' '.join(n.xpath('.//text()[not(ancestor::script) and not(ancestor::style)]')))
def canonical(url):
    p=urlsplit(url);q=urlencode([(k,v) for k,v in parse_qsl(p.query) if k.lower() not in {'utm_source','utm_medium','utm_campaign','from','spm','share_token','isappinstalled'}])
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path,q,''))
def title_hash(title):
    t=unicodedata.normalize('NFKC',title)
    for label in LABELS:t=t.replace(label,'')
    return hashlib.sha256(re.sub(r'[\W_丨｜]','',t).encode()).hexdigest()
def date_text(text,tz='Asia/Shanghai'):
    if not isinstance(text,str):return ''
    m=re.search(r'(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?',text or '')
    if not m:return ''
    try:return datetime(*[int(x or 0) for x in m.groups()],tzinfo=ZoneInfo(tz)).isoformat()
    except ValueError:return ''
def document(data):
    # Most modern pages are UTF-8; legacy People's Daily declares GBK correctly.
    try:return html.fromstring(data)
    except (etree.ParserError,ValueError,TypeError):raise ValueError('网页为空或HTML无法解析，未计入') from None
def meta(doc,*names):
    for name in names:
        v=doc.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")=$n or @property=$n]/@content',n=name.lower())
        if v and clean(v[0]):return clean(v[0])
    return ''
def next_data(doc):
    s=doc.xpath('//script[@id="__NEXT_DATA__"]/text()')
    try:
        value=json.loads(s[0]) if s else {};props=value.get('props',{}) if isinstance(value,dict) else {}
        page=props.get('pageProps',{}) if isinstance(props,dict) else {}
        return page if isinstance(page,dict) else {}
    except ValueError:return {}
def links(data,base,source):
    doc=document(data);out=[]
    if source=='cctv':
        raw=data.decode('utf-8','replace');m=re.search(r'var\s+obj\s*=\s*(\[)',raw)
        if m:
            try:
                obj,_=json.JSONDecoder().raw_decode(raw[m.start(1):])
                out.extend(x.get('url','') for x in obj if isinstance(x,dict))
            except ValueError:pass
    if source=='thepaper':
        data_obj=next_data(doc).get('data',{});items=data_obj.get('list',[]) if isinstance(data_obj,dict) else []
        for x in items if isinstance(items,list) else []:
            if isinstance(x,dict) and isinstance(x.get('contId'),(int,str)) and str(x['contId']).isdigit():out.append('https://www.thepaper.cn/newsDetail_forward_'+str(x['contId']))
    for a in doc.xpath('//a[@href]'):
        href=urljoin(base,a.get('href'));txt=node_text(a)
        if len(txt)>=6:out.append(href)
    valid=[]
    for u in out:
        if not isinstance(u,str):continue
        try:
            if source_of(u)!=source:continue
            if source=='custom' and urlsplit(u).hostname!=urlsplit(base).hostname:continue
        except ValueError:continue
        u=canonical(u)
        if re.search(r'\.(?:jpg|png|pdf|zip|mp4)(?:\?|$)',u,re.I):continue
        if source=='cctv' and not re.search(r'/\d{4}/\d{2}/\d{2}/',u):continue
        if source=='bjnews' and '/detail/' not in u:continue
        if source=='thepaper' and not re.search(r'(newsDetail_forward_|newsDetail\.jsp\?contid=)',u):continue
        if source=='people' and not ('/content/' in u or re.search(r'/n1?/\d{4}/',u)):continue
        if source=='xinhua' and not re.search(r'/20\d{6}[a-z0-9]*/',u):continue
        if u not in valid and canonical(base)!=u:valid.append(u)
    return valid

def discover(fetcher,source,day,custom=()):
    urls=[];errors=[]
    if source=='people':
        d=date.fromisoformat(day);base=f'https://paper.people.com.cn/rmrb/pc/layout/{d:%Y%m}/{d:%d}/node_01.html'
        try:
            data,final=fetcher.get(base);doc=document(data)
            # Locate actual commentary page from daily issue rather than assuming page number.
            for a in doc.xpath('//a[@href]'):
                if '评论' in node_text(a):urls.append(urljoin(final,a.get('href')))
            if not urls:urls=[base]
        except ValueError as e:errors.append(str(e));urls=[SOURCES[source]['url']]
    elif source=='bjnews':urls=[SOURCES[source]['url']]+[f'https://www.bjnews.com.cn/point/{p}.html' for p in range(2,5)]
    else:urls=[SOURCES[source]['url']]
    urls.extend(u for u in custom if source_of(u)==source)
    found=[]
    for u in dict.fromkeys(urls):
        try:
            data,final=fetcher.get(u);found.extend(links(data,final,source))
            if source=='xinhua':
                raw=data.decode('utf-8','replace')
                ids=list(dict.fromkeys(re.findall(r'datasource\s*:\s*([a-f0-9]{32})',raw)))[:5]
                for ident in ids:
                    try:
                        payload,_=fetcher.get(urljoin(final,'ds_'+ident+'.json'))
                        feed=json.loads(payload)
                        rows=feed.get('datasource',[]) if isinstance(feed,dict) else []
                        for row in rows if isinstance(rows,list) else []:
                            if isinstance(row,dict) and isinstance(row.get('publishUrl'),str):found.append(urljoin(final,row['publishUrl']))
                    except (ValueError,TypeError):errors.append('新华列表数据暂不可读')
        except ValueError as e:errors.append(str(e))
    return list(dict.fromkeys(found))[:70],errors

def parse_article(data,url,timezone='Asia/Shanghai'):
    source=source_of(url);doc=document(data);details=next_data(doc).get('detailData',{})
    nd=details.get('contentDetail',{}) if isinstance(details,dict) else {}
    if not isinstance(nd,dict):nd={}
    generic={}
    if source=='custom':
        for script in doc.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                value=json.loads(script);objects=value if isinstance(value,list) else value.get('@graph',[value])
                generic=next((x for x in objects if isinstance(x,dict) and any(k in str(x.get('@type','')) for k in ('Article','NewsArticle','BlogPosting'))),generic)
            except (ValueError,AttributeError,TypeError):pass
    title=clean(nd.get('name','')) or clean(generic.get('headline',''))
    if not title:
        h=doc.xpath('//h1');title=node_text(h[0]) if h else meta(doc,'og:title','title')
    if source=='people' and (not title or len(title)<5):
        for comment in doc.xpath('//comment()'):
            m=re.search(r'<title>(.*?)</title>',str(comment),re.S)
            if 'enpproperty' in str(comment) and m:
                title=clean(re.sub('<[^>]*>','',m.group(1)));break
    if not title or len(title)<5:raise ValueError('未识别到真实文章标题')
    bodies=doc.xpath(BODY_SELECTORS.get(source,'//article | //main'));body=node_text(bodies[0]) if bodies else ''
    if isinstance(nd.get('content'),str) and nd['content']:body=node_text(document(nd['content']))
    if not body:
        # Optional reputable extractor for layouts not covered; metadata is NOT filled by an LLM.
        try:
            import trafilatura
            body=trafilatura.extract(data,include_comments=False,include_tables=False) or ''
        except ImportError:pass
    if len(body)<100:raise ValueError('正文不足或页面为加载壳，未计入')
    paper_props={}
    if source=='people' and 'paper.people.com.cn/rmrb/' in url:
        for comment in doc.xpath('//comment()'):
            if 'enpproperty' in str(comment):
                for key in ('date','author','source'):
                    m=re.search(r'<'+key+r'>(.*?)</'+key+r'>',str(comment),re.S)
                    if m:paper_props[key]=clean(m.group(1))
    pub=paper_props.get('date','') if paper_props else (nd.get('pubTime','') or generic.get('datePublished','') or meta(doc,'article:published_time','publishdate','pubdate','date','dc.date'))
    if not isinstance(pub,str):pub=''
    if not pub or not re.search(r'\d{1,2}:\d{2}',pub):
        nodes=doc.xpath('//*[@id="title_area"]//*[contains(@class,"info")] | //div[@class="info"] | //*[contains(@class,"timer")] | //time | //*[contains(@class,"date")] | //*[contains(@class,"source")]')
        precise=next((date_text(node_text(n),timezone) for n in nodes if re.search(r'\d{1,2}:\d{2}',node_text(n)) and date_text(node_text(n),timezone)), '')
        pub=precise or pub or next((date_text(node_text(n),timezone) for n in nodes if date_text(node_text(n),timezone)), '')
    published=date_text(pub,timezone)
    if source=='custom' and pub:
        try:
            exact=datetime.fromisoformat(pub.replace('Z','+00:00'))
            if exact.tzinfo:published=exact.isoformat()
        except (ValueError,AttributeError):pass
    if source=='people' and not published:
        m=re.search(r'/(20\d{2})(\d{2})/(\d{2})/',url)
        if m:published=date_text('-'.join(m.groups()),timezone)
    if not published:raise ValueError('原页缺少可核实的绝对发布日期，未计入')
    if (not re.search(r'\d{1,2}:\d{2}',pub)) or (source=='people' and 'paper.people.com.cn/rmrb/' in url):published=published[:10]
    origin=clean(nd.get('source','')) or meta(doc,'source')
    if source=='people' and 'paper.people.com.cn/rmrb/' not in url and '人民日报' not in origin:
        # Check only the article's explicit source block, never arbitrary body mentions.
        source_nodes=doc.xpath('//*[contains(@class,"source") or contains(@class,"sou")]')
        origin=' '.join(node_text(n) for n in source_nodes)[:500]
        if '人民日报' not in origin:raise ValueError('人民网页面未核实为人民日报来源，未混算人民日报')
    author=paper_props.get('author','') if paper_props else (clean(nd.get('author','')) or meta(doc,'author','dc.creator'))
    if source=='custom' and not author:
        ga=generic.get('author',[]);ga=ga if isinstance(ga,list) else [ga]
        author='、'.join(clean(x.get('name','')) if isinstance(x,dict) else clean(x) for x in ga if isinstance(x,(dict,str)))
    if not author:
        # Explicit publication bylines only. Editors/reviewers are deliberately excluded.
        lines=[clean(x) for n in bodies for x in n.xpath('.//p//text()') if clean(x)]
        patterns=[r'^(?:作者|撰稿)\s*[:：/]\s*(.{2,40})$',r'^新华社记者\s*(.{2,25})$',r'^(央视评论员|本报评论员|人民日报评论员|新华社评论员)$']
        for line in lines[:8]+lines[-8:]:
            for pat in patterns:
                m=re.match(pat,line)
                if m:author=m.group(1);break
            if author:break
    if source=='people' and not author:
        nodes=doc.xpath('//*[@id="ozoom"]//h4 | //p[contains(@class,"author")] | //div[contains(@class,"author")]')
        author=next((node_text(n) for n in nodes if 1<len(node_text(n))<35),'')
    if '编辑' in author or '责编' in author or len(author)>80:author=''
    label=next((x for x in LABELS if x in title),'')
    if source=='custom' and not label:label=next((x for x in ('时评','快评','社论','Editorial','Commentary') if x in title),'')
    if not label and source=='cctv' and '央视评论员' in body:label='央视评论员署名'
    media=SOURCES[source]['name'] if source in SOURCES else '用户来源 · '+urlsplit(url).hostname
    if source=='custom':origin='页面自述：'+(meta(doc,'og:site_name') or origin or urlsplit(url).hostname)[:100]
    return {'date':published[:10],'title':title,'author':author or '未署名','media':media,
            'source_id':source,'url':canonical(url),'published_at':published,'body':body,
            'title_hash':title_hash(title),'body_hash':hashlib.sha256(re.sub(r'\s+','',body).encode()).hexdigest(),'classification':label,'original_source':origin or media}
