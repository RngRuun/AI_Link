<script setup lang="ts">
import {ref} from 'vue'
import Icon from './Icon.vue'
import type {ImportKind} from './types'
const props=defineProps<{target:string;disabled:boolean;busy:boolean}>()
const emit=defineEmits<{choose:[kind:ImportKind];files:[files:File[]]}>()
const dragging=ref(false)
let depth=0
function enter(event:DragEvent){if(event.dataTransfer?.types.includes('Files')){event.preventDefault();depth++;dragging.value=true}}
function leave(){depth=Math.max(0,depth-1);if(!depth)dragging.value=false}
function drop(event:DragEvent){depth=0;dragging.value=false;if(props.disabled)return;const files=Array.from(event.dataTransfer?.files || []);if(files.length)emit('files',files)}
</script>
<template>
  <div class="import-zone" :class="{'is-dragging':dragging&&!disabled,'is-busy':busy,'is-disabled':disabled}" role="group" :aria-label="`为${target}导入 JSONL 文件或文件夹`" :aria-busy="busy" @dragenter="enter" @dragleave="leave" @dragover.prevent="event=>{if(event.dataTransfer)event.dataTransfer.dropEffect=disabled?'none':'copy'}" @drop.prevent.stop="drop">
    <span class="import-zone-icon"><Icon :name="busy?'refresh':dragging?'folder':'upload'" :class="{spinning:busy}" :size="25"/></span>
    <div class="import-description"><strong>{{ busy?'正在准备或导入记录…':dragging&&!disabled?'松开即可导入':'拖入 JSONL 或年／月／日文件夹' }}</strong><p>本次归属：<span>{{ target }}</span></p><small>自动读取全部子目录，重复与重叠用量不再计入。</small></div>
    <div class="import-zone-actions"><button type="button" class="btn secondary import-files-button" :disabled="disabled" @click="emit('choose','files')"><Icon name="upload" :size="16"/>选择对话记录</button><button type="button" class="btn primary import-folder-button" :disabled="disabled" @click="emit('choose','folder')"><Icon name="folder" :size="16"/>导入文件夹</button></div>
    <div v-if="busy" class="import-progress" role="status"><i></i><span>扫描并解析本机记录，大目录可能需要一些时间</span></div>
  </div>
</template>
<style scoped>
.import-zone{display:grid;grid-template-columns:46px minmax(0,1fr);gap:15px;padding:23px;border:1px dashed var(--accent-strong);border-radius:14px;background:rgba(var(--accent-rgb),.025);min-width:0;transition:background-color .25s,border-color .25s,box-shadow .25s}
.import-zone.is-dragging{border-color:var(--accent);border-style:solid;background:rgba(var(--accent-rgb),.09);box-shadow:inset 0 0 0 2px rgba(var(--accent-rgb),.15)}
.import-zone-icon{width:44px;height:44px;display:grid;place-items:center;border-radius:12px;background:var(--accent-soft);color:var(--accent);grid-column:1;align-self:start}
.import-description{min-width:0}.import-description strong{font-size:13px;line-height:1.8;color:var(--text);font-weight:550;overflow-wrap:anywhere}.import-description p{font-size:11px;line-height:1.8;margin:5px 0;color:var(--muted);overflow-wrap:anywhere}.import-description p span{color:var(--accent)}.import-description small{display:block;font-size:10px;line-height:1.85;color:var(--muted)}
.import-zone-actions{grid-column:2;display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:3px;min-width:0}.import-zone-actions .btn{font-size:12px}
.import-progress{grid-column:1/-1;display:grid;gap:10px;padding-top:4px;overflow:hidden;font-size:10px;color:var(--muted);line-height:1.8}.import-progress>i{height:3px;background:linear-gradient(90deg,transparent,var(--accent),transparent);width:40%;animation:import-scan 1.6s ease-in-out infinite;border-radius:4px}
@keyframes import-scan{from{transform:translateX(-100%)}to{transform:translateX(350%)}}
@media(max-width:700px){.import-zone{padding:17px;grid-template-columns:36px minmax(0,1fr);gap:12px}.import-zone-icon{width:36px;height:36px;border-radius:10px}.import-description strong{font-size:12px}.import-zone-actions{grid-column:1/-1;margin-top:5px}.import-zone-actions .btn{flex:1;min-width:0;font-size:11px;padding:10px 9px}}
@media(max-width:420px){.import-zone-actions{flex-direction:column;align-items:stretch}.import-zone-actions .btn{width:100%}}
@media(prefers-reduced-motion:reduce){.import-zone{transition:none}.import-progress>i{animation:none;width:100%;opacity:.45}}
</style>
