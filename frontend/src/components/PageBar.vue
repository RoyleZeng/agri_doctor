<script setup lang="ts">
/**
 * 內容頁頂列：左邊頁名與說明，右邊由各頁用 slot 放自己的動作（新增規劃、開單、匯出）。
 * 取代原本農場頁的第二層 sub-nav 與大標題區塊——第三輪回饋說那兩層把複合圖擠到第三屏。
 */
defineProps<{ title: string; note?: string }>()
</script>

<template>
  <div class="pagebar">
    <slot name="lead" />
    <b>{{ title }}</b>
    <span v-if="note" class="note">{{ note }}</span>
    <span class="sp" />
    <slot />
  </div>
</template>

<style scoped>
.pagebar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 56px;
  padding: 8px 28px;
  background: #fff;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  color: var(--muted);
  flex-wrap: wrap;
}
.pagebar b { color: var(--ink); font-size: 15px; }
.pagebar .sp { flex: 1; }
.note { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 1023px) {
  /* 外殼已在上方留出抽屜按鈕高度，不再重複往右縮，避免動作被擠成三行。 */
  .pagebar { padding: 10px 14px; gap: 8px; }
}
</style>
