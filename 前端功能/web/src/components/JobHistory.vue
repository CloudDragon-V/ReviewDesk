<script setup>
import { computed } from 'vue'
import { displayDate, describeJobUsage } from '../utils.js'
const props = defineProps({ jobs: { type: Array, default: () => [] }, busy: { type: Boolean, default: false }, error: { type: String, default: '' } })
const emit = defineEmits(['refresh'])
const attention = computed(() => props.jobs.filter(job => ['failed', 'interrupted'].includes(job.status)).length)
const statusNames = { queued: '等待处理', running: '处理中', succeeded: '已完成', failed: '失败', interrupted: '已中断' }
</script>

<template>
  <details class="job-history" @toggle="event => { if (event.target.open) emit('refresh') }">
    <summary>近期任务与中断记录 <span v-if="attention">· {{ attention }} 条失败 / 中断</span></summary>
    <div class="job-history-tools"><p>显示最近 20 条记录。失败或中断的模型请求不会自动重试。</p><button class="text-button" :disabled="busy" @click="emit('refresh')">{{ busy ? '正在刷新…' : '刷新任务记录' }}</button></div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-else-if="!jobs.length" class="inline-note">{{ busy ? '正在读取记录…' : '还没有保存的任务记录。' }}</p>
    <ol v-else class="job-history-list">
      <li v-for="job in jobs" :key="job.id">
        <div class="job-history-heading"><strong>{{ job.kind === 'import' ? '项目导入' : job.kind === 'manual' ? '说明书生成' : '后台任务' }}</strong><span class="badge" :class="{ green: job.status === 'succeeded', amber: ['failed', 'interrupted'].includes(job.status) }">{{ statusNames[job.status] || job.status }}</span><time :datetime="job.updated_at || job.created_at">{{ displayDate(job.updated_at || job.created_at) }}</time></div>
        <p>{{ job.message }}</p>
        <p v-if="job.error" class="job-history-error">{{ job.error }}</p>
        <p v-if="job.result?.export_status === 'failed'" class="job-history-error">报告正文已保留，生成时自动导出失败；请打开对应版本的说明书查看当前保存状态，必要时重试或下载。</p>
        <p class="job-history-usage">{{ describeJobUsage(job) }}</p>
        <small>任务 {{ String(job.id).slice(0, 8) }}</small>
      </li>
    </ol>
  </details>
</template>
