import type {ImportResult} from './types'
export function importNotice(result:ImportResult){
 const ignored=(result.skipped || 0)+(result.malformed || 0)
 return `已处理 ${result.files} 份记录，新增 ${result.tokens.toLocaleString('zh-CN')} tokens。重复用量不再计入。${ignored?`忽略 ${ignored} 条无效用量记录。`:''}${result.skipped_links?`跳过 ${result.skipped_links} 个链接或联接点。`:''}`
}
