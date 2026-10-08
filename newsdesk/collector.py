from __future__ import annotations
from contextlib import nullcontext
from .sources import discover,parse_article,links
from .config import SOURCES,source_of
from .network import Fetcher
from .browser import Screenshots
from .ai import classify
from .store import now

def collect_day(store,vault,day,urls=(),fetcher=None,screenshotter=None,progress=lambda x:None):
    settings=store.settings();own=fetcher is None;fetcher=fetcher or Fetcher();candidates={s:[] for s in settings.sources};accepted=[]
    errors=[];rejected=0
    try:
        for url in urls:
            src=source_of(url)
            if src not in candidates:candidates[src]=[]
            candidates[src].append(url)
        for src in settings.sources:
            progress('正在检查 '+SOURCES[src]['name']+' 的公开栏目')
            found,errs=discover(fetcher,src,day,settings.custom_source_urls)
            candidates[src].extend(u for u in found if u not in candidates[src])
            for e in set(errs):errors.append(SOURCES[src]['name']+'：'+e)
        for url in settings.custom_source_urls:
            if source_of(url)!='custom':continue
            try:
                progress('正在检查你添加的公开媒体栏目')
                data,final=fetcher.get(url)
                candidates.setdefault('custom',[]).extend(links(data,final,'custom')[:60])
            except ValueError as e:errors.append('自定义来源：'+str(e))
        # A rotating queue prefers varied publishers; it never demands exactly one per publisher.
        queue=[]
        if settings.media_balance:
            for i in range(max((len(v) for v in candidates.values()),default=0)):
                queue.extend(v[i] for v in candidates.values() if i<len(v))
        else:queue=[u for v in candidates.values() for u in v]
        existing=store.articles(day);slots=settings.target_count-len(existing)
        for url in list(dict.fromkeys(queue))[:150]:
            if len(accepted)>=slots:break
            try:
                progress(f'已找到 {len(existing)+len(accepted)}/{settings.target_count} 篇，核对原文日期与署名')
                data,final=fetcher.get(url);a=parse_article(data,final,'Asia/Shanghai')
                if a['date']!=day:continue
                if store.duplicate(a['url'],a['title_hash'],day,a['body_hash']) or any(x['title_hash']==a['title_hash'] or x['url']==a['url'] or x['body_hash']==a['body_hash'] for x in accepted):continue
                combined=a['title']+' '+a['body']
                if settings.include_keywords and not any(k in combined for k in settings.include_keywords):rejected+=1;continue
                if any(k in combined for k in settings.exclude_keywords):rejected+=1;continue
                if not a['classification']:
                    if settings.api_enabled:
                        if not classify(store,vault,settings,a):rejected+=1;continue
                        a['classification']='API辅助判定（可人工复核）'
                    else:rejected+=1;continue
                a.pop('body');accepted.append(a)
            except ValueError as e:
                if len(errors)<25:errors.append(str(e))
        pending=[a for a in existing if a['screenshot_status']!='ok']
        for a in accepted:
            pending.append(store.add({**a,'captured_at':'','screenshot_status':'pending','screenshot_path':'','status':'待截图','error':''}))
        if pending:
            try:
                with (nullcontext(screenshotter) if screenshotter is not None else Screenshots(fetcher.allowed)) as browser:
                    for a in pending:
                        relative=f"screenshots/{day}/{a['id']}.png"
                        try:
                            progress('正在截取原媒体网页：'+a['title'][:35]);fetcher.allowed(a['url'])
                            browser.capture(a,store.root/relative)
                            store.execute('UPDATE articles SET captured_at=?,screenshot_status=?,screenshot_path=?,status=?,error=? WHERE id=?',(now(),'ok',relative,'完整','',a['id']))
                        except ValueError as e:
                            store.execute('UPDATE articles SET screenshot_status=?,status=?,error=? WHERE id=?',('failed','截图失败',str(e),a['id']))
            except ValueError as e:
                errors.append(str(e))
                for a in pending:store.execute('UPDATE articles SET screenshot_status=?,status=?,error=? WHERE id=?',('failed','截图失败',str(e),a['id']))
        rows=store.articles(day);complete=sum(a['screenshot_status']=='ok' for a in rows)
        result={'date':day,'articles':len(rows),'screenshots':complete,'target':settings.target_count,'shortfall':max(0,settings.target_count-complete),'errors':list(dict.fromkeys(errors)),'rejected':rejected}
        store.event(f'{day}：{len(rows)}篇真实时评，{complete}张成功截图，缺口{result["shortfall"]}篇','warning' if result['shortfall'] else 'info')
        for error in result['errors'][:8]:store.event(error,'warning')
        return result
    finally:
        if own:fetcher.close()
