"""Explicit existing-thread adoption and independent continuation preferences."""
from pathlib import Path
import time


class ConversationControls:
    @staticmethod
    def conversation_name(thread):
        title=thread.get('name') or thread.get('preview') or ''
        if isinstance(title,str) and title.strip():return title.strip().splitlines()[0][:120]
        directory=thread.get('cwd')
        return '未命名对话'+(' · '+Path(directory).name if isinstance(directory,str) and directory else '')

    def continuation(self, thread_id, record):
        value=record.get('continuation')
        if isinstance(value,dict) and type(value.get('enabled')) is bool and isinstance(value.get('prompt'),str) and len(value['prompt'])<=4000:
            return {'enabled':value['enabled'],'prompt':value['prompt'].strip()}
        if 'continuation' in record:return {'enabled':False,'prompt':''}
        target=self.data.get('resume_target')
        return {'enabled':thread_id==target if target else True,'prompt':''}

    def configure_continuation(self, thread_id, enabled, prompt):
        if type(enabled) is not bool or not isinstance(prompt,str) or len(prompt)>4000 or len(prompt.encode('utf-8'))>16000:
            raise ValueError('续聊开关必须是布尔值，自定义内容最多 4000 字。')
        with self.lock:
            if self.enabled:raise RuntimeError('请先关闭守护，再修改续聊规则。')
            if thread_id not in {c['id'] for c in self.choices}:raise ValueError('请选择当前已经接管的对话。')
            record=self.data['threads'][thread_id]
            if record.get('state')=='resume_pending':raise RuntimeError('该对话的续聊结果尚未核实，请先刷新状态。')
            record['continuation']={'enabled':enabled,'prompt':prompt.strip()}
            self.data['continuations_version']=1
            self._save()

    def browse_conversations(self, search='', cursor=None):
        if not isinstance(search,str) or len(search)>120 or (cursor is not None and (not isinstance(cursor,str) or len(cursor)>4096)):
            raise ValueError('对话搜索或分页参数无效。')
        with self.lock:
            if not self.rpc:raise RuntimeError('请先连接 Codex，再读取已有对话。')
            params={'limit':30,'sortKey':'updated_at','sortDirection':'desc','modelProviders':[],
                    'sourceKinds':['cli','vscode','exec','appServer'],'archived':False}
            if search.strip():params['searchTerm']=search.strip()
            if cursor:params['cursor']=cursor
            response=self.rpc.call('thread/list',params)
            items=[]
            for thread in response.get('data',[])[:30]:
                if not isinstance(thread,dict) or not isinstance(thread.get('id'),str):continue
                items.append({'id':thread['id'],'name':self.conversation_name(thread),'cwd':thread.get('cwd') or '',
                              'updated':thread.get('updatedAt'),'source':thread.get('source') if isinstance(thread.get('source'),str) else 'Codex'})
            self.catalog={'items':items,'cursor':response.get('nextCursor'),'search':search,'updated':time.time()}
            self.catalog_ids={c['id'] for c in items}
            names={c['id']:c['name'] for c in items}
            for choice in self.choices:
                if choice['id'] in names:choice['name']=names[choice['id']]
            return self.catalog

    def attach_conversations(self, ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or any(not isinstance(k,str) or len(k)>128 for k in ids):
            raise ValueError('每批请选择 1–20 个已有对话。')
        with self.lock:
            if self.enabled:raise RuntimeError('请先关闭守护，再接入已有对话。')
            if not self.rpc:raise RuntimeError('请先连接 Codex。')
            if any(k not in self.catalog_ids|{c['id'] for c in self.choices} for k in ids):raise ValueError('目标必须来自当前已读取的对话列表。')
            if len(set(self.data['threads'])|set(ids))>100:raise ValueError('最多接入 100 个对话。')
            attached=[];failed=[]
            for thread_id in dict.fromkeys(ids):
                try:
                    thread=self.rpc.call('thread/resume',{'threadId':thread_id})['thread']
                    record=self.data['threads'].setdefault(thread_id,{'state':'watching'})
                    record.update(selected=True,attached=True,cwd=thread.get('cwd') or record.get('cwd',''))
                    record.setdefault('continuation',{'enabled':True,'prompt':''})
                    record.pop('control_error',None)
                    turns=thread.get('turns') or []
                    if turns:
                        latest=turns[-1];self.runtime[thread_id]=latest
                        if latest.get('status') in ('interrupted','failed') and latest.get('id') and record.get('state')!='resume_pending':
                            record.update(state='paused',turn_id=latest['id'],windows=['primary','secondary'],stop_reason='adopted_unfinished')
                    if thread_id not in {c['id'] for c in self.choices}:self.choices.append({'id':thread_id,'name':self.conversation_name(thread)})
                    self.personal.prime(self.data.get('account_fingerprint'),thread_id)
                    attached.append(thread_id)
                except Exception as error:
                    reason=('该对话正由另一个 Codex 进程写入；当前连接无法接管，请使用同一共享服务。'
                            if 'active writer' in str(error).lower() else '接入失败：'+str(error)[:180])
                    failed.append({'id':thread_id,'reason':reason})
            self._save()
            self.last_attach={'attached':attached,'failed':failed}
            return self.last_attach
