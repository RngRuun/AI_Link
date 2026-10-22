<script setup lang="ts">
import { ref,computed } from 'vue'
import Icon from './Icon.vue'
import type { AppState } from './types'
const props=defineProps<{state:AppState}>()
const filter=ref('all')
const events=computed(()=>props.state.telemetry.events.filter(e=>filter.value==='all' || ['error','warning'].includes(e.level)))
const level:Record<string,string>={info:'信息',success:'成功',warning:'注意',error:'错误'}
</script>
<template><div class="workspace-page"><section class="panel events-panel"><div class="section-heading"><div><span class="eyebrow muted">ACTIVITY LOG</span><h3>每一次状态变化，都有迹可循</h3><p>仅记录连接状态和计数，不记录认证信息与对话正文。</p></div><label class="sr-only" for="event-filter">筛选日志</label><select id="event-filter" v-model="filter"><option value="all">全部记录</option><option value="errors">仅注意与错误</option></select></div><div v-if="!events.length" class="empty-state"><span class="empty-icon"><Icon name="clock" :size="29"/></span><strong>这里暂时没有记录</strong><p>启用优化或完成检测后，新的状态会显示在这里。</p></div><ol v-else class="event-list"><li v-for="(event,index) in events" :key="`${event.time}-${index}`"><span class="event-dot" :class="event.level"><Icon :name="event.level==='success' ? 'check' : event.level==='error' ? 'close' : 'info'" :size="15"/></span><div><div class="event-meta"><span class="event-level" :class="event.level">{{ level[event.level] || event.level }}</span><time>{{ new Date(event.time*1000).toLocaleTimeString('zh-CN',{hour12:false}) }}</time></div><p>{{ event.message }}</p></div></li></ol></section></div></template>
