"""Previewed local retention using official Codex archive/delete operations."""
import copy
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from .codex_rpc import CodexRPC
from .cloud_retention import CloudExports
from .config import atomic_json
from .retention_content import safe_file,fingerprint,local_content,write_preserved


class StorageCleanup:
    def __init__(self,state_dir,quota,rpc_factory=CodexRPC,home=None):
        self.root=Path(state_dir)/'retention';self.home=Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex').absolute()
        self.quota=quota;self.rpc_factory=rpc_factory;self.lock=threading.RLock();self.catalog={};self.plans={}
        self.config_path=self.root/'preferences.json';self.pinned=[];self.batches={};self.error=None
        self.cloud=CloudExports(self.root/'cloud');self.last_result=None;self.preview_data=None;self.updated=0
        self.load_error=None
        try:
            if self.config_path.exists():
                safe_file(self.config_path,self.root,1024*1024);preferences=json.loads(self.config_path.read_text('utf-8'))
                if not isinstance(preferences,dict):raise ValueError('配置必须是对象')
                self.pinned=preferences.get('pinned',[])
                if not isinstance(self.pinned,list) or len(self.pinned)>5500 or any(not isinstance(i,str) or len(i)>150 for i in self.pinned):raise ValueError('重要标记格式错误')
        except (ValueError,OSError,TypeError) as error:self.load_error='清理配置损坏，已进入只读模式：'+str(error)[:100];self.pinned=[]
        batch_root=self.root/'batches'
        if batch_root.exists():
            for path in sorted(batch_root.glob('*/manifest.json'))[-200:]:
                try:
                    safe_file(path,self.root,2*1024*1024);value=json.loads(path.read_text('utf-8'))
                    if not isinstance(value,dict) or not re.fullmatch(r'[a-f0-9]{32}',path.parent.name) or value.get('id')!=path.parent.name or value.get('source') not in ('local','cloud') or not isinstance(value.get('items'),list) or len(value['items'])>20:raise ValueError('批次格式错误')
                    if type(value.get('created')) not in (int,float) or not math.isfinite(value['created']) or not isinstance(value.get('keep'),dict) or set(value['keep'])!={'files','final','decisions','code'} or any(type(v) is not bool for v in value['keep'].values()):raise ValueError('批次字段错误')
                    for entry in value['items']:
                        if not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or entry.get('folder') not in (None,hashlib.sha256(entry['id'].encode()).hexdigest()):raise ValueError('批次路径错误')
                        if not isinstance(entry.get('name'),str) or type(entry.get('bytes')) is not int or entry['bytes']<0 or entry.get('state') not in ('pending','failed','cloud_pending','archive_pending','archived','quarantined','restored','delete_pending','purged'):raise ValueError('批次目标字段错误')
                    self.batches[value['id']]=value
                except (ValueError,OSError,TypeError,KeyError) as error:self.load_error='回收区索引损坏，已进入只读模式：'+str(error)[:100]

    def writable(self):
        if self.load_error:raise RuntimeError(self.load_error)
        if self.root.resolve()!=self.root.absolute():raise RuntimeError('成果根目录不能是链接。')
        if len(self.batches)>=200:raise RuntimeError('清理批次达到 200，请先整理历史成果目录。')

    @contextmanager
    def connection(self):
        # Local cleanup never borrows a remote account connection or starts a model.
        rpc=self.rpc_factory();rpc.connect()
        try:yield rpc
        finally:rpc.close()

    def inventory(self,rpc):
        rows=[]
        for archived in (False,True):
            cursor=None;seen=set()
            while True:
                params={'limit':100,'modelProviders':[],'sourceKinds':['cli','vscode','exec','appServer','subAgent','subAgentReview','subAgentCompact','subAgentThreadSpawn','subAgentOther','unknown'],'archived':archived,'sortKey':'updated_at','sortDirection':'desc'}
                if cursor:params['cursor']=cursor
                value=rpc.call('thread/list',params)
                for row in value.get('data',[]):rows.append({**row,'archived':archived})
                if len(rows)>5000:raise ValueError('历史超过 5000 个目标，请先使用 Codex 官方工具分批整理。')
                cursor=value.get('nextCursor')
                if not cursor:break
                if cursor in seen:raise RuntimeError('Codex 分页游标重复，已停止扫描。')
                seen.add(cursor)
        return rows

    @staticmethod
    def relations(rows):
        protected=set()
        for row in rows:
            parent=row.get('forkedFromId') or row.get('parentThreadId')
            source=row.get('source')
            if isinstance(source,dict):
                spawn=(source.get('subAgent') or {}).get('thread_spawn',{}) if isinstance(source.get('subAgent'),dict) else {}
                parent=parent or spawn.get('parent_thread_id')
                protected.add(row['id'])  # Unknown structured sources are conservative.
            if parent:protected.update((parent,row['id']))
        return protected

    def protected(self,identity,row=None,include_user=True):
        if include_user and identity in self.pinned:return '已标记重要'
        record=self.quota.data.get('threads',{}).get(identity,{})
        if record.get('selected') or record.get('start_pending') or record.get('state') in ('interrupt_pending','resume_pending','paused','paused_idle'):return '额度守护／续聊目标'
        if row and row.get('isPinned'):return 'Codex 已固定对话'
        return None

    def transcript(self,row):
        path=row.get('path')
        if not isinstance(path,str):raise ValueError('Codex 未提供本地会话文件路径。')
        candidate=Path(path).absolute()
        if not any(candidate.is_relative_to(self.home/folder) for folder in ('sessions','archived_sessions')):raise ValueError('会话文件不属于本机 Codex 目录。')
        return safe_file(candidate,self.home)

    def scan(self,source):
        if source not in ('local','cloud'):raise ValueError('请选择本机或云端导出分区。')
        with self.lock:
            self.preview_data=None;self.error=None
            try:
                if source=='cloud':
                    if self.cloud.error:raise RuntimeError(self.cloud.error)
                    rows=[{k:v for k,v in r.items() if k!='content'} for r in self.cloud.records.values()]
                    for row in rows:row.update(kind='conversation',protected=self.protected(row['id'],include_user=False),classification=self.counts(self.cloud.records[row['id'].removeprefix('cloud:')]['content']))
                else:
                    with self.connection() as rpc:inventory=self.inventory(rpc)
                    relations=self.relations(inventory);rows=[]
                    for item in inventory:
                        if item['archived']:continue
                        row={'id':item['id'],'name':str(item.get('name') or item.get('preview') or '未命名本地对话')[:160],
                             'workspace':item.get('cwd') or '未标注工作区','updated':item.get('updatedAt') or 0,'source':'local','kind':'conversation',
                             'protected':self.protected(item['id'],item,False) or ('派生／关联对话，防止级联操作' if item['id'] in relations else None),'meta':item}
                        try:
                            path=self.transcript(item);row['bytes']=path.stat().st_size;row['classification']=self.counts(local_content(path))
                        except (ValueError,OSError,TypeError) as error:row.update(bytes=0,protected='无法完整读取：'+str(error)[:80],classification={})
                        if time.time()-row['updated']<86400:row['protected']=row['protected'] or '最近 24 小时有更新'
                        rows.append(row)
                    log_root=self.home/'log'
                    if log_root.exists():
                        for path in sorted(log_root.glob('*.log'))[:500]:
                            try:
                                safe_file(path,self.home);info=path.stat()
                                identity='log:'+hashlib.sha256(str(path).encode()).hexdigest()
                                rows.append({'id':identity,'name':path.name,'workspace':'诊断日志（独立于工作区）','updated':info.st_mtime,'source':'local','kind':'log',
                                             'bytes':info.st_size,'path':str(path),'classification':{},'protected':self.protected(identity,include_user=False) or ('最近 24 小时有更新' if time.time()-info.st_mtime<86400 else None)})
                            except (ValueError,OSError):continue
                for row in rows:
                    row['base_protection']=row.get('protected')
                    if row['id'] in self.pinned:row['protected']='已标记重要'
                self.catalog={k:v for k,v in self.catalog.items() if v['source']!=source}
                self.catalog.update({r['id']:r for r in rows});self.updated=time.time()
            except Exception as error:self.error=str(error)[:250];raise
            return {'items':len(rows)}

    @staticmethod
    def counts(content):return {k:len(v) if isinstance(v,list) else int(bool(v)) for k,v in content.items()}

    def snapshot(self):
        with self.lock:
            items=[{k:v for k,v in row.items() if k not in ('meta','path','content','base_protection')} for row in self.catalog.values()]
            batches=[{'id':b['id'],'created':b['created'],'source':b['source'],'keep':b['keep'],'items':[{k:v for k,v in r.items() if k in ('id','name','state','bytes','retained','error')} for r in b['items']]} for b in self.batches.values()]
            return copy.deepcopy({'items':items,'updated':self.updated,'error':self.load_error or self.error,'preview':self.preview_data,'last_result':self.last_result,
                                  'batches':sorted(batches,key=lambda b:b['created'],reverse=True),'cloud_count':len(self.cloud.records),
                                  'root':str(self.home),'library':str(self.root),'cloud_connection':'export_only','pinned':self.pinned,'read_only_error':self.load_error})

    def pin(self,identity,important):
        if type(important) is not bool or identity not in self.catalog:raise ValueError('请选择扫描结果中的项目。')
        with self.lock:
            self.writable()
            if important and identity not in self.pinned:self.pinned.append(identity)
            if not important and identity in self.pinned:self.pinned.remove(identity)
            atomic_json(self.config_path,{'pinned':self.pinned});self.preview_data=None
            if important:self.catalog[identity]['protected']='已标记重要'
            elif self.catalog[identity]['protected']=='已标记重要':self.catalog[identity]['protected']=self.catalog[identity].get('base_protection') or self.protected(identity,include_user=False)

    @staticmethod
    def policy(value):
        if not isinstance(value,dict) or set(value)!={'source','workspace','olderDays','from','to','keep','includeLogs'}:raise ValueError('清理规则字段无效。')
        if value['source'] not in ('local','cloud') or not isinstance(value['workspace'],str) or len(value['workspace'])>2000:raise ValueError('分区或工作区无效。')
        if type(value['olderDays']) is not int or not 0<=value['olderDays']<=3650 or type(value['includeLogs']) is not bool:raise ValueError('闲置天数或日志选项无效。')
        for key in ('from','to'):
            number=value[key]
            if number is not None and (type(number) not in (int,float) or not math.isfinite(number) or number<0):raise ValueError('日期范围无效。')
        if value['from'] is not None and value['to'] is not None and value['from']>value['to']:raise ValueError('开始日期不能晚于结束日期。')
        keep=value['keep']
        if not isinstance(keep,dict) or set(keep)!={'files','final','decisions','code'} or any(type(v) is not bool for v in keep.values()) or not any(keep.values()):raise ValueError('至少选择一种保留成果。')
        return copy.deepcopy(value)

    def matches(self,row,policy):
        if row['source']!=policy['source'] or row.get('protected') or self.protected(row['id']):return False
        if type(row.get('updated')) not in (int,float) or not math.isfinite(row['updated']):return False
        if row['source']=='local' and time.time()-row['updated']<86400:return False
        if row['kind']=='log':
            if not policy['includeLogs'] or policy['workspace']:return False
        elif policy['workspace'] and row['workspace']!=policy['workspace']:return False
        return (row['updated']<=time.time()-policy['olderDays']*86400 and (policy['from'] is None or row['updated']>=policy['from']) and (policy['to'] is None or row['updated']<=policy['to']))

    def preview(self,ids,policy):
        policy=self.policy(policy)
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('请选择 1–20 个项目。')
        with self.lock:
            rows=[]
            for identity in ids:
                row=self.catalog.get(identity)
                if not row or not self.matches(row,policy):raise ValueError('所选项目受保护或已不匹配当前规则，请重新扫描与选择。')
                item=copy.deepcopy(row)
                if row['source']=='local':item['fingerprint']=fingerprint(self.transcript(row['meta']) if row['kind']=='conversation' else safe_file(row['path'],self.home))
                else:item['fingerprint']=hashlib.sha256(json.dumps(self.cloud.records[identity.removeprefix('cloud:')],sort_keys=True).encode()).hexdigest()
                rows.append(item)
            if sum(r['bytes'] for r in rows)>512*1024*1024:raise ValueError('本批原始记录超过 512 MiB，请减少所选项目。')
            token=uuid.uuid4().hex;self.plans={token:{'created':time.time(),'rows':rows,'policy':policy}}
            self.preview_data={'token':token,'created':time.time(),'source':policy['source'],'count':len(rows),'bytes':sum(r['bytes'] for r in rows),'keep':policy['keep'],
                               'items':[{'id':r['id'],'name':r['name'],'kind':r['kind'],'bytes':r['bytes']} for r in rows]}
            return self.preview_data

    def save_batch(self,batch):atomic_json(self.root/'batches'/batch['id']/'manifest.json',batch)

    def execute(self,token):
        with self.lock,self.quota.lock:
            self.writable()
            plan=self.plans.pop(token,None)
            self.preview_data=None
            if not plan or time.time()-plan['created']>600:raise ValueError('预览已过期或已使用，请重新预览。')
            batch={'id':uuid.uuid4().hex,'created':time.time(),'source':plan['policy']['source'],'keep':plan['policy']['keep'],'items':[]}
            self.batches[batch['id']]=batch;self.save_batch(batch)
            with self.connection() if batch['source']=='local' else null_connection() as rpc:
                inventory=self.inventory(rpc) if rpc else [];relations=self.relations(inventory)
                for row in plan['rows']:
                    entry={'id':row['id'],'name':row['name'],'bytes':row['bytes'],'state':'pending','kind':row['kind']};batch['items'].append(entry);self.save_batch(batch)
                    folder=self.root/'batches'/batch['id']/hashlib.sha256(row['id'].encode()).hexdigest()
                    folder.mkdir(parents=True,exist_ok=True);entry['folder']=folder.name
                    try:
                        if not self.matches(self.catalog.get(row['id'],{}),plan['policy']):raise RuntimeError('规则或保护状态已变化。')
                        if batch['source']=='cloud':
                            key=row['id'].removeprefix('cloud:');record=self.cloud.records[key]
                            if hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()!=row['fingerprint']:raise RuntimeError('导出副本已变化。')
                            entry['retained']=write_preserved(folder/'preserved',record['content'],batch['keep'])
                            entry.update(state='cloud_pending',conversation_id=record['conversation_id'],workspace=record['workspace'])
                            # Local source import remains intact; this is a cloud action checklist.
                        else:
                            expected=row['fingerprint']
                            if row['kind']=='conversation':
                                current=next((r for r in inventory if r['id']==row['id'] and not r['archived']),None)
                                if not current or row['id'] in relations or self.protected(row['id'],current):raise RuntimeError('目标已变化、被固定或有关联对话。')
                                if time.time()-current.get('updatedAt',time.time())<86400:raise RuntimeError('最近 24 小时更新的对话不会清理。')
                                path=self.transcript(current)
                                if fingerprint(path)!=expected:raise RuntimeError('文件在预览后发生变化，未清理。')
                                before=path.read_bytes()
                                thread=rpc.call('thread/resume',{'threadId':row['id']})['thread']
                                latest=(thread.get('turns') or [{}])[-1]
                                if thread.get('status',{}).get('type') in ('active','busy') or latest.get('status') not in ('completed','interrupted','failed'):raise RuntimeError('回合仍在运行或状态无法确认，未清理。')
                                after=path.read_bytes()
                                if after!=before:
                                    # Codex persists this metadata event on resume. Allow only
                                    # its exact append shape, never appended messages or turns.
                                    tail=after[len(before):]
                                    records=[json.loads(line) for line in tail.splitlines()] if len(tail)<=16384 and after.startswith(before) else []
                                    if not records or any(v.get('type')!='event_msg' or (v.get('payload') or {}).get('type')!='thread_settings_applied' for v in records):raise RuntimeError('接管期间出现新的对话内容，未清理。')
                                    expected=fingerprint(path)
                            else:path=safe_file(row['path'],self.home)
                            if fingerprint(path)!=expected:raise RuntimeError('文件在预览后发生变化，未清理。')
                            content=local_content(path) if row['kind']=='conversation' else {'final':'','decisions':'','code':'','files':[]}
                            entry['retained']=write_preserved(folder/'preserved',content,batch['keep'],row['workspace'] if row['kind']=='conversation' else None)
                            backup=folder/'original';shutil.copy2(path,backup)
                            if fingerprint(backup)['sha256']!=expected['sha256'] or fingerprint(path)!=expected:raise RuntimeError('备份校验或原文件一致性检查失败。')
                            entry.update(original=str(path),sha256=expected['sha256'],bytes=expected['bytes'],state='archive_pending');self.save_batch(batch)
                            if row['kind']=='conversation':rpc.call('thread/archive',{'threadId':row['id']});entry['state']='archived'
                            else:path.unlink();entry['state']='quarantined'
                            self.catalog.pop(row['id'],None)
                    except Exception as error:
                        entry['error']=str(error)[:250]
                        if entry['state']=='pending':entry['state']='failed'
                    self.save_batch(batch)
            self.last_result={'batchId':batch['id'],'completed':sum(e['state'] in ('archived','quarantined','cloud_pending') for e in batch['items']),
                              'failed':sum(bool(e.get('error')) for e in batch['items']),'cloud_modified':False,'freed_bytes':0}
            return self.last_result

    def restore(self,batch_id,identity):
        with self.lock,self.quota.lock:
            if self.load_error:raise RuntimeError(self.load_error)
            batch=self.batches[batch_id];entry=next(e for e in batch['items'] if e['id']==identity)
            if entry['state'] not in ('archived','quarantined','archive_pending'):raise ValueError('此项目不在可恢复状态。')
            folder=self.root/'batches'/batch_id/entry['folder'];backup=safe_file(folder/'original',self.root)
            if fingerprint(backup)['sha256']!=entry['sha256']:raise RuntimeError('恢复备份校验失败。')
            if entry['kind']=='conversation':
                with self.connection() as rpc:
                    current=rpc.call('thread/read',{'threadId':identity})['thread'];path=self.transcript(current)
                    if not path.is_relative_to(self.home/'archived_sessions'):raise RuntimeError('目标已在活动目录或状态未知，未覆盖。')
                    if fingerprint(path)['sha256']!=entry['sha256']:raise RuntimeError('归档记录已有变化，未覆盖。')
                    rpc.call('thread/unarchive',{'threadId':identity})
            else:
                original=Path(entry['original'])
                if original.exists() or not original.is_relative_to(self.home/'log'):raise RuntimeError('原位置已有文件或位置无效，未覆盖。')
                if original.parent.resolve()!=original.parent.absolute():raise RuntimeError('原目录链接已变化。')
                shutil.copy2(backup,original)
            entry['state']='restored';entry.pop('error',None);self.save_batch(batch)

    def purge_preview(self,batch_id):
        with self.lock:
            batch=self.batches[batch_id]
            if batch['source']!='local':raise ValueError('云端分区只能在官方页面完成删除。')
            entries=[e for e in batch['items'] if e['state'] in ('archived','quarantined','restored')]
            if not entries:raise ValueError('此批没有可永久清除的项目。')
            token=uuid.uuid4().hex;self.plans[token]={'created':time.time(),'purge':batch_id,'ids':[e['id'] for e in entries]}
            return {'token':token,'count':len(entries),'bytes':sum(e['bytes'] for e in entries),'names':[e['name'] for e in entries],
                    'preserved':str(self.root/'batches'/batch_id),'warning':'将永久删除所选原始聊天／日志及恢复备份；已提取成果和工作区文件保留。已恢复项目只删除本应用的备份。'}

    def purge(self,token):
        with self.lock,self.quota.lock:
            if self.load_error:raise RuntimeError(self.load_error)
            plan=self.plans.pop(token,None)
            if not plan or not plan.get('purge') or time.time()-plan['created']>600:raise ValueError('永久清理确认已过期。')
            batch=self.batches[plan['purge']];completed=0;failures=[]
            with self.connection() as rpc:
                relations=self.relations(self.inventory(rpc))
                for entry in batch['items']:
                    if entry['id'] not in plan['ids']:continue
                    try:
                        if entry['state'] not in ('archived','quarantined','restored'):raise RuntimeError('回收区状态已变化。')
                        backup=safe_file(self.root/'batches'/batch['id']/entry['folder']/'original',self.root)
                        if fingerprint(backup)['sha256']!=entry['sha256']:raise RuntimeError('原始备份校验失败。')
                        if entry['state']=='archived':
                            if entry['id'] in relations or self.protected(entry['id']):raise RuntimeError('目标已受保护或出现派生关系。')
                            thread=rpc.call('thread/read',{'threadId':entry['id']})['thread'];path=self.transcript(thread)
                            if not path.is_relative_to(self.home/'archived_sessions') or fingerprint(path)['sha256']!=entry['sha256']:raise RuntimeError('原归档位置或内容已变化。')
                            entry['state']='delete_pending';self.save_batch(batch)
                            rpc.call('thread/delete',{'threadId':entry['id']})
                        backup.unlink();entry['state']='purged';entry.pop('error',None);completed+=1
                    except Exception as error:entry['error']=str(error)[:250];failures.append(entry['name'])
                    self.save_batch(batch)
            self.last_result={'completed':completed,'failed':len(failures),'cloud_modified':False,'permanent':True}
            return self.last_result

    def import_cloud(self,path,label):
        with self.lock:
            self.writable();result=self.cloud.import_file(path,label);self.scan('cloud');return result

    def location(self,kind):
        locations={'codex':self.home/'sessions','state':self.root.parent,'library':self.root,'cloud':self.root/'cloud'}
        if kind not in locations:raise ValueError('不支持的数据目录。')
        path=locations[kind];path.mkdir(parents=True,exist_ok=True)
        if path.resolve()!=path.absolute():raise ValueError('数据目录不能是链接。')
        return str(path)


@contextmanager
def null_connection():yield None
