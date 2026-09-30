<script setup>
import { computed, ref, watch } from 'vue'
import { renderManual, characterCount, safeDownloadUrl, paginateManual, describeUsage, parseSourceReference } from '../utils.js'
const props = defineProps({ manual: { type: Object, required: true }, token: { type: String, required: true }, exportBusy: { type: Boolean, default: false } })
const emit = defineEmits(['locate', 'retry-export'])
const pageIndex = ref(0)
const pages = computed(() => paginateManual(props.manual.markdown || ''))
const rendered = computed(() => renderManual(pages.value[pageIndex.value] || ''))
const count = computed(() => props.manual.character_count ?? characterCount(props.manual.markdown))
const download = computed(() => safeDownloadUrl(props.manual.download_url || `/api/manuals/${encodeURIComponent(props.manual.id)}/download`, props.token))
function scrollTo(id) { document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
function locateSource(event) {
  const button = event.target.closest?.('button[data-source-path]')
  if (!button || !event.currentTarget.contains(button)) return
  const location = parseSourceReference(`reviewdesk-source:${encodeURIComponent(button.dataset.sourcePath)}?start=${button.dataset.sourceStart}&end=${button.dataset.sourceEnd}`)
  if (location) emit('locate', { ...location, revision_id: props.manual.revision_id })
}
watch(() => props.manual.id, () => { pageIndex.value = 0 })
</script>
<template>
  <div class="manual-status"><div><span class="badge" :class="{ green: manual.mode === 'local', amber: manual.mode === 'partial' }">{{ manual.mode === 'partial' ? '部分模型解释 · 请查看边界说明' : manual.mode === 'ai' || manual.mode === 'deepseek' ? 'DeepSeek + 代码证据' : '本地代码分析' }}</span><span>{{ Number(count).toLocaleString('zh-CN') }} 字符 <small>（不含空白）</small></span></div><a v-if="download" class="button primary" :href="download" download>↓ 下载完整 Markdown</a></div>
  <p v-if="manual.export_status === 'failed'" class="notice error" role="alert"><span>说明书正文和用量已保存在本机数据库，自动保存 Markdown 文件失败：{{ manual.export_error || '请检查文件占用或目录写入权限。' }} 仍可下载完整内容。</span><button class="button" :disabled="exportBusy" @click="emit('retry-export', manual.id)">{{ exportBusy ? '正在重试保存…' : '重试保存文件（不调用模型）' }}</button></p>
  <p v-else-if="count > 5000" class="notice success">这份说明超过 5000 字。{{ manual.auto_exported ? '后台已自动保存 Markdown 文档。' : '可通过下载按钮获取完整 Markdown 文档。' }}你可以在下方继续阅读，也可以下载完整文件。</p>
  <p v-if="manual.document_path" class="inline-note">文档位置：{{ manual.document_path }}</p>
  <details v-if="manual.limitations?.length" class="card boundaries"><summary>本次说明的覆盖范围与边界</summary><ul><li v-for="(limit, index) in manual.limitations" :key="index">{{ limit }}</li></ul></details>
  <div v-if="pages.length > 1" class="manual-pagination"><span>全文较长，已分成 {{ pages.length }} 部分展示；下载仍包含完整内容。</span><div class="button-row"><button class="button" :disabled="pageIndex === 0" @click="pageIndex--">上一部分</button><select v-model="pageIndex" aria-label="选择说明书阅读部分"><option v-for="(_, index) in pages" :key="index" :value="index">第 {{ index + 1 }} / {{ pages.length }} 部分</option></select><button class="button" :disabled="pageIndex >= pages.length - 1" @click="pageIndex++">下一部分</button></div></div>
  <div v-if="manual.usage" class="usage-line">{{ describeUsage(manual.usage) }}<details><summary>查看用量原始记录</summary><pre>{{ JSON.stringify(manual.usage, null, 2) }}</pre></details></div>
  <p class="inline-note">点击带下划线的源码引用，可查看这份说明书对应版本的代码。旧报告的纯文本位置需重新生成后才能点击。</p>
  <div class="manual-layout"><aside class="manual-toc"><strong>阅读目录</strong><button v-for="heading in rendered.headings" :key="heading.id" :class="{ sub: heading.level > 2 }" @click="scrollTo(heading.id)">{{ heading.text }}</button></aside><article class="markdown-body" @click="locateSource" v-html="rendered.html"></article></div>
</template>
