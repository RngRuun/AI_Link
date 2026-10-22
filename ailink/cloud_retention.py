"""Local ChatGPT export import. No private API, login or cloud mutations."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import time
import zipfile
from .config import atomic_json
from .retention_content import classify

class CloudExports:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'index.json';self.records={};self.error=None
        try:
            if self.path.exists():
                if self.path.stat().st_size>128*1024*1024:raise ValueError('索引过大')
                records=json.loads(self.path.read_text('utf-8'))
                if not isinstance(records,dict) or len(records)>5000:raise ValueError('索引格式无效')
                for key,row in records.items():
                    if not re.fullmatch('[a-f0-9]{64}',key) or not isinstance(row,dict) or row.get('id')!='cloud:'+key or not re.fullmatch('[a-zA-Z0-9_-]{1,128}',str(row.get('conversation_id',''))):raise ValueError('索引身份无效')
                    if not isinstance(row.get('content'),dict) or set(row['content'])!={'final','decisions','code','files'}:raise ValueError('索引内容无效')
                self.records=records
        except (ValueError,OSError,TypeError) as error:self.error='云端导出索引无法读取，已停止覆盖原文件：'+str(error)[:100]

    def import_file(self,path,label):
        if self.error:raise RuntimeError(self.error)
        path=Path(path)
        if not isinstance(label,str) or not 1<=len(label.strip())<=40:raise ValueError('请输入 1–40 字的账户／导出分组名称。')
        if path.suffix.lower() not in ('.zip','.json') or path.stat().st_size>512*1024*1024:raise ValueError('请选择不超过 512 MiB 的导出 ZIP 或 conversations JSON。')
        blocks=[]
        if path.suffix.lower()=='.zip':
            with zipfile.ZipFile(path) as archive:
                infos=archive.infolist()
                if len(infos)>10000 or sum(i.file_size for i in infos)>1024*1024*1024:raise ValueError('导出包超过 10000 个条目或解压合计 1 GiB。')
                if sum(i.file_size for i in infos if re.fullmatch(r'conversations(?:[-_]?\d+)?\.json',PurePosixPath(i.filename).name,re.I))>128*1024*1024:raise ValueError('导出对话 JSON 合计超过 128 MiB。')
                for info in infos:
                    name=PurePosixPath(info.filename.replace('\\','/'))
                    if name.is_absolute() or '..' in name.parts or re.match(r'^[a-z]:',str(name),re.I) or info.flag_bits&1:raise ValueError('导出包含不安全路径或加密条目。')
                    if info.file_size>10*1024*1024 and info.file_size/max(info.compress_size,1)>200:raise ValueError('导出包压缩率异常。')
                    if re.fullmatch(r'conversations(?:[-_]?\d+)?\.json',name.name,re.I):
                        if info.file_size>64*1024*1024:raise ValueError('单个 conversations JSON 不能超过 64 MiB。')
                        blocks.append(json.loads(archive.read(info)))
        else:
            if path.stat().st_size>64*1024*1024:raise ValueError('JSON 不能超过 64 MiB。')
            blocks.append(json.loads(path.read_text('utf-8-sig')))
        if not blocks:raise ValueError('未找到 conversations.json 或分卷文件。')
        prepared={};label=label.strip()
        for block in blocks:
            if not isinstance(block,list):raise ValueError('导出 conversations 必须为数组。')
            for conversation in block:
                if not isinstance(conversation,dict):raise ValueError('导出对话格式无效。')
                identity=conversation.get('id') or conversation.get('conversation_id')
                if not isinstance(identity,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',identity):raise ValueError('对话 ID 无效。')
                key=hashlib.sha256((label+'\0'+identity).encode()).hexdigest()
                content=self.content(conversation)
                timestamp=conversation.get('update_time') or conversation.get('create_time')
                if type(timestamp) not in (int,float) or not 0<timestamp<=time.time()+86400:raise ValueError('导出记录时间无效。')
                prepared[key]={'id':'cloud:'+key,'conversation_id':identity,'name':str(conversation.get('title') or '未命名云端对话')[:160],
                              'workspace':label,'updated':timestamp,'bytes':len(json.dumps(conversation).encode()),'source':'cloud','content':content}
        if len(set(prepared)|set(self.records))>5000:raise ValueError('本地云端索引最多 5000 个对话。')
        # Imported text is sensitive; keep only the bounded result needed for preservation.
        merged={**self.records,**prepared};encoded=json.dumps(merged,ensure_ascii=False)
        if len(encoded.encode())>128*1024*1024:raise ValueError('本地导出索引超过 128 MiB。')
        self.root.mkdir(parents=True,exist_ok=True);atomic_json(self.path,merged);self.records=merged
        return {'imported':len(prepared),'cloud_modified':False}

    @staticmethod
    def content(conversation):
        mapping=conversation.get('mapping') or {}
        if not isinstance(mapping,dict) or len(mapping)>10000:raise ValueError('对话节点无效或过多。')
        # Follow only the selected branch; do not merge alternate answers.
        node=conversation.get('current_node');seen=set();messages=[]
        if node:
            while node:
                if node in seen or node not in mapping:raise ValueError('对话分支无效。')
                seen.add(node);entry=mapping[node];message=entry.get('message') or {}
                if message.get('author',{}).get('role')=='assistant':
                    parts=message.get('content',{}).get('parts',[]);messages.append('\n'.join(p for p in parts if isinstance(p,str)))
                node=entry.get('parent')
            messages.reverse()
        else:
            # Older exports have no branch marker. Explicitly refuse ambiguous branches.
            if any(len(v.get('children',[]))>1 for v in mapping.values()):raise ValueError('缺少当前分支标记，无法安全选择回答。')
            for value in mapping.values():
                message=value.get('message') or {}
                if message.get('author',{}).get('role')=='assistant':messages.append('\n'.join(p for p in message.get('content',{}).get('parts',[]) if isinstance(p,str)))
        if sum(len(m) for m in messages)>2*1024*1024:raise ValueError('单个导出对话正文超过 2 MiB。')
        return classify(messages[-1:],messages)
