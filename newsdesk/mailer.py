from __future__ import annotations
import hashlib,smtplib,ssl
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from .store import now

def send_package(store,vault,start,end,package,smtp_factory=None,manual_confirmed=False,settings_snapshot=None):
    s=settings_snapshot or store.settings()
    if not s.weekly_email_enabled and not manual_confirmed:raise ValueError('邮件发送尚未由你在设置中启用或手动确认')
    from .config import Settings
    Settings.model_validate(s.model_dump()|{'weekly_email_enabled':True})
    password=vault.get('smtp',s)
    if not password:raise ValueError('SMTP 密码/授权码缺失；请在本机填写或恢复凭据管理器')
    key='mail:'+start+':'+end+':'+hashlib.sha256(s.smtp_to_addr.encode()).hexdigest()[:12]
    previous=store.one('SELECT * FROM runs WHERE key=?',(key,))
    if previous and previous['state'] in {'prepared','sent','sending','uncertain','interrupted'}:
        raise ValueError('该日期范围已发送或发送结果不确定。为避免重复发送，请先在邮箱核对；程序不会自动重发')
    msg=EmailMessage(policy=SMTP);msg['Subject']=f'新闻时评周报 {start} 至 {end}'
    msg['From']=s.smtp_from_addr;msg['To']=s.smtp_to_addr
    gaps=[f"{d['date']} 缺{d['shortfall']}篇完整截图" for d in package['shortfalls'] if d['shortfall']]
    msg.set_content(f'按刊发日期归档的新闻时评及真实原页截图见两个附件。\n范围：{start} 至 {end}\n条目：{package["counts"]["articles"]}；成功截图：{package["counts"]["screenshots"]}\n'+('缺口：\n'+'\n'.join(gaps) if gaps else '本范围每天均已达到设置目标。')+'\n补采的截图时间为实际采集时间。自动发送需电脑开机且本程序运行。')
    for file in package['files']:
        p=Path(file['path']);sub='zip' if p.suffix=='.zip' else 'vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        msg.add_attachment(p.read_bytes(),maintype='application',subtype=sub,filename=p.name)
    raw=msg.as_bytes()
    if len(raw)>s.max_message_mb*1024*1024:raise ValueError(f'附件经邮件编码后超过{s.max_message_mb}MB限制，尚未发送。请本地下载或缩小日期范围')
    if not store.claim_send(key):raise ValueError('该日期范围已被另一个发送任务占用；未重复发送')
    try:
        context=ssl.create_default_context()
        factory=smtp_factory or (smtplib.SMTP_SSL if s.smtp_mode=='ssl' else smtplib.SMTP)
        kwargs={'host':s.smtp_host,'port':s.smtp_port,'timeout':30}
        if s.smtp_mode=='ssl':kwargs['context']=context
        with factory(**kwargs) as client:
            client.ehlo()
            if s.smtp_mode=='starttls':client.starttls(context=context);client.ehlo()
            client.login(s.smtp_username,password)
            store.run(key,'sending')
            refused=client.sendmail(s.smtp_from_addr,[s.smtp_to_addr],raw)
            if refused:raise ValueError('SMTP 拒收目标地址')
        store.run(key,'sent','SMTP 服务已接受；不等同收件人已阅读')
        store.event(f'{start} 至 {end} 周报已被 SMTP 服务接受')
        return {'ok':True,'message':'SMTP 服务已接受邮件；请在收件箱/垃圾邮件中核对'}
    except Exception:
        state=store.one('SELECT state FROM runs WHERE key=?',(key,))['state']
        store.run(key,'uncertain' if state=='sending' else 'failed','发送失败或结果不确定；为防重复请先核对邮箱')
        raise ValueError('邮件发送失败或结果不确定。密钥和服务器响应未写入日志；请核对邮箱及 SMTP 设置后再操作') from None
