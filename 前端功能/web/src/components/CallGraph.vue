<script setup>
import { computed, ref } from 'vue'
import { layoutGraph } from '../utils.js'
const props = defineProps({ program: { type: Object, required: true }, selected: { type: String, default: '' } })
const emit = defineEmits(['select'])
const search = ref(''), path = ref(''), zoom = ref(1)
const graph = computed(() => layoutGraph(props.program.symbols, props.program.calls, search.value, path.value))
const unresolved = computed(() => (props.program.calls || []).filter(c => !c.resolved && (!path.value || c.path === path.value) && (!search.value || `${c.callee} ${c.path}`.toLowerCase().includes(search.value.toLowerCase()))))
const focusedEdges = computed(() => graph.value.edges.filter(e => !props.selected || e.source === props.selected || e.target === props.selected))
function curve(edge) {
  if (edge.from.id === edge.to.id) return `M ${edge.from.x + 258} ${edge.from.y + 20} C ${edge.from.x + 306} ${edge.from.y - 25}, ${edge.from.x + 306} ${edge.from.y + 95}, ${edge.from.x + 258} ${edge.from.y + 50}`
  const x1 = edge.from.x + 258, y1 = edge.from.y + 33, x2 = edge.to.x, y2 = edge.to.y + 33
  const delta = x2 > x1 ? Math.max(35, (x2 - x1) / 2) : 45
  return `M ${x1} ${y1} C ${x1 + delta} ${y1}, ${x2 - delta} ${y2}, ${x2} ${y2}`
}
</script>
<template>
  <section class="card graph-card">
    <div class="card-heading"><div><span class="eyebrow">FOLLOW THE CONNECTIONS</span><h2>函数之间，怎样接力？</h2><p>箭头从调用方指向被调用方。点击节点查看输入、步骤、返回值。</p></div><span class="badge">静态调用关系</span></div>
    <div class="graph-tools"><input v-model="search" type="search" placeholder="查找函数 / 模块" aria-label="查找调用图节点"><select v-model="path" aria-label="筛选调用图文件"><option value="">全部文件</option><option v-for="file in program.files" :key="file.path" :value="file.path">{{ file.path }}</option></select><div class="zoom-buttons"><button @click="zoom = Math.max(.5, zoom - .15)" aria-label="缩小">−</button><button @click="zoom = 1">{{ Math.round(zoom * 100) }}%</button><button @click="zoom = Math.min(1.8, zoom + .15)" aria-label="放大">＋</button></div></div>
    <p v-if="graph.hidden" class="inline-note">为保持图形可读，当前显示 120 个节点；还有 {{ graph.hidden }} 个。请按文件或名称缩小范围。</p>
    <div class="graph-canvas" tabindex="0" aria-label="可滚动的函数调用图">
      <svg :width="graph.width * zoom" :height="graph.height * zoom" :viewBox="`0 0 ${graph.width} ${graph.height}`" role="img" aria-label="函数调用图，节点按文件分列，位置不代表执行顺序">
        <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#607dba"/></marker></defs>
        <text v-for="(column, i) in graph.columns" :key="column" :x="28 + i * 310" y="31" class="graph-column">{{ column.length > 32 ? '…' + column.slice(-31) : column }}</text>
        <path v-for="edge in graph.edges" :key="edge.id" :d="curve(edge)" fill="none" :stroke="!selected || focusedEdges.includes(edge) ? '#829bca' : '#dfe5ed'" :stroke-width="focusedEdges.includes(edge) && selected ? 2.5 : 1.5" marker-end="url(#arrowhead)"><title>{{ edge.callee }} · {{ edge.path }}:{{ edge.line }}</title></path>
        <g v-for="node in graph.nodes" :key="node.id" class="graph-node" :class="{ active: selected === node.id }" role="button" tabindex="0" :aria-label="`${node.name}，${node.path} 第 ${node.start_line} 行`" @click="emit('select', node)" @keydown.enter="emit('select', node)" @keydown.space.prevent="emit('select', node)">
          <rect :x="node.x" :y="node.y" width="258" height="66" rx="12"/><rect :x="node.x + 12" :y="node.y + 16" width="32" height="32" rx="8" class="node-icon-bg"/><text :x="node.x + 28" :y="node.y + 38" text-anchor="middle" class="node-icon">{{ node.kind === 'module' ? 'M' : node.kind === 'class' ? 'C' : 'ƒ' }}</text><text :x="node.x + 54" :y="node.y + 28" class="node-title">{{ (node.qualified_name || node.name).slice(0, 23) }}</text><text :x="node.x + 54" :y="node.y + 47" class="node-caption">{{ node.kind === 'module' ? '文件顶层' : node.kind === 'class' ? '类' : '函数' }} · L{{ node.start_line }}–{{ node.end_line }}</text><title>{{ node.qualified_name || node.name }} — {{ node.purpose }}</title>
        </g>
        <text v-if="!graph.nodes.length" x="30" y="100" class="graph-column">没有符合条件的函数或模块。</text>
      </svg>
    </div>
    <div class="graph-legend"><span><i class="legend-line"></i> 已找到项目内的调用目标</span><span>同列属于同一文件；上下位置不代表执行先后</span></div>
    <details class="unresolved"><summary>{{ unresolved.length }} 处调用需要保留边界说明 <small>（内置函数、外部库或动态调用）</small></summary><p>“未解析”不等于代码有错；静态分析无法可靠确定目标时，不猜测连线。</p><div v-for="call in unresolved" :key="call.id" class="unresolved-row"><code>{{ call.callee }}</code><span>{{ call.description }}</span><button class="text-button" @click="emit('select', program.symbols.find(s => s.id === call.source), call.line)">{{ call.path }}:{{ call.line }}</button></div></details>
  </section>
</template>
