"""Import-based multi-person token reports. No account-percent attribution."""
from __future__ import annotations
import copy
import csv
import hashlib
import io
import json
import math
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from html import escape
from pathlib import Path
from .config import atomic_json
from .import_paths import collect_jsonl

COLORS = ['#67618e','#3e7472','#80625c','#837250','#52718b','#755e78','#63785e','#545e83','#86664d','#78698a','#48727a','#79794f','#7b5b6e','#567066','#6e6f89','#817153','#577087','#7b655f','#697653','#69677f']
WINDOWS = {"primary": 18000, "secondary": 604800, "all": None}


def member_name(value):
    if not isinstance(value, str):
        raise ValueError("成员名称必须为文本。")
    name = unicodedata.normalize('NFC', value.strip())
    if not 1 <= len(name) <= 24 or any(ord(c)<32 or ord(c)==127 for c in name):
        raise ValueError("成员名称须为 1–24 个字符，不能包含控制字符。")
    try:
        name.encode('utf-8')
    except UnicodeError:
        raise ValueError("成员名称包含无效字符。") from None
    return name


def merged_ranges(ranges):
    result = []
    for start,end in sorted(ranges,key=lambda r:(r[0],r[1])):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1],end)
        else:
            result.append([start,end])
    return result


def uncovered(start, end, ranges):
    result, cursor = [], start
    for left,right in ranges:
        if right <= cursor:
            continue
        if left >= end:
            break
        if left > cursor:
            result.append((cursor,min(end,left)))
        cursor = max(cursor,right)
    if cursor < end:
        result.append((cursor,end))
    return result


class TeamUsage:
    def __init__(self, directory):
        self.path = Path(directory)/'team-usage.json'
        self.lock = threading.RLock()
        self.data = {'version':1,'members':{},'sessions':{}}
        self.error = None
        try:
            if self.path.exists():
                if self.path.stat().st_size > 32*1024*1024:
                    raise ValueError()
                data = json.loads(self.path.read_text(encoding='utf-8'))
                self._validate(data)
                self.data = data
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.error = '多人统计文件无法读取；已保留原文件，请从备份恢复后再导入。'

    @staticmethod
    def _validate(data):
        if data.get('version') != 1 or not isinstance(data.get('members'),dict) or not isinstance(data.get('sessions'),dict):
            raise ValueError()
        if len(data['members'])>20 or len(data['sessions'])>5000:
            raise ValueError()
        count, total_tokens = 0,0
        for key,member in data['members'].items():
            if not isinstance(key,str) or len(key)>64 or member_name(member['name']) != member['name']:
                raise ValueError()
        for key,session in data['sessions'].items():
            if len(key)!=64 or session['member'] not in data['members'] or session['plan'] not in ('plus','pro'):
                raise ValueError()
            segments = session['segments']
            if not isinstance(segments,list):
                raise ValueError()
            previous = -1
            for seg in sorted(segments,key=lambda s:s['start']):
                if type(seg['start']) is not int or type(seg['end']) is not int or not 0<=seg['start']<seg['end']<=10**15:
                    raise ValueError()
                if seg['start'] < previous or type(seg['at']) not in (int,float) or not math.isfinite(seg['at']) or seg['at']<946684800:
                    raise ValueError()
                previous = seg['end']
                total_tokens += seg['end']-seg['start']
            count += len(segments)
        if count>100000 or total_tokens>9007199254740991:
            raise ValueError()

    def _check(self):
        if self.error:
            raise ValueError(self.error)

    def _commit(self, data):
        atomic_json(self.path,data)
        self.data = data

    def member(self, name, member_id=None):
        with self.lock:
            self._check()
            name = member_name(name)
            if any(m['name'].casefold()==name.casefold() and key!=member_id for key,m in self.data['members'].items()):
                raise ValueError("成员名称已存在，请选择已有成员。")
            if member_id is not None and member_id not in self.data['members']:
                raise ValueError("成员不存在。")
            if member_id is None and len(self.data['members'])>=20:
                raise ValueError("最多支持 20 位成员。")
            key = member_id or uuid.uuid4().hex
            data = copy.deepcopy(self.data)
            data['members'][key] = {'name':name,'created':data['members'].get(key,{}).get('created',time.time())}
            self._commit(data)
            return {'memberId':key,'name':name}

    def assign(self, key, member_id, plan):
        with self.lock:
            self._check()
            if not isinstance(key,str) or key not in self.data['sessions'] or member_id not in self.data['members'] or plan not in ('plus','pro'):
                raise ValueError("记录、成员或套餐模式无效。")
            data = copy.deepcopy(self.data)
            data['sessions'][key].update(member=member_id,plan=plan)
            self._commit(data)
            return {'assigned':True}

    def import_files(self, paths, member_id, plan):
        discovered = collect_jsonl(paths)
        def streams():
            for path in discovered['paths']:
                with path.open(encoding='utf-8-sig') as handle:
                    yield handle
        try:
            result = self._import(streams(),member_id,plan,discovered['files'])
        except (UnicodeError,OSError) as error:
            raise ValueError('部分 JSONL 无法读取，请保留原始 UTF-8 会话文件并检查访问权限；本批未写入。') from error
        return result | {'bytes':discovered['bytes'],'skipped_links':discovered['skipped_links']}

    def import_text(self, text, member_id, plan):
        if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>64000:
            raise ValueError("请粘贴不超过 64 KB 的 JSONL 用量记录；更大的记录请导入文件。")
        return self._import([io.StringIO(text.lstrip('\ufeff'))],member_id,plan,1)

    def _import(self, streams, member_id, plan, files):
        with self.lock:
            self._check()
            if not isinstance(member_id,str) or member_id not in self.data['members'] or plan not in ('plus','pro'):
                raise ValueError("请先选择成员和 Plus／Pro 模式。")
            data = copy.deepcopy(self.data)
            coverage = {key:merged_ranges((s['start'],s['end']) for s in item['segments']) for key,item in data['sessions'].items()}
            total, valid, skipped, malformed, added = 0,0,0,0,0
            now = time.time()
            segment_count = sum(len(s['segments']) for s in data['sessions'].values())
            previous_total = sum(seg['end']-seg['start'] for s in data['sessions'].values() for seg in s['segments'])
            for stream in streams:
                thread_id, previous = None,0
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        malformed += 1
                        continue
                    if not isinstance(record,dict) or not isinstance(record.get('payload'),dict):
                        continue
                    payload = record['payload']
                    if record.get('type')=='session_meta':
                        value = payload.get('id')
                        thread_id = value if isinstance(value,str) and 1<=len(value)<=128 else None
                        previous = None if payload.get('forked_from_id') else 0
                        continue
                    if record.get('type')!='event_msg' or payload.get('type')!='token_count':
                        continue
                    try:
                        count = payload['info']['total_token_usage']['total_tokens']
                        stamp = datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                        at = stamp.timestamp()
                        if not thread_id or type(count) is not int or not 0<=count<=10**15 or stamp.tzinfo is None or not 946684800<=at<=now+60:
                            raise ValueError()
                    except (ValueError,KeyError,TypeError,AttributeError,OverflowError):
                        skipped += 1
                        continue
                    valid += 1
                    key = hashlib.sha256(thread_id.encode()).hexdigest()
                    session = data['sessions'].get(key)
                    if session and (session['member']!=member_id or session['plan']!=plan):
                        owner = data['members'][session['member']]['name']
                        raise ValueError(f"对话 {key[:8]} 已归属「{owner}」· {session['plan'].upper()}。本批未写入；可在记录归属中调整。")
                    if session is None:
                        session = data['sessions'][key] = {'member':member_id,'plan':plan,'segments':[]}
                        coverage[key] = []
                    if previous is None:
                        previous = count
                        continue
                    if count<previous:
                        skipped += 1
                        continue
                    pieces = uncovered(previous,count,coverage[key])
                    for start,end in pieces:
                        last = session['segments'][-1] if session['segments'] else None
                        if last and last['end']==start and int(last['at']//60)==int(at//60):
                            last.update(end=end,at=max(last['at'],at))
                        else:
                            session['segments'].append({'start':start,'end':end,'at':at})
                            segment_count += 1
                        total += end-start
                        added += 1
                    if pieces:
                        coverage[key] = merged_ranges([*coverage[key],*pieces])
                    previous = count
                    if len(data['sessions'])>5000 or segment_count>100000:
                        raise ValueError("记录容量已达上限（5000 个对话／100000 个用量分段）；本批未写入。")
            if not valid:
                raise ValueError("未找到带有效时间及 token_count 的 Codex JSONL 记录。普通聊天正文不能准确换算 tokens。")
            if previous_total+total>9007199254740991:
                raise ValueError("总用量超过可精确展示的数值范围；本批未写入。")
            self._commit(data)
            return {'tokens':total,'samples':added,'valid':valid,'skipped':skipped,'malformed':malformed,'files':files,'plan':plan}

    def snapshot(self, now=None):
        with self.lock:
            now = time.time() if now is None else now
            members = [{'id':key,'name':m['name']} for key,m in self.data['members'].items()]
            plans = {plan:{window:{'total':0,'members':[{**m,'tokens':0,'sessions':0,'share':0} for m in members]}
                           for window in WINDOWS} for plan in ('plus','pro')}
            positions = {m['id']:i for i,m in enumerate(members)}
            sessions = []
            for key,session in self.data['sessions'].items():
                segments = session['segments']
                total = sum(s['end']-s['start'] for s in segments)
                sessions.append(dict(key=key,member=session['member'],plan=session['plan'],tokens=total,
                                     updated=max((s['at'] for s in segments),default=0)))
                for window,seconds in WINDOWS.items():
                    tokens = sum(s['end']-s['start'] for s in segments if s['at']<=now and (seconds is None or s['at']>now-seconds))
                    report = plans[session['plan']][window]
                    item = report['members'][positions[session['member']]]
                    item['tokens'] += tokens
                    item['sessions'] += int(tokens>0)
                    report['total'] += tokens
            for windows in plans.values():
                for report in windows.values():
                    for member in report['members']:
                        member['share'] = member['tokens']/report['total']*100 if report['total'] else 0
            return dict(members=members, plans=plans, sessions=sorted(sessions,key=lambda s:s['updated'],reverse=True),
                        error=self.error,updated=now,basis='explicit_member_imports',session_count=len(sessions))

    def export(self, plan, window, format):
        if plan not in ('plus','pro') or window not in WINDOWS or format not in ('svg','csv'):
            raise ValueError("导出选项无效。")
        state = self.snapshot()
        if state['error']:
            raise ValueError(state['error'])
        report = state['plans'][plan][window]
        label = {'primary':'近 5 小时','secondary':'近 7 天','all':'全部导入历史'}[window]
        if format=='csv':
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(['成员','模式','时间窗口','tokens','占比百分比','对话数'])
            for m in report['members']:
                name = "'"+m['name'] if m['name'].startswith(('=','+','-','@')) else m['name']
                writer.writerow([name,plan.upper(),label,m['tokens'],f"{m['share']:.4f}",m['sessions']])
            return {'data':'\ufeff'+output.getvalue(),'extension':'csv'}
        return {'data':render_svg_report(report,plan,label),'extension':'svg'}


def shade(hex_color, amount):
    values = [int(hex_color[i:i+2],16) for i in (1,3,5)]
    return '#'+''.join(f'{round(v+(255-v)*amount if amount>=0 else v*(1+amount)):02x}' for v in values)


def render_svg_report(report, plan, label):
    height = 510+32*len(report['members'])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}">',
             '<rect width="100%" height="100%" fill="#141d2b"/>',
             f'<defs><mask id="ring-cut" maskUnits="userSpaceOnUse" x="0" y="0" width="1000" height="{height}"><rect width="1000" height="{height}" fill="white"/><circle cx="500" cy="240" r="80" fill="black"/></mask>',
             '<radialGradient id="center-well" cx="40%" cy="35%" r="75%"><stop offset="0%" stop-color="#101a28"/><stop offset="100%" stop-color="#172231"/></radialGradient>']
    for i,m in enumerate(report['members']):
        color = COLORS[i%len(COLORS)]
        parts.append(f'<linearGradient id="piece-{i}" x1="10%" y1="0%" x2="90%" y2="100%"><stop offset="0%" stop-color="{shade(color,.055)}"/><stop offset="55%" stop-color="{color}"/><stop offset="100%" stop-color="{shade(color,-.16)}"/></linearGradient>')
    parts.extend(['</defs>','<g font-family="Microsoft YaHei,Segoe UI,sans-serif" fill="#d6e0ee">',
                  '<text x="50" y="34" font-size="10" letter-spacing="2" fill="#718298">AI LINK / USAGE REPORT</text>',
                  f'<text x="50" y="70" font-size="23" font-weight="550">多人用量 · {plan.upper()} · {label}</text>',
                  '<path d="M50 96H950" fill="none" stroke="#2a3648"/>'])
    angle, pieces = -math.pi/2,[]
    for i,m in enumerate(report['members']):
        if not m['tokens']:
            continue
        next_angle = angle+2*math.pi*m['tokens']/report['total']
        color, mid = COLORS[i%len(COLORS)],(angle+next_angle)/2
        if m['tokens']==report['total']:
            shape='<circle cx="500" cy="240" r="132"'
        else:
            x1,y1=500+132*math.cos(angle),240+132*math.sin(angle)
            x2,y2=500+132*math.cos(next_angle),240+132*math.sin(next_angle)
            shape=f'<path d="M500 240L{x1:.3f} {y1:.3f}A132 132 0 {int(next_angle-angle>math.pi)} 1 {x2:.3f} {y2:.3f}Z"'
        pieces.append((i,m,color,shape,500+106*math.cos(mid),240+106*math.sin(mid)))
        angle = next_angle
    parts.append('<circle cx="500" cy="240" r="106" fill="none" stroke="#101a28" stroke-width="54"/><g mask="url(#ring-cut)">')
    for i,m,color,shape,x,y in pieces:
        parts.append(shape+f' fill="url(#piece-{i})" stroke="#182332" stroke-width="1.6" stroke-linejoin="round"/>')
    parts.append('</g><circle cx="500" cy="240" r="132.5" fill="none" stroke="#607186" stroke-opacity=".18" stroke-width="1"/><circle cx="500" cy="240" r="79.5" fill="url(#center-well)" stroke="#2b3849" stroke-width=".8"/>')
    for i,m,color,shape,x,y in pieces:
        if m['share']>=7:
            parts.append(f'<text x="{x:.2f}" y="{y+4:.2f}" text-anchor="middle" fill="#f0f3f7" font-size="11" font-weight="550">{m["share"]:.1f}%</text>')
    total_text=f'{report["total"]:,}'
    font_size=max(12,min(28,250/len(total_text)))
    parts.extend(['<text x="500" y="215" text-anchor="middle" font-size="7" letter-spacing="1.6" fill="#718298">OBSERVED TOKENS</text>',
                  f'<text x="500" y="251" text-anchor="middle" font-size="{font_size:.2f}" font-weight="550">{total_text}</text>',
                  '<path d="M483 269H517" fill="none" stroke="#2b3849"/><text x="500" y="290" text-anchor="middle" font-size="10" letter-spacing="1" fill="#718298">tokens</text>',
                  '<text x="500" y="411" text-anchor="middle" font-size="11" fill="#8190a5">来源：明确归属的导入记录；不是账号额度百分比。</text>'])
    if not report['total']:
        parts.append('<text x="500" y="392" text-anchor="middle" font-size="11" fill="#8190a5">暂无用量</text>')
    for i,m in enumerate(report['members']):
        y=473+i*32
        parts.append(f'<circle cx="50" cy="{y-5}" r="3" fill="{COLORS[i%len(COLORS)]}"/><text x="68" y="{y}" font-size="13">{escape(m["name"])}</text><text x="640" y="{y}" text-anchor="end" font-size="13">{m["tokens"]:,} tokens</text><text x="940" y="{y}" text-anchor="end" font-size="12" fill="#8190a5">{m["share"]:.1f}%</text>')
    parts.append('</g></svg>')
    return ''.join(parts)
