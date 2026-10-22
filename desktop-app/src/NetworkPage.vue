<script setup lang="ts">
import { computed } from 'vue'
import Icon from './Icon.vue'
import NetworkInsights from './NetworkInsights.vue'
import type { AppState } from './types'
const props = defineProps<{ state: AppState; busy: string }>()
const emit = defineEmits<{ action: [method: string, params: Record<string, unknown>, message: string]; navigate: [page: string] }>()
const active = computed(() => props.state.system.owned && props.state.relay)
const pending = computed(() => props.state.operation.running)
const number = (n: number) => new Intl.NumberFormat('zh-CN', { notation: n > 99999 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(n)
const bytes = computed(() => { const n = props.state.telemetry.counters.uploaded + props.state.telemetry.counters.downloaded; return n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(1) + ' KB' })
const metrics = computed(() => [
  { label: '当前连接', value: number(props.state.telemetry.counters.active), icon: 'link', note: '正在传输的加密隧道' },
  { label: '成功建连', value: number(props.state.telemetry.counters.connected), icon: 'check', note: '本次运行累计' },
  { label: '建连重试', value: number(props.state.telemetry.counters.retries), icon: 'refresh', note: '仅在隧道建立前重试' },
  { label: '累计传输', value: bytes.value, icon: 'layers', note: '上传与下载合计' }
])
const services = computed(() => Object.entries(props.state.providers).map(([key, provider]) => ({ key, ...provider, last: [...props.state.telemetry.history].reverse().find(s => s.provider === key) })))
const outcomes: Record<string, string> = { reachable: 'HTTP 可达', restricted: '服务可达 · 访问受限', method_unsupported: '服务可达 · 方法受限', failed: '连接失败', tls_error: '证书校验失败', interrupted: '连接已中断', service_error: '服务返回错误', closed: '服务已关闭连接', cancelled: '检测已取消' }
function toggle() { emit('action', active.value ? 'network.disable' : 'network.enable', {}, active.value ? '网络优化已关闭，原代理已恢复。' : '正在验证连接，通过后自动开启优化。') }
</script>
<template><div class="workspace-page">
  <section class="hero network-hero" :class="{ 'is-active': active }">
    <div class="hero-copy">
      <div class="eyebrow"><span class="status-dot" :class="{ live: active, pending }"></span>{{ active ? 'NETWORK PROTECTED' : pending ? 'VERIFYING CONNECTION' : 'NETWORK OPTIMIZATION' }}</div>
      <h2>连接更从容，<br><span>专注你的下一步。</span></h2>
      <p>为 Codex、Gemini 等 AI 应用提供统一连接入口，<br class="desktop-break">在网络波动时，为建连多留一份余地。</p>
      <div class="button-row">
        <button class="btn primary" :disabled="!!busy || pending" @click="toggle"><Icon :name="busy || pending ? 'refresh' : 'power'" :class="{ spinning: pending }"/>{{ pending ? '正在验证连接' : active ? '关闭网络优化' : '开启网络优化' }}</button>
        <button class="btn ghost" @click="emit('navigate','diagnostics')">检测连接<Icon name="arrow" :size="17"/></button>
      </div>
      <span class="hero-footnote"><Icon name="shield" :size="14"/>保留原始 TLS 加密 · 随时恢复原代理</span>
    </div>
    <div class="network-art" aria-hidden="true">
      <svg class="topology-lines" viewBox="0 0 320 232" preserveAspectRatio="none"><path class="topology-rail" d="M55 52C111 52 103 111 160 111S209 180 269 180"/><path v-if="active" class="topology-flow" d="M55 52C111 52 103 111 160 111S209 180 269 180"/><circle cx="55" cy="52" r="3"/><circle cx="269" cy="180" r="3"/></svg>
      <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="orbit orbit-three"></div>
      <div class="network-core"><Icon name="network" :size="42"/><span>AI LINK</span></div>
      <span class="satellite sat-top"><Icon name="terminal"/>Codex</span><span class="satellite sat-bottom"><Icon name="bolt"/>Gemini</span>
      <span class="art-caption"><i :class="{ live: active }"></i>{{ active ? '优化已开启' : '准备就绪，等待开启' }}</span>
    </div>
  </section>
  <div v-if="state.operation.error" class="notice warning" role="alert"><Icon name="info"/><span>{{ state.operation.error }}</span></div>
  <section class="metric-grid" aria-label="本次运行统计">
    <article v-for="metric in metrics" :key="metric.label" class="metric-card"><div class="metric-top"><span>{{ metric.label }}</span><Icon :name="metric.icon" :size="18"/></div><strong class="metric-number">{{ metric.value }}</strong><span class="metric-note">{{ metric.note }}</span></article>
  </section>
  <section class="panel connection-path">
    <div class="section-heading"><div><span class="eyebrow muted">CONNECTION ROUTE</span><h3>当前连接路径</h3></div><span class="badge" :class="{ success: active }">{{ active ? '优化中继已接入' : '沿用现有系统代理' }}</span></div>
    <div class="route-line"><div class="route-node"><span class="route-icon"><Icon name="terminal"/></span><div><strong>AI 应用</strong><small>遵循系统代理的连接</small></div></div><span class="route-connector" :class="{ active }"></span><div class="route-node" :class="{ faded: !active }"><span class="route-icon"><Icon name="shield"/></span><div><strong>{{ active ? 'AI Link 中继' : 'AI Link 待开启' }}</strong><small>127.0.0.1:{{ state.relay_port }}</small></div></div><span class="route-connector" :class="{ active }"></span><div class="route-node"><span class="route-icon"><Icon name="globe"/></span><div><strong>本地上游</strong><small>127.0.0.1:{{ state.config.upstreams[0]?.port }}</small></div></div></div>
    <p class="micro-copy">系统代理：{{ state.system.enabled ? state.system.server : '未开启' }} <span>·</span> 已有连接不会自动迁移</p>
  </section>
  <section class="ai-services">
    <div class="section-heading"><div><h3>AI 服务连接</h3><p>检测结果来自实际 TLS 与 HTTP 请求。</p></div><button class="text-button" @click="emit('navigate','diagnostics')">查看诊断<Icon name="chevron" :size="16"/></button></div>
    <div class="service-grid"><article v-for="(service, index) in services" :key="service.key" class="service-card"><div class="service-logo" :class="`logo-${index}`"><Icon :name="index % 2 === 0 ? 'terminal' : 'bolt'" :size="22"/></div><div class="service-info"><strong>{{ service.name }}</strong><span>{{ service.last ? outcomes[service.last.outcome] || service.last.outcome : '尚未检测' }}</span></div><span class="latency">{{ service.last?.latency_ms ? `${Math.round(service.last.latency_ms)} ms` : '—' }}</span></article></div>
    <p class="micro-copy section-note"><Icon name="info" :size="14"/>HTTP 可达不代表模型权限或生成功能已验证；已经断开的会话仍需 AI 应用自行重连。</p>
  </section>
<NetworkInsights :state="state" :busy="busy" @action="(m,p,n)=>emit('action',m,p,n)" @navigate="emit('navigate',$event)"/>
</div></template>
