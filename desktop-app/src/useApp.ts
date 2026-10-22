import { ref, computed, onMounted, onUnmounted } from 'vue'
import type { AppState } from './types'
export function useApp() {
  const state = ref<AppState | null>(null)
  const busy = ref('')
  const importActive = ref(false)
  const connectionError = ref('')
  const toast = ref<{ text: string; error: boolean } | null>(null)
  let polling = false, timer: ReturnType<typeof setInterval>, toastTimer: ReturnType<typeof setTimeout>
  const initialTime = Date.now()
  const notify = (text: string, error = false) => { toast.value = { text, error }; clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.value = null, error ? 9000 : 4500) }
  async function refresh() {
    if (polling || importActive.value) return
    polling = true
    try { state.value = await window.aiLink.request<AppState>('status'); connectionError.value = '' }
    catch (error) { if (!importActive.value && Date.now() - initialTime > 12000) connectionError.value = String(error).replace(/^.*?Error: /, '') }
    finally { polling = false }
  }
  async function run(method: string, params: Record<string, unknown> = {}, success = '操作已完成。') {
    if (busy.value || importActive.value) return false
    busy.value = method
    // Vue proxies cannot cross Electron's structured-clone boundary. The service
    // protocol accepts JSON only; normalize nested reactive arrays before IPC.
    try { const payload = JSON.parse(JSON.stringify(params)) as Record<string, unknown>; await window.aiLink.request(method, payload); notify(success); void refresh(); return true }
    catch (error) { notify(String(error).replace(/^.*?Error: /, ''), true); return false }
    finally { busy.value = '' }
  }
  async function exportReport() { if(importActive.value)return notify('记录正在导入，请完成后导出报告。');try { if (await window.aiLink.exportReport()) notify('诊断报告已导出。') } catch (error) { notify(String(error), true) } }
  let unsubscribeImport:(()=>void)|undefined
  onMounted(() => {unsubscribeImport=window.aiLink.onImportActivity(active=>{importActive.value=active;if(!active)void refresh()});void refresh(); timer = setInterval(refresh, 1500) })
  onUnmounted(() => { clearInterval(timer); clearTimeout(toastTimer);unsubscribeImport?.() })
  return { state, busy:computed(()=>importActive.value?'usage.import':busy.value), connectionError, toast, run, refresh, notify, exportReport, online: computed(() => !!state.value && !connectionError.value) }
}
