from __future__ import annotations
import json, sqlite3, threading, uuid
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from .config import Settings

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        for d in ('screenshots','exports'): (self.root/d).mkdir(exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/'newsdesk.sqlite3',check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY,date TEXT,title TEXT,author TEXT,media TEXT,source_id TEXT,url TEXT UNIQUE,published_at TEXT,captured_at TEXT,screenshot_status TEXT,screenshot_path TEXT,status TEXT,error TEXT,title_hash TEXT,classification TEXT,original_source TEXT,body_hash TEXT);
        CREATE INDEX IF NOT EXISTS article_date ON articles(date);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,ts TEXT,level TEXT,message TEXT);
        CREATE TABLE IF NOT EXISTS runs (key TEXT PRIMARY KEY,state TEXT,updated TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS api_usage (date TEXT PRIMARY KEY,count INTEGER);
        CREATE TABLE IF NOT EXISTS rule_proposals (id TEXT PRIMARY KEY,base TEXT,changes TEXT,summary TEXT,applied INTEGER DEFAULT 0);
        '''); self.db.commit()
        if 'body_hash' not in [r['name'] for r in self.db.execute('PRAGMA table_info(articles)')]:self.execute('ALTER TABLE articles ADD COLUMN body_hash TEXT')
        self.execute("UPDATE runs SET state='interrupted',detail='程序上次退出时任务尚未完成；请检查后手动重试' WHERE state='running'")
    def execute(self,sql,args=()):
        with self.lock:
            c=self.db.execute(sql,args);self.db.commit();return c
    def all(self,sql,args=()):
        with self.lock:return [dict(x) for x in self.db.execute(sql,args).fetchall()]
    def one(self,sql,args=()):
        rows=self.all(sql,args);return rows[0] if rows else None
    def settings(self):
        row=self.one('SELECT value FROM config WHERE id=1')
        return Settings.model_validate_json(row['value']) if row else Settings()
    def save_settings(self,s):self.execute('INSERT OR REPLACE INTO config VALUES(1,?)',(s.model_dump_json(),))
    def event(self,message,level='info'):
        self.execute('INSERT INTO events(ts,level,message) VALUES(?,?,?)',(now(),level,message[:1500]))
    def articles(self,start,end=None):
        return self.all('SELECT * FROM articles WHERE date BETWEEN ? AND ? ORDER BY date,media,published_at,title',(start,end or start))
    def duplicate(self,url,title_hash,day,body_hash=''):
        d=date.fromisoformat(day)
        return self.one('SELECT id FROM articles WHERE url=? OR ((title_hash=? OR (body_hash=? AND body_hash!=\'\')) AND date BETWEEN ? AND ?)',(url,title_hash,body_hash,(d-timedelta(days=7)).isoformat(),(d+timedelta(days=7)).isoformat())) is not None
    def add(self,item):
        item={'id':uuid.uuid4().hex,**item}
        self.execute('INSERT INTO articles('+','.join(item)+') VALUES('+','.join('?' for _ in item)+')',tuple(item.values()));return item
    def run(self,key,state,detail=''):
        self.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?)',(key,state,now(),detail))
    def claim_send(self,key):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.db.execute('SELECT state FROM runs WHERE key=?',(key,)).fetchone()
                if row and row['state']!='failed':self.db.rollback();return False
                self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?)',(key,'prepared',now(),''))
                self.db.commit();return True
            except Exception:self.db.rollback();raise
    def consume_api(self,day,limit):
        with self.lock:
            row=self.one('SELECT count FROM api_usage WHERE date=?',(day,));n=row['count'] if row else 0
            if n>=limit:raise ValueError('今日 API 调用次数达到本机上限；失败请求也计入，供应商仍可能收费')
            self.execute('INSERT OR REPLACE INTO api_usage VALUES(?,?)',(day,n+1))
