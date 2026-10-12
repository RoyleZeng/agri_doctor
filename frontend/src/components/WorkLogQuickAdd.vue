<script setup lang="ts">
import { ref } from 'vue'
import type { LogFormMode, Plan, WorkLog } from '@/api/planning'
import FarmModal from './FarmModal.vue'
import WorkLogForm from './WorkLogForm.vue'

/** 桌面是 modal、手機是 bottom sheet；表單本體與事項面板內嵌的是同一個 WorkLogForm。 */
defineProps<{ title: string; farmerId: string; plans: Plan[]; fields?: string[]; mode: LogFormMode }>()
const emit = defineEmits<{ close: []; saved: [log: WorkLog]; 'restore-plan': [planId: string] }>()
const busy = ref(false)
</script>

<template>
  <FarmModal :title="title" wide sheet @close="!busy && emit('close')">
    <WorkLogForm
      :farmer-id="farmerId"
      :plans="plans"
      :fields="fields"
      :mode="mode"
      @busy="busy = $event"
      @saved="emit('saved', $event)"
      @done="emit('close')"
      @cancel="emit('close')"
      @restore-plan="emit('restore-plan', $event)"
    />
  </FarmModal>
</template>
