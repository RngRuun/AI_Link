<script setup lang="ts">
import Icon from './Icon.vue'
defineProps<{ context:'personal'|'team'; mode:string }>()
// CODEX_HOME default: https://learn.chatgpt.com/docs/config-file/config-advanced
// The sessions location and accepted format also follow the local importer.
</script>

<template>
  <details class="jsonl-guide" :data-context="context">
    <summary>
      <span class="jsonl-guide-icon"><Icon name="folder" :size="19"/></span>
      <span class="jsonl-guide-title"><strong>JSONL 导入教程</strong><small>找到记录 → 确认归属 → 查看用量</small></span>
      <span class="jsonl-guide-label">3 步上手</span><Icon class="jsonl-guide-chevron" name="down" :size="17"/>
    </summary>
    <div class="jsonl-guide-body">
      <p class="jsonl-guide-intro">使用 Codex 的原始会话日志。无需转换格式，也无需编辑文件内容。</p>
      <ol class="jsonl-guide-steps">
        <li>
          <span class="jsonl-step-number" aria-hidden="true">01</span>
          <div><h4>找到自己的会话记录</h4><p>打开 Windows 文件资源管理器，将下面路径粘贴到地址栏并按 Enter：</p>
            <code class="jsonl-guide-path" tabindex="0" aria-label="Windows 默认会话目录，可选中文字复制">%USERPROFILE%\.codex\sessions</code>
            <p>进入相应日期的子文件夹，找到 <code>rollout-….jsonl</code> 文件。每个文件对应一个对话；也可以直接导入 sessions、年、月或日文件夹，应用会递归读取全部子目录。</p>
            <small>自定义了 <code>CODEX_HOME</code> 时，请进入该目录下的 <code>sessions</code>。WSL、远程或其他电脑上的记录，需要从实际运行 Codex 的位置取得。</small>
          </div>
        </li>
        <li>
          <span class="jsonl-step-number" aria-hidden="true">02</span>
          <div><h4>{{ context==='team'?'确认成员与计划':'确认个人档案与计划' }}</h4>
            <p v-if="context==='team'">先填写「成员名称」并添加成员，再在「本次记录归属」中选中本人。确认上方计划为 <strong>{{ mode.toUpperCase() }}</strong>，每个人分别导入自己的文件。</p>
            <p v-else>在「统计档案」中选中自己，确认上方计划为 <strong>{{ mode.toUpperCase() }}</strong>。修改 Plus／Pro 后先点「保存策略」，并在导入前关闭额度守护。</p>
            <small>记录按所选计划归类，文件本身不会验证历史套餐。共享账号的总额度不代表个人用量；同一对话由多人共用时，无法自动按人拆分。</small>
          </div>
        </li>
        <li>
          <span class="jsonl-step-number" aria-hidden="true">03</span>
          <div><h4>选择 JSONL，查看统计结果</h4>
            <p>点击「选择对话记录」选择文件，或点「导入文件夹」直接选择年／月／日目录；也可从文件资源管理器拖入文件或文件夹。整批记录归属本次选定成员与计划，确认归属后再导入。</p>
            <p v-if="context==='team'">在「成员用量占比」和「成员明细」查看结果。较早的记录请选择「全部历史」；误选成员或计划时，在下方「记录归属」中调整。</p>
            <p v-else>在下方图表查看近 5 小时和近 7 天的用量。早于这两个滚动窗口的记录不会显示在当前曲线中。</p>
            <small>{{ context==='team'?'每次可选择或拖入最多 20 个文件或目录；递归最多 5000 份 JSONL、合计 1 GB，单文件 64 MB。超限时整批拒绝，可改选月份或日期目录。粘贴记录仍限 64 KB。':'每批最多 20 份，每份不超过 64 MB。仅选择属于当前个人档案的对话记录。' }}</small>
          </div>
        </li>
      </ol>
      <div class="jsonl-guide-result"><Icon name="check" :size="16"/><span>原文件不会被修改；记录在本机解析，只保存用量统计。同一归属下重复或重叠的用量不会再次计入。</span></div>
      <div class="jsonl-guide-faq">
        <h4>遇到问题？</h4>
        <details><summary>有 JSONL 文件，为什么无法统计？<Icon name="down" :size="14"/></summary><p>不要选 <code>history.jsonl</code>、普通聊天导出或仅改了扩展名的文本。当前导入器只识别 Codex 会话日志中的会话标识和带时间的 token 用量事件；Gemini／ChatGPT 的普通聊天导出不能直接用于此统计。</p><p>需要核对格式时，用文本编辑器只读查看原文件：应包含 <code>session_meta</code>、<code>event_msg / token_count</code>、累计 <code>total_tokens</code> 和有效时间。没有用量事件时不能从聊天字数还原准确 tokens。</p></details>
        <details><summary>导入成功，但新增用量为 0？<Icon name="down" :size="14"/></summary><p>先确认是否重复导入、所选计划是否正确，以及记录是否落在当前时间窗口内。分叉对话的首个继承计数不会当作新消耗。缺少部分记录时，只能统计文件中实际保留的用量。</p><p v-if="context==='team'">查看历史请选「全部历史」。提示已归属其他成员或模式时，先在「记录归属」调整；本批冲突记录不会被重复记入。</p><p v-else>个人图表只显示近 5 小时和近 7 天；更早的记录不会改变当前曲线。账号剩余额度百分比不会因导入发生变化。</p></details>
        <details><summary>找不到 sessions 或目标对话？<Icon name="down" :size="14"/></summary><p>确认 Windows 用户、运行设备和 <code>CODEX_HOME</code> 是否与原对话一致。云端或其他设备的对话不一定保存在当前电脑；没有本地会话用量日志时，不能通过聊天导出代替。请从实际运行位置取得原始文件后再导入。</p></details>
      </div>
    </div>
  </details>
</template>

<style scoped>
.jsonl-guide{min-width:0;border:1px solid var(--line);border-radius:12px;background:rgba(var(--accent-rgb),.025);color:var(--text)}
.jsonl-guide>summary{list-style:none;display:flex;align-items:center;gap:12px;padding:17px 18px;cursor:pointer;border-radius:12px;transition:background .2s}
.jsonl-guide>summary::-webkit-details-marker,.jsonl-guide-faq summary::-webkit-details-marker{display:none}
.jsonl-guide>summary:hover{background:rgba(var(--accent-rgb),.035)}
.jsonl-guide summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.jsonl-guide-icon{width:35px;height:35px;display:grid;place-items:center;background:var(--accent-soft);border-radius:10px;color:var(--accent);flex:0 0 auto}
.jsonl-guide-title{display:grid;gap:5px;min-width:0;flex:1}.jsonl-guide-title strong{font-size:12px;font-weight:600}.jsonl-guide-title small{font-size:10px;line-height:1.7;color:var(--muted)}
.jsonl-guide-label{font-size:10px;color:var(--muted);white-space:nowrap}.jsonl-guide-chevron{color:var(--muted);flex:0 0 auto;transition:transform .22s}.jsonl-guide[open]>summary>.jsonl-guide-chevron{transform:rotate(180deg)}
.jsonl-guide-body{padding:0 20px 20px;min-width:0}.jsonl-guide[open]>.jsonl-guide-body{animation:guide-reveal .23s ease-out}
.jsonl-guide-intro{font-size:11px;line-height:1.85;color:var(--muted);border-top:1px solid var(--line);padding-top:18px;margin:0 0 21px}
.jsonl-guide-steps{list-style:none;margin:0;padding:0;display:grid;gap:22px}.jsonl-guide-steps>li{display:grid;grid-template-columns:30px minmax(0,1fr);gap:12px;align-items:start;min-width:0}.jsonl-step-number{font-size:11px;font-variant-numeric:tabular-nums;color:var(--accent);border:1px solid var(--line);border-radius:8px;height:29px;display:grid;place-items:center;margin-top:1px}
.jsonl-guide h4{font-size:12px;font-weight:600;line-height:1.7;margin:0 0 7px}.jsonl-guide p{font-size:11px;line-height:1.95;margin:7px 0;color:var(--muted);overflow-wrap:anywhere}.jsonl-guide p strong{color:var(--text);font-weight:600}.jsonl-guide li small{display:block;font-size:10px;line-height:1.9;color:var(--muted);overflow-wrap:anywhere}.jsonl-guide code{font-family:Consolas,'Cascadia Code',monospace;font-size:.95em;color:var(--text);overflow-wrap:anywhere}
.jsonl-guide .jsonl-guide-path{display:block;background:rgba(var(--accent-rgb),.055);border:1px solid var(--line);border-radius:8px;padding:12px 13px;margin:10px 0;user-select:all;font-size:11px;line-height:1.8}
.jsonl-guide-result{display:flex;align-items:flex-start;gap:9px;border-top:1px solid var(--line);padding-top:17px;margin-top:21px;color:var(--muted);font-size:10px;line-height:1.9}.jsonl-guide-result>svg{flex:0 0 auto;margin-top:2px;color:var(--accent)}
.jsonl-guide-faq{border-top:1px solid var(--line);padding-top:18px;margin-top:17px}.jsonl-guide-faq>h4{color:var(--text);margin-bottom:8px}.jsonl-guide-faq>details+details{border-top:1px solid var(--line)}.jsonl-guide-faq summary{display:flex;align-items:center;justify-content:space-between;gap:12px;list-style:none;cursor:pointer;padding:11px 0;color:var(--text);font-size:11px;line-height:1.7}.jsonl-guide-faq summary svg{flex:0 0 auto;color:var(--muted);transition:transform .2s}.jsonl-guide-faq details[open]>summary svg{transform:rotate(180deg)}.jsonl-guide-faq p:last-child{margin-bottom:16px}
@keyframes guide-reveal{from{opacity:.35;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:700px){.jsonl-guide>summary{padding:15px 14px;gap:10px}.jsonl-guide-body{padding:0 14px 17px}.jsonl-guide-label{display:none}.jsonl-guide-steps>li{grid-template-columns:26px minmax(0,1fr);gap:10px}.jsonl-step-number{height:26px}.jsonl-guide .jsonl-guide-path{padding:10px;font-size:11px}.jsonl-guide-title small{font-size:10px}}
@media(prefers-reduced-motion:reduce){.jsonl-guide[open]>.jsonl-guide-body{animation:none}.jsonl-guide svg{transition:none}}
</style>
