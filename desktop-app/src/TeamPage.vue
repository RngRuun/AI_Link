<script setup lang="ts">
import {ref,watch} from 'vue'
import Icon from './Icon.vue'
import TeamUsage from './TeamUsage.vue'
import type {TeamState} from './types'
defineProps<{team?:TeamState;busy:string}>()
const emit=defineEmits<{action:[method:string,params:Record<string,unknown>,message:string];error:[message:string];notice:[message:string]}>()
let initial='plus';try{if(localStorage.getItem('ai-link-team-plan')==='pro')initial='pro'}catch{}
const mode=ref(initial)
watch(mode,value=>{try{localStorage.setItem('ai-link-team-plan',value)}catch{}})
</script>
<template><div class="workspace-page team-page"><section class="panel team-space-heading"><div><span class="eyebrow">TEAM WORKSPACE</span><h3>每个人的记录，在这里汇总。</h3><p>独立管理成员与导入报表，计划选择不会改变额度守护。</p></div><div class="segmented team-plan-selector" role="group" aria-label="团队报表计划"><button v-for="plan in ['plus','pro']" :key="plan" :class="{selected:mode===plan}" :disabled="!!busy" :aria-pressed="mode===plan" @click="mode=plan"><Icon :name="plan==='plus'?'bolt':'layers'" :size="16"/>{{ plan==='plus'?'Plus':'Pro' }}</button></div></section><TeamUsage :team="team" :mode="mode" :busy="busy" @action="(m,p,n)=>emit('action',m,p,n)" @error="emit('error',$event)" @notice="emit('notice',$event)"/></div></template>
<style scoped>
.team-space-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.team-space-heading h3{font-size:20px;line-height:1.7;margin:7px 0;color:var(--text)}.team-space-heading p{color:var(--muted);font-size:12px;line-height:1.9}.team-plan-selector{margin:0;width:200px;flex:0 0 auto}.team-plan-selector button{min-width:75px}
@media(max-width:700px){.team-space-heading{align-items:stretch;flex-direction:column;gap:18px}.team-space-heading h3{font-size:18px}.team-plan-selector{width:100%}}
</style>
