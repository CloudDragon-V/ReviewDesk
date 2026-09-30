<script setup>
defineProps({ finding: { type: Object, required: true } })
const emit = defineEmits(['locate'])
</script>

<template>
  <div class="finding">
    <span class="badge amber">{{ finding.severity || '提示' }}</span>
    <div>
      <strong>{{ finding.title }}</strong>
      <pre v-if="finding.evidence" class="finding-evidence">{{ finding.evidence }}</pre>
      <p v-if="finding.recommendation">建议：{{ finding.recommendation }}</p>
      <button class="text-button" @click="emit('locate', finding.path, finding.start_line, finding.end_line)">{{ finding.path }}:{{ finding.start_line }}</button>
    </div>
  </div>
</template>
