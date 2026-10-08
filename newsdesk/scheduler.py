from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

def week_range(current,timezone='Asia/Shanghai'):
    local=current.astimezone(ZoneInfo(timezone));today=local.date()
    # The most recent Thursday's package always covers the preceding Thu–Wed.
    thursday=today-timedelta(days=(today.weekday()-3)%7)
    return {'start':(thursday-timedelta(days=7)).isoformat(),'end':(thursday-timedelta(days=1)).isoformat(),'delivery_date':thursday.isoformat()}

def due_jobs(settings,current):
    local=current.astimezone(ZoneInfo(settings.timezone));day=local.date().isoformat();jobs=[]
    if settings.auto_collect and local.strftime('%H:%M')>=settings.daily_time:jobs.append(('daily:'+day,{'kind':'daily','date':day}))
    weekly_local=current.astimezone(ZoneInfo('Asia/Shanghai'))
    if settings.weekly_email_enabled and weekly_local.weekday()==3 and weekly_local.hour>=14:
        r=week_range(current,'Asia/Shanghai');jobs.append(('weekly:'+r['delivery_date'],{'kind':'weekly',**r}))
    return jobs

def next_schedule(settings,current):
    local=current.astimezone(ZoneInfo(settings.timezone));h,m=map(int,settings.daily_time.split(':'))
    daily=local.replace(hour=h,minute=m,second=0,microsecond=0)
    if daily<=local:daily+=timedelta(days=1)
    week_local=current.astimezone(ZoneInfo('Asia/Shanghai'))
    weekly=week_local.replace(hour=14,minute=0,second=0,microsecond=0)+timedelta(days=(3-week_local.weekday())%7)
    if weekly<=week_local:weekly+=timedelta(days=7)
    return {'daily':daily.isoformat() if settings.auto_collect else None,'weekly':weekly.isoformat() if settings.weekly_email_enabled else None,'timezone':settings.timezone,'weekly_timezone':'Asia/Shanghai','requires_open':True}
