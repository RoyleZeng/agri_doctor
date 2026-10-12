<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import Icon from './Icon.vue'
defineProps<{ title: string; wide?: boolean; sheet?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
let previous: HTMLElement | null = null
onMounted(() => {
  previous = document.activeElement as HTMLElement
  dialog.value?.showModal()
})
onBeforeUnmount(() => {
  dialog.value?.close()
  previous?.focus()
})
</script>
<template>
  <dialog
    ref="dialog"
    class="farm-modal"
    :class="{ wide, sheet }"
    aria-labelledby="farm-dialog-title"
    @cancel.prevent="emit('close')"
    @click="$event.target === dialog && emit('close')"
  >
    <header>
      <h2 id="farm-dialog-title">{{ title }}</h2>
      <button
        class="btn icon-btn"
        type="button"
        aria-label="關閉視窗"
        @click="emit('close')"
      >
        <Icon name="close" />
      </button>
    </header>
    <div class="modal-body"><slot /></div>
  </dialog>
</template>
<style scoped>
.farm-modal {
  width: min(570px, calc(100% - 24px));
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 0;
  color: var(--ink);
  max-height: calc(100dvh - 40px);
  box-shadow: 0 24px 100px #142a2940;
}
.farm-modal.wide {
  width: min(720px, calc(100% - 24px));
}
.farm-modal::backdrop {
  background: #142a2966;
  backdrop-filter: blur(3px);
}
header {
  position: sticky;
  top: 0;
  background: white;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 24px;
  border-bottom: 1px solid var(--line);
  z-index: 1;
}
h2 {
  font-size: 19px;
}
.icon-btn {
  padding: 0;
  width: 40px;
}
.modal-body {
  padding: 24px;
}
/* 手機上記農事是單手在田邊操作：表單從底部升起，儲存鈕落在拇指範圍。 */
@media (max-width: 660px) {
  .farm-modal.sheet {
    width: 100%;
    max-width: 100%;
    margin: auto 0 0;
    border-radius: 18px 18px 0 0;
    max-height: 94dvh;
  }
}
@media (max-width: 600px) {
  .modal-body {
    padding: 18px;
  }
  header {
    padding: 14px 18px;
  }
}
</style>
