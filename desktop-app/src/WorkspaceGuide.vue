<script setup lang="ts">
import Icon from './Icon.vue'
defineProps<{ selectedPath:string }>()
</script>

<template>
  <details class="workspace-guide">
    <summary><span class="workspace-help-icon"><Icon name="folder" :size="19"/></span><span class="workspace-help-title"><strong>工作区选择教程</strong><small>选对项目文件夹，让任务从正确的位置开始。</small></span><span class="workspace-help-label">开始前了解</span><Icon name="down" class="workspace-help-chevron" :size="17"/></summary>
    <div class="workspace-help-body">
      <div class="workspace-help-steps">
        <p class="workspace-help-intro">工作区就是这次任务使用的本地文件夹。选择目录后，Codex 才知道要分析哪份项目。</p>
        <ol>
          <li><span aria-hidden="true">01</span><div><h4>找到项目所在的文件夹</h4><p>代码分析通常选择项目根目录，也就是包含 <code>src</code>、<code>README.md</code> 或 <code>package.json</code> 等文件的那一层。文档分析则选择存放文档的文件夹，无需 Git 仓库。</p></div></li>
          <li><span aria-hidden="true">02</span><div><h4>在托管任务中选择工作区</h4><p>在账户连接处选择「AI Link 托管」并连接 Codex，再点「新建托管任务」→「选择工作区」。在系统窗口中选中文件夹，点击「选择文件夹」。</p></div></li>
          <li><span aria-hidden="true">03</span><div><h4>先准备目标，再开启守护</h4><p>确认目录后，先点「准备受保护对话」，此时不会发送模型任务。回到上方保存策略并开启守护，再输入要分析的内容并点击「发送任务」。满额续聊目标也可指定为这段对话。</p></div></li>
        </ol>
      </div>
      <aside class="workspace-folder-example" aria-label="项目根目录示例">
        <span class="eyebrow muted">PROJECT ROOT</span><h4>选择项目这一层</h4>
        <div class="workspace-tree"><div class="workspace-tree-root"><Icon name="folder" :size="18"/><strong>我的项目</strong><span>选择</span></div><div class="workspace-tree-children"><span><Icon name="folder" :size="15"/>src</span><span><Icon name="folder" :size="15"/>docs</span><span><Icon name="terminal" :size="15"/>README.md</span></div></div>
        <p>这是目录示例，实际名称不作限制。选择包含所需文件的项目目录即可。</p>
        <div class="workspace-current"><small>当前工作区</small><code>{{ selectedPath || '尚未选择，展开下方托管任务后设置' }}</code></div>
      </aside>
      <div class="workspace-help-scope"><Icon name="eye" :size="17"/><p>本应用新建任务使用<strong>只读工作区</strong>，适合审阅、解释和问题分析。发送任务后，Codex 可能读取项目文件并将相关内容发送给模型；需要修改文件时，请在完整 Codex 客户端中处理。</p></div>
      <details class="workspace-help-faq"><summary>按钮不可用，或不确定该选什么？<Icon name="down" :size="14"/></summary><p>「新建托管任务」需要连接 Codex，并使用「AI Link 托管」模式。已经连接共享进程时，先断开再切换模式。发送任务还需要工作目录、任务内容和已开启的额度守护；操作进行中请等待完成。</p><p>选择你希望分析的项目文件夹即可。JSONL 用量统计在上方独立导入，不需要将 sessions 目录设置为任务工作区。</p></details>
    </div>
  </details>
</template>

<style scoped>
.workspace-guide{min-width:0;border:1px solid var(--line);border-radius:12px;background:rgba(var(--accent-rgb),.025);margin-bottom:20px}
.workspace-guide>summary{list-style:none;display:flex;align-items:center;gap:12px;cursor:pointer;padding:16px 17px;border-radius:12px;transition:background .2s}
.workspace-guide summary::-webkit-details-marker{display:none}.workspace-guide>summary:hover{background:rgba(var(--accent-rgb),.035)}.workspace-guide summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.workspace-help-icon{display:grid;place-items:center;width:35px;height:35px;border-radius:10px;background:var(--accent-soft);color:var(--accent);flex:0 0 auto}.workspace-help-title{display:grid;gap:5px;min-width:0;flex:1}.workspace-help-title strong{font-size:12px;color:var(--text);font-weight:600}.workspace-help-title small{font-size:11px;line-height:1.7;color:var(--muted)}.workspace-help-label{font-size:10px;white-space:nowrap;color:var(--muted)}.workspace-help-chevron{flex:0 0 auto;color:var(--muted);transition:transform .22s}.workspace-guide[open]>summary>.workspace-help-chevron{transform:rotate(180deg)}
.workspace-help-body{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:22px;padding:0 20px 20px;min-width:0}.workspace-guide[open]>.workspace-help-body{animation:workspace-help-reveal .24s ease-out}.workspace-help-steps{border-top:1px solid var(--line);padding-top:17px;min-width:0}.workspace-help-body p{font-size:12px;color:var(--muted);line-height:1.95;margin:6px 0;overflow-wrap:anywhere}.workspace-help-intro{margin:0 0 18px!important}.workspace-help-steps ol{list-style:none;padding:0;margin:0;display:grid;gap:20px}.workspace-help-steps li{display:grid;grid-template-columns:27px minmax(0,1fr);gap:11px;min-width:0}.workspace-help-steps li>span{height:27px;display:grid;place-items:center;border:1px solid var(--line);border-radius:8px;color:var(--accent);font-size:10px;font-variant-numeric:tabular-nums}.workspace-help-body h4{font-size:12px;font-weight:600;color:var(--text);line-height:1.7;margin:0 0 5px}.workspace-help-body code{font-family:Consolas,'Cascadia Code',monospace;font-size:.95em;overflow-wrap:anywhere;color:var(--text)}
.workspace-folder-example{padding:18px;border:1px solid var(--line);border-radius:12px;min-width:0;align-self:start;background:rgba(var(--accent-rgb),.022)}.workspace-folder-example h4{margin:8px 0 16px;font-size:13px}.workspace-tree{font-size:12px}.workspace-tree-root{display:flex;align-items:center;gap:9px;border:1px solid var(--accent-strong);border-radius:8px;background:var(--accent-soft);padding:11px;color:var(--accent)}.workspace-tree-root strong{font-weight:550}.workspace-tree-root>span{margin-left:auto;font-size:10px}.workspace-tree-children{display:grid;gap:16px;margin:14px 0 18px 18px;border-left:1px solid var(--line);padding:3px 0 3px 14px;color:var(--muted)}.workspace-tree-children>span{display:flex;align-items:center;gap:8px}.workspace-folder-example>p{font-size:11px}.workspace-current{border-top:1px solid var(--line);margin-top:18px;padding-top:15px;display:grid;gap:9px}.workspace-current small{font-size:10px;color:var(--muted)}.workspace-current code{font-size:11px;line-height:1.8;overflow-wrap:anywhere}
.workspace-help-scope{grid-column:1/-1;display:flex;align-items:flex-start;gap:10px;padding-top:16px;border-top:1px solid var(--line)}.workspace-help-scope>svg{margin-top:5px;color:var(--accent);flex:0 0 auto}.workspace-help-scope p{margin:0;font-size:11px}.workspace-help-scope strong{color:var(--text);font-weight:550}
.workspace-help-faq{grid-column:1/-1;border-top:1px solid var(--line);padding-top:5px;margin-top:-5px}.workspace-help-faq summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;cursor:pointer;font-size:11px;color:var(--text);line-height:1.8}.workspace-help-faq summary svg{flex:0 0 auto;color:var(--muted);transition:transform .2s}.workspace-help-faq[open] summary svg{transform:rotate(180deg)}.workspace-help-faq p{font-size:11px}
@keyframes workspace-help-reveal{from{opacity:.3;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1170px){.workspace-help-body{grid-template-columns:minmax(0,1fr)}.workspace-folder-example{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0 20px}.workspace-folder-example>.eyebrow,.workspace-folder-example>h4,.workspace-tree{grid-column:1}.workspace-folder-example>p{grid-column:2;grid-row:1/3}.workspace-current{grid-column:2;grid-row:3;margin:0;align-self:start}}
@media(max-width:700px){.workspace-guide>summary{padding:14px;gap:10px}.workspace-help-label{display:none}.workspace-help-body{padding:0 14px 17px;gap:18px}.workspace-folder-example{display:block;padding:16px}.workspace-current{margin-top:16px}.workspace-help-title small{font-size:10px}}
@media(prefers-reduced-motion:reduce){.workspace-guide[open]>.workspace-help-body{animation:none}.workspace-guide svg{transition:none}}
</style>
