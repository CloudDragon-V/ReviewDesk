<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { tokenizePython, sourceLines } from '../utils.js'
const props = defineProps({ source: { type: Object, default: null }, line: { type: Number, default: 0 }, endLine: { type: Number, default: 0 } })
const codeRoot = ref(null)
const rows = computed(() => sourceLines(props.source?.content || '').map((text, i) => ({ number: i + 1, tokens: tokenizePython(text) })))
watch(() => [props.source?.path, props.line], async () => {
  await nextTick()
  const target = codeRoot.value?.querySelector(`[data-line="${props.line}"]`)
  if (target && codeRoot.value) codeRoot.value.scrollTop = Math.max(0, target.offsetTop - codeRoot.value.offsetTop - 65)
})
</script>
<template>
  <div class="source-view">
    <div class="source-heading"><span class="file-dot">py</span><strong>{{ source?.path || '尚未选择源码' }}</strong><small v-if="line">定位 L{{ line }}<template v-if="endLine > line">–{{ endLine }}</template></small></div>
    <div v-if="source" ref="codeRoot" class="code-scroll" tabindex="0" aria-label="Python 源代码">
      <div v-for="row in rows" :key="row.number" class="code-line" :class="{ selected: row.number >= line && row.number <= (endLine || line) }" :data-line="row.number"><span class="line-number">{{ row.number }}</span><code><span v-for="(token, index) in row.tokens" :key="index" :class="`syntax-${token.kind}`">{{ token.text }}</span>{{ row.tokens.length ? '' : ' ' }}</code></div>
    </div>
    <p v-else class="empty-small">点击函数或行号，在这里对照原始代码。</p>
  </div>
</template>
