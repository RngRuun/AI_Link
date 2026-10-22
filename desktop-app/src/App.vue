<script setup lang="ts">
import { computed, ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useApp } from './useApp'
import Icon from './Icon.vue'
import NetworkPage from './NetworkPage.vue'
import QuotaPage from './QuotaPage.vue'
import DiagnosticsPage from './DiagnosticsPage.vue'
import SettingsPage from './SettingsPage.vue'
import EventsPage from './EventsPage.vue'
import TeamPage from './TeamPage.vue'
import StoragePage from './StoragePage.vue'
import WorkspaceDirectory from './WorkspaceDirectory.vue'
import AmbientField from './AmbientField.vue'
const { state,busy,connectionError,toast,run,exportReport,online }=useApp()
const page=ref('home'), heading=ref<HTMLElement>()
const closing=ref(false)
let unsubscribeClosing:(()=>void)|undefined
onMounted(()=>unsubscribeClosing=window.aiLink.onClosing(()=>closing.value=true))
onUnmounted(()=>unsubscribeClosing?.())
let storedMotion=false
try {storedMotion=localStorage.getItem('ai-link-reduced-motion')==='true'} catch { /* Default to system preference. */ }
const reducedMotion=ref(storedMotion)
const shell=ref<HTMLElement>()
const colorQuery=window.matchMedia('(prefers-color-scheme: dark)')
let storedTheme='system'
try{storedTheme=localStorage.getItem('ai-link-theme') || 'system'}catch{}
const theme=ref(storedTheme),systemDark=ref(colorQuery.matches)
const dark=computed(()=>theme.value==='dark' || (theme.value==='system' && systemDark.value))
const systemThemeChanged=(event:MediaQueryListEvent)=>systemDark.value=event.matches
onMounted(()=>{colorQuery.addEventListener('change',systemThemeChanged);void window.aiLink.setTheme?.(dark.value)?.catch(()=>{})})
onUnmounted(()=>colorQuery.removeEventListener('change',systemThemeChanged))
watch(dark,value=>{void window.aiLink.setTheme?.(value)?.catch(()=>{})})
const themeSwitching=ref(false)
async function toggleTheme(event:MouseEvent){
 if(themeSwitching.value)return
 themeSwitching.value=true
 const nextTheme=dark.value?'light':'dark'
 const change=async()=>{theme.value=nextTheme;try{localStorage.setItem('ai-link-theme',theme.value)}catch{};await nextTick()}
 document.documentElement.style.setProperty('--theme-x',`${event.clientX}px`);document.documentElement.style.setProperty('--theme-y',`${event.clientY}px`)
 try{
  if(document.startViewTransition && !reducedMotion.value && !window.matchMedia('(prefers-reduced-motion: reduce)').matches){document.documentElement.classList.add('theme-snapshot');await document.startViewTransition(change).finished}
  else await change()
 }catch{await change()}
 finally{document.documentElement.classList.remove('theme-snapshot');themeSwitching.value=false}
}
let pointerFrame=0
const waveTimers=new Map<HTMLElement,ReturnType<typeof setTimeout>>()
onUnmounted(()=>{cancelAnimationFrame(pointerFrame);for(const timer of waveTimers.values())clearTimeout(timer)})
function waveEnter(event:PointerEvent){
 const button=(event.target as Element).closest<HTMLElement>('.btn,.icon-button,.text-button')
 if(!button||button.contains(event.relatedTarget as Node)||button.matches(':disabled')||event.pointerType==='touch')return
 const existing=waveTimers.get(button)
 if(existing){clearTimeout(existing);waveTimers.delete(button);button.classList.add('wave-hover');return}
 const rect=button.getBoundingClientRect(),x=Math.max(0,Math.min(rect.width,event.clientX-rect.left)),y=Math.max(0,Math.min(rect.height,event.clientY-rect.top))
 button.style.setProperty('--wave-x',`${x}px`);button.style.setProperty('--wave-y',`${y}px`)
 button.style.setProperty('--wave-radius',`${Math.ceil(Math.hypot(Math.max(x,rect.width-x),Math.max(y,rect.height-y)))+2}px`)
 button.classList.add('wave-reset');button.classList.remove('wave-hover')
 void getComputedStyle(button,'::before').clipPath
 button.classList.remove('wave-reset');button.classList.add('wave-hover')
}
function waveLeave(event:PointerEvent){const button=(event.target as Element).closest<HTMLElement>('.btn,.icon-button,.text-button');if(!button||button.contains(event.relatedTarget as Node))return;button.classList.remove('wave-hover');const old=waveTimers.get(button);if(old)clearTimeout(old);waveTimers.set(button,setTimeout(()=>waveTimers.delete(button),550))}
function pointer(event:PointerEvent){
 if(reducedMotion.value || event.pointerType==='touch')return
 const x=event.clientX,y=event.clientY,target=(event.target as Element).closest<HTMLElement>('.panel,.metric-card,.service-card')
 cancelAnimationFrame(pointerFrame);pointerFrame=requestAnimationFrame(()=>{
 shell.value?.style.setProperty('--pointer-x',`${x}px`);shell.value?.style.setProperty('--pointer-y',`${y}px`)
 if(target){const rect=target.getBoundingClientRect();target.style.setProperty('--hover-x',`${x-rect.left}px`);target.style.setProperty('--hover-y',`${y-rect.top}px`)}
 })
}
watch(reducedMotion,value=>{try {localStorage.setItem('ai-link-reduced-motion',String(value))}catch{}})
const pages=[{id:'home',title:'工作空间目录',subtitle:'找到功能，回到你的工作。',icon:'grid',label:'DIRECTORY'},{id:'network',title:'连接工作台',subtitle:'把连接交给 AI Link，把注意力留给创造。',icon:'network',label:'NETWORK'},
  {id:'quota',title:'额度守护',subtitle:'掌握剩余额度，为每一段任务安排好节奏。',icon:'shield',label:'CODEX'},
  {id:'team',title:'多人用量',subtitle:'独立归属每个人的记录，清楚比较团队用量。',icon:'users',label:'TEAM USAGE'},
  {id:'storage',title:'存储清理',subtitle:'本机与云端分区整理，把成果留在手边。',icon:'archive',label:'RETENTION'},
  {id:'diagnostics',title:'连接诊断',subtitle:'从连接到响应，了解网络的真实表现。',icon:'pulse',label:'DIAGNOSTICS'},
  {id:'settings',title:'传输策略',subtitle:'调整本地连接参数，让优化适合你的网络。',icon:'settings',label:'PREFERENCES'},
  {id:'events',title:'运行记录',subtitle:'查看本次运行的连接、策略和守护事件。',icon:'clock',label:'ACTIVITY'}]
const current=computed(()=>pages.find(p=>p.id===page.value)!)
const component=computed(()=>({home:WorkspaceDirectory,network:NetworkPage,quota:QuotaPage,team:TeamPage,storage:StoragePage,diagnostics:DiagnosticsPage,settings:SettingsPage,events:EventsPage}[page.value]))
const componentProps=computed(()=>page.value==='home'?{}:page.value==='storage'?{storage:state.value!.storage,busy:busy.value}:page.value==='quota' ? {quota:state.value!.quota,busy:busy.value} : page.value==='team'?{team:state.value!.team,busy:busy.value}:{state:state.value!,busy:busy.value})
watch(page,async()=>{await nextTick();heading.value?.focus({preventScroll:true});document.querySelector('.page-scroll')?.scrollTo({top:0})})
async function action(method:string,params:Record<string,unknown>,message:string){await run(method,params,message)}
async function returnDirectory(){page.value='home';await nextTick();document.querySelector('.page-scroll')?.scrollTo({top:0,behavior:reducedMotion.value?'instant':'smooth'})}
async function openDataDirectory(){try{await window.aiLink.openStorageLocation(page.value==='quota'?'codex':page.value==='storage'?'library':'state')}catch(error){showError(String(error))}}
function showError(text:string){toast.value={text,error:true}}
</script>
<template>
  <div ref="shell" class="app-shell" :class="{'theme-quota':page==='quota','theme-team':page==='team','theme-storage':page==='storage','reduced-motion':reducedMotion,'dark-mode':dark}" @pointermove="pointer" @pointerover="waveEnter" @pointerout="waveLeave" @dragover.prevent @drop.prevent>
    <a class="skip-link" href="#main">跳到主要内容</a>
    <div class="titlebar"><span class="titlebar-mark"><Icon name="arrow" :size="14"/></span><span>AI 用户体验优化</span><span class="titlebar-separator">/</span><span class="titlebar-caption">AI Link</span></div>
    <aside class="sidebar" aria-label="主导航"><div class="brand"><span class="brand-mark"><Icon name="arrow" :size="27"/></span><div><strong>AI Link<span class="brand-dot">.</span></strong><small>AI EXPERIENCE, RECONNECTED</small></div></div><div class="sidebar-section">工作空间</div><nav><button v-for="(item,index) in pages" :key="item.id" class="nav-item" :data-page="item.id" :class="{active:page===item.id, 'quota-nav':item.id==='quota','team-nav':item.id==='team','storage-nav':item.id==='storage','nav-separated':item.id==='diagnostics'}" :aria-current="page===item.id?'page':undefined" :title="item.title" @click="page=item.id"><Icon :name="item.icon" :size="20"/><span>{{ item.title }}</span><i v-if="item.id==='quota' && state?.quota.enabled" class="nav-live"></i><span v-if="item.id==='network' && state?.system.owned" class="nav-live network-live"></span></button></nav><div class="sidebar-bottom"><div class="sidebar-note"><span class="sidebar-note-icon"><Icon name="shield" :size="18"/></span><strong>你的控制，始终在线。</strong><p>网络与额度独立开关，<br>按你需要的方式工作。</p></div><button class="motion-button" :aria-pressed="reducedMotion" :title="reducedMotion?'恢复界面动画':'减少界面动画'" @click="reducedMotion=!reducedMotion"><Icon name="sun" :size="18"/><span>减少动画</span><span class="mini-switch" :class="{on:reducedMotion}"><i></i></span></button><div class="sidebar-version"><span>WINDOWS DESKTOP</span><span>v0.13.0</span></div></div></aside>
    <main id="main" class="main-workspace" tabindex="-1"><AmbientField :reduced="reducedMotion" :dark="dark" :palette="page"/><header class="workspace-header"><div><div class="breadcrumb">工作空间 <Icon name="chevron" :size="12"/><span>{{ current.label }}</span></div><h1 ref="heading" tabindex="-1">{{ current.title }}</h1><p>{{ current.subtitle }}</p></div><div class="header-actions"><button class="btn directory-return" aria-label="返回工作空间目录" title="返回工作空间目录" @click="returnDirectory"><Icon name="grid" :size="17"/><span>返回目录</span></button><button class="icon-button open-data-directory" aria-label="打开本页数据目录" title="打开本页数据目录" @click="openDataDirectory"><Icon name="folder" :size="18"/></button><span class="connection-badge"><i :class="{online}"></i>{{ online?'本机服务已连接':'正在连接本机服务' }}</span><button class="icon-button theme-toggle" :aria-busy="themeSwitching" :aria-label="dark?'切换到浅色模式':'切换到黑夜模式'" :title="dark?'切换到浅色模式':'切换到黑夜模式'" :aria-pressed="dark" @click="toggleTheme"><Icon :name="dark?'sun':'moon'" :size="18"/></button><button class="btn export-button" :disabled="!state" @click="exportReport"><Icon name="download" :size="17"/><span>导出报告</span></button></div></header><div class="page-scroll"><div v-if="connectionError" class="notice warning backend-error" role="alert"><Icon name="info"/><div><strong>本机服务暂不可用</strong><p>{{ connectionError }}</p></div></div><Transition name="page" mode="out-in"><KeepAlive v-if="state"><component :is="component" :key="page" v-bind="componentProps" @action="action" @navigate="page=$event" @error="showError" @notice="toast={text:$event,error:false}"/></KeepAlive><div v-else class="startup-state" key="startup"><div class="startup-symbol"><Icon name="network" :size="30"/></div><h2>正在准备你的工作空间</h2><p>{{ connectionError || '正在连接本机服务，网络优化与额度守护默认关闭。' }}</p><div class="loading-bar"><i></i></div></div></Transition><footer class="page-footer"><span><Icon name="shield" :size="13"/>本地运行 · 最小化继续运行</span><span>关闭应用时恢复原代理</span></footer></div></main>
    <Transition name="toast"><div v-if="toast" class="toast-message" :class="{error:toast.error}" :role="toast.error?'alert':'status'"><span class="toast-icon"><Icon :name="toast.error?'info':'check'" :size="18"/></span><p>{{ toast.text }}</p><button class="icon-button" aria-label="关闭提示" @click="toast=null"><Icon name="close" :size="16"/></button></div></Transition>
    <div v-if="busy" class="task-indicator" role="status"><Icon name="refresh" :size="13" class="spinning"/>正在处理</div>
    <div v-if="closing" class="closing-screen" role="status"><span class="startup-symbol"><Icon name="shield" :size="30"/></span><strong>正在恢复原代理并退出</strong><p>请稍候，独立守护会保护这次关闭过程。</p><Icon name="refresh" :size="18" class="spinning"/></div>
  </div>
</template>
