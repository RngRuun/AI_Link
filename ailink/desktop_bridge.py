"""Allowlisted desktop commands, shared by Electron IPC and service tests."""
from __future__ import annotations
import time


class DesktopBridge:
    def __init__(self, app):
        self.app = app

    def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("操作参数必须是对象。")
        if self.app.shutdown_event.is_set() and method != "status":
            raise RuntimeError("应用正在退出。")
        a = self.app
        schemas = {
            "status": set(), "network.enable": set(), "network.disable": set(),
            "network.configure": {"config"}, "diagnostics.start": {"providers", "duration"},
            "diagnostics.cancel": set(), "kernel.apply": set(), "kernel.restore": set(),
            "quota.connect": {"shared"}, "quota.disconnect": set(), "quota.refresh": set(),
            "quota.configure": {"mode", "threshold", "autoResume", "bucket"},
            "quota.arm": {"enabled"}, "quota.select": {"threadId", "selected"},
            "quota.chat": {"prompt", "cwd"}, "report": set()
        }
        schemas.update({"quota.profile.create": {"name"}, "quota.profile.select": {"profileId"},
                        "quota.attribute": {"threadId", "personal"}, "quota.import": {"paths"}})
        schemas.update({'quota.source.refresh':set(),'quota.source.configure':{'enabled'}})
        schemas['quota.prepare']={'cwd'}
        schemas.update({'storage.scan':{'source'},'storage.preview':{'ids','policy'},'storage.execute':{'token'},
                        'storage.pin':{'id','important'},'storage.restore':{'batchId','id'},'storage.refresh':set(),
                        'storage.import':{'path','label'},'storage.location':{'kind'},'storage.purge.preview':{'batchId'},
                        'storage.purge':{'token'},'storage.cloud.target':{'id'}})
        schemas.update({'quota.threads.browse':{'search','cursor'},'quota.threads.attach':{'ids'},
                        'quota.continuation':{'threadId','enabled','prompt'}})
        schemas.update({"network.preset":{"name"},"network.measure":set(),"network.measure.cancel":set(),
                        "team.member.create":{"name"},"team.member.rename":{"memberId","name"},
                        "team.assign":{"sessionKey","memberId","plan"},
                        "team.import.files":{"paths","memberId","plan"},
                        "team.import.text":{"text","memberId","plan"},
                        "team.export":{"plan","window","format"}})
        valid_fields = method in schemas and (set(params) == schemas[method] or
                       (method == "quota.configure" and schemas[method] <= set(params) <= schemas[method] | {"caps","resumeTarget"}) or
                       (method == 'quota.chat' and set(params)==schemas[method]|{'threadId'}) or
                       (method == 'quota.connect' and schemas[method]<=set(params)<=schemas[method]|{'endpoint','tokenFile'}))
        if not valid_fields:
            raise ValueError("不支持的桌面操作或参数。")
        for field in ("shared", "autoResume", "enabled", "selected", "personal"):
            if field in params and type(params[field]) is not bool:
                raise ValueError("开关必须是布尔值。")
        if method == "status":
            return a.status()
        if method=='storage.scan':return a.storage.scan(params['source'])
        if method=='storage.preview':return a.storage.preview(params['ids'],params['policy'])
        if method=='storage.execute':return a.storage.execute(params['token'])
        if method=='storage.pin':return a.storage.pin(params['id'],params['important'])
        if method=='storage.restore':return a.storage.restore(params['batchId'],params['id'])
        if method=='storage.refresh':return {'accepted':True}
        if method=='storage.import':return a.storage.import_cloud(params['path'],params['label'])
        if method=='storage.location':return {'path':a.storage.location(params['kind'])}
        if method=='storage.purge.preview':return a.storage.purge_preview(params['batchId'])
        if method=='storage.purge':return a.storage.purge(params['token'])
        if method=='storage.cloud.target':
            identity=params['id']
            if identity is None:return {'url':'https://chatgpt.com/'}
            row=a.storage.cloud.records.get(identity.removeprefix('cloud:')) if isinstance(identity,str) else None
            if not row:raise ValueError('请选择已导入的云端记录。')
            return {'url':'https://chatgpt.com/c/'+row['conversation_id']}
        if method == "network.enable":
            a.enable()
        elif method == "network.disable":
            return a.disable()
        elif method == "network.configure":
            return a.configure(params["config"])
        elif method == "network.preset":
            return a.preset(params['name'])
        elif method == "network.measure":
            return a.measure_paths()
        elif method == "network.measure.cancel":
            a.measure.cancel()
        elif method == "team.member.create":
            return a.team.member(params['name'])
        elif method == "team.member.rename":
            return a.team.member(params['name'],params['memberId'])
        elif method == "team.assign":
            return a.team.assign(params['sessionKey'],params['memberId'],params['plan'])
        elif method == "team.import.files":
            return a.team.import_files(params['paths'],params['memberId'],params['plan'])
        elif method == "team.import.text":
            return a.team.import_text(params['text'],params['memberId'],params['plan'])
        elif method == "team.export":
            return a.team.export(params['plan'],params['window'],params['format'])
        elif method == "diagnostics.start":
            with a.mutation_lock:
                if a.shutdown_event.is_set():
                    raise RuntimeError("应用正在退出。")
                a.relay.start()
            return a.diagnostics.start(params["providers"], ["upstream", "relay"], params["duration"])
        elif method == "diagnostics.cancel":
            a.diagnostics.stop.set()
        elif method in ("kernel.apply", "kernel.restore"):
            with a.mutation_lock:
                result = a.kernel.apply() if method.endswith("apply") else a.kernel.restore()
                a.kernel_cache = a.kernel.status()
                a.last_kernel_refresh = time.monotonic()
                return result
        elif method == "quota.connect":
            if 'endpoint' in params or 'tokenFile' in params:a.quota.connect(params['shared'],params.get('endpoint',''),params.get('tokenFile'))
            else:a.quota.connect(params['shared'])
        elif method=='quota.threads.browse':
            return a.quota.browse_conversations(params['search'],params['cursor'])
        elif method=='quota.threads.attach':
            return a.quota.attach_conversations(params['ids'])
        elif method=='quota.continuation':
            a.quota.configure_continuation(params['threadId'],params['enabled'],params['prompt'])
        elif method == "quota.disconnect":
            a.quota.close()
        elif method == "quota.refresh":
            a.quota.refresh()
        elif method == "quota.configure":
            args=[params['mode'],params['threshold'],params['autoResume'],params['bucket']]
            if 'caps' in params or 'resumeTarget' in params:args.append(params.get('caps'))
            if 'resumeTarget' in params:args.append(params['resumeTarget'])
            a.quota.configure(*args)
        elif method == "quota.arm":
            a.quota.arm(params["enabled"])
        elif method == "quota.select":
            if not isinstance(params["threadId"], str) or len(params["threadId"]) > 128:
                raise ValueError("对话 ID 无效。")
            a.quota.select(params["threadId"], params["selected"])
        elif method == "quota.chat":
            if not all(isinstance(params[k], str) for k in ("prompt", "cwd")):
                raise ValueError("任务与目录必须是文本。")
            return a.quota.start_chat(params["prompt"], params["cwd"],params.get('threadId'))
        elif method == 'quota.prepare':
            return a.quota.prepare_chat(params['cwd'])
        elif method == "quota.profile.create":
            a.quota.profile(name=params["name"])
        elif method == "quota.profile.select":
            a.quota.profile(profile_id=params["profileId"])
        elif method == "quota.attribute":
            a.quota.attribute(params["threadId"], params["personal"])
        elif method == "quota.import":
            return a.quota.import_personal(params["paths"])
        elif method == 'quota.source.refresh':
            if not a.quota.source:raise RuntimeError('自动采集尚未初始化。')
            a.quota.source.request()
        elif method == 'quota.source.configure':
            if not a.quota.source:raise RuntimeError('自动采集尚未初始化。')
            a.quota.source.configure(params['enabled'])
        elif method == "report":
            state = a.status()
            return {key: state[key] for key in ("version", "config", "telemetry", "diagnostics", "kernel", "network")}
        return {"accepted": True}
