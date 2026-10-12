<script setup lang="ts">
import { computed, ref } from 'vue'
import { planning, type LogPhoto, type TrashedPhoto, type WorkLog } from '@/api/planning'
import {
  MOA_MISSING,
  canBackfillRegistration,
  canModifyLog,
  harvestAfter,
  kindOf,
  missingText,
  shortDay,
} from '@/composables/farmLog'
import { toast } from '@/composables/toast'
import type { IconName } from './icons'
import Icon from './Icon.vue'
import PhotoStrip from './PhotoStrip.vue'
import PhotoUploader from './PhotoUploader.vue'

const props = defineProps<{ log: WorkLog; userId: string | null; admin?: boolean; readonly?: boolean; highlighted?: boolean }>()
const emit = defineEmits<{
  edit: [log: WorkLog]
  remove: [log: WorkLog]
  backfill: [log: WorkLog]
  updated: [log: WorkLog]
  refresh: []
}>()

const busy = ref(false)
const kind = computed(() => kindOf(props.log.kind))
const kindIcon = computed(() => kind.value.icon as IconName)
const canModify = computed(() => !props.readonly && (props.admin || canModifyLog(props.log, props.userId)))
const missing = computed(() => props.log.compliance?.missing ?? [])
const snapshot = computed(() => props.log.registration_snapshot)
const moaMissing = computed(() => props.log.kind === 'spray' && !props.log.moa_codes.length)
const backfillable = computed(() => canModify.value && canBackfillRegistration(props.log))
const harvest = computed(() => (snapshot.value ? harvestAfter(props.log.done_on, snapshot.value.phi_days) : null))
const manual = computed(
  () => !props.log.event_id && !props.log.prescription && !props.log.recommendation_id && !snapshot.value,
)

const meta = computed(() => {
  const log = props.log
  const parts: { label: string; value: string }[] = []
  const add = (label: string, value: string) => {
    if (value.trim()) parts.push({ label, value })
  }
  add('', log.field_text)
  add('', log.crop_text)
  add('', log.area_text)
  add('', log.done_time ?? '')
  add(log.kind === 'irrigate' ? '用水' : '用量', [log.amount_value, log.amount_unit].filter(Boolean).join(' '))
  add('產量', [log.yield_value, log.yield_unit].filter(Boolean).join(' '))
  add('去向', log.yield_destination)
  add('批號', log.material_lot)
  add('供應', log.material_supplier)
  add(log.kind === 'scout' ? '觀察' : '針對', log.pest_text)
  add('人力', log.labor_text)
  return parts
})

// 植物醫生替農場記的紀錄要看得出是誰寫的，農民才知道為什麼自己不能改。
const author = computed(() => {
  const role = props.log.created_by_role === 'admin' ? '管理者' : props.log.created_by_role === 'doctor' ? '植物醫生' : '農民'
  return `${props.log.created_by_name || '未知使用者'}（${role}）`
})
const recordedAt = computed(() =>
  new Intl.DateTimeFormat('zh-TW', {
    timeZone: 'Asia/Taipei', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(props.log.created_at)),
)

async function removePhoto(photo: LogPhoto) {
  busy.value = true
  try {
    await planning.deletePhoto(props.log.id, photo.id)
    toast.ok('照片已移到「最近刪除」，保留期內可以還原')
    emit('refresh')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function restorePhoto(photo: TrashedPhoto) {
  busy.value = true
  try {
    const restored = await planning.restorePhoto(props.log.id, photo.id)
    emit('updated', {
      ...props.log,
      photos: [...props.log.photos, restored].sort((a, b) => a.sort_order - b.sort_order),
      trashed_photos: props.log.trashed_photos.filter((item) => item.id !== photo.id),
    })
    toast.ok('照片已還原')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

function addPhoto(photo: LogPhoto) {
  emit('updated', { ...props.log, photos: [...props.log.photos, photo] })
}
</script>

<template>
  <article :id="`log-${log.id}`" class="wlog" :class="{ highlighted }">
    <span class="wlog-icon" :class="kind.tone" :title="kind.name"><Icon :name="kindIcon" :size="20" /></span>
    <div class="wlog-body">
      <div class="wlog-title">{{ log.title }}</div>
      <div v-if="meta.length" class="wlog-meta">
        <template v-for="(part, index) in meta" :key="index"
          ><span v-if="index"> · </span>{{ part.label ? `${part.label} ` : '' }}<b>{{ part.value }}</b></template
        >
      </div>
      <div class="wlog-links">
        <span v-if="log.event_id" class="tag brand">依規劃事項完成</span>
        <span v-if="log.prescription" class="tag info">處方 {{ log.prescription.case_no }}</span>
        <span v-if="log.recommendation_id" class="tag info">來自精準模式建議</span>
        <span v-if="snapshot" class="tag info">藥劑登記</span>
        <span v-if="manual" class="tag info">手動記錄</span>
        <span v-for="code in log.moa_codes" :key="code" class="tag moa">{{ code }}</span>
      </div>
      <p v-if="missing.length" class="wlog-warn"><Icon name="warn" :size="13" />缺{{ missingText(missing) }}（產銷履歷需要）</p>
      <p v-if="moaMissing" class="wlog-warn">
        <Icon name="warn" :size="13" />{{ MOA_MISSING }}
        <button v-if="backfillable" type="button" class="text-btn" @click="emit('backfill', log)">補選登記</button>
      </p>
      <p v-if="snapshot?.crop_matches === false" class="wlog-warn">
        <Icon name="warn" :size="13" />所選登記核准於「{{ snapshot.crop?.name || '其他作物' }}」，不是這筆紀錄的作物
      </p>
      <p v-if="snapshot?.banned" class="wlog-warn crit"><Icon name="warn" :size="13" />所選登記含禁用成分</p>
      <p v-if="harvest && snapshot" class="wlog-phi">安全採收期 {{ snapshot.phi_days }} 天 → {{ shortDay(harvest) }} 起可採</p>
      <p v-if="log.note" class="wlog-note">{{ log.note }}</p>
      <div class="wlog-by">
        <span>{{ author }} 記錄於 {{ recordedAt }}</span>
        <span v-if="log.locked && canModify">已超過 24 小時，可改備註、補照片</span>
        <span v-if="canModify" class="wlog-actions">
          <button type="button" class="text-btn" @click="emit('edit', log)">
            <Icon name="edit" :size="13" />{{ log.locked ? '改備註' : '修改' }}
          </button>
          <button v-if="!log.locked" type="button" class="text-btn danger" @click="emit('remove', log)">
            <Icon name="trash" :size="13" />刪除
          </button>
        </span>
      </div>
    </div>
    <div class="wlog-side">
      <PhotoStrip
        :photos="log.photos"
        :trashed="log.trashed_photos"
        :can-edit="canModify"
        :busy="busy"
        @remove="removePhoto"
        @restore="restorePhoto"
      />
      <PhotoUploader v-if="canModify" compact :log-id="log.id" @uploaded="addPhoto" />
    </div>
  </article>
</template>

<style scoped>
.wlog { display: grid; grid-template-columns: 44px minmax(0, 1fr) 176px; gap: 14px; padding: 14px 16px; border-bottom: 1px solid var(--line); scroll-margin: 90px; transition: background .4s; }
.wlog:last-child { border-bottom: none; }
.wlog.highlighted { background: #fbf8ea; box-shadow: inset 3px 0 0 var(--warn); }
.wlog-icon { width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center; }
.spray { background: #fbebe9; color: #b23b2d; }
.sack { background: #fdf3df; color: #9a6208; }
.drop { background: #e9f0f9; color: #24578f; }
.work { background: #e7f1ea; color: var(--brand); }
.harvest { background: #efe9f6; color: #5b3d8a; }
.soil { background: #f3ece4; color: #7a5a3a; }
.eye { background: #eef0f2; color: #4f5d6b; }
.other { background: #f1f3f0; color: #5d6a62; }
.wlog-body { min-width: 0; }
.wlog-title { font-weight: 600; font-size: 15px; line-height: 1.45; overflow-wrap: anywhere; }
.wlog-meta { font-size: 13px; color: var(--ink-2); margin-top: 2px; overflow-wrap: anywhere; }
.wlog-meta b { font-weight: 600; color: var(--ink); }
.wlog-links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.wlog-warn { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin: 6px 0 0; font-size: 12px; color: var(--warn); }
.wlog-warn.crit { color: var(--crit); }
.wlog-warn .text-btn { padding: 0 0 0 4px; font-size: 12px; font-weight: 600; }
.wlog-phi { margin: 6px 0 0; font-size: 12px; color: var(--ink-2); }
.wlog-note { margin: 6px 0 0; font-size: 13px; line-height: 1.6; color: var(--ink-2); white-space: pre-line; overflow-wrap: anywhere; }
.wlog-by { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; margin-top: 8px; font-size: 12px; color: var(--muted); }
.wlog-actions { display: inline-flex; gap: 12px; margin-left: auto; }
.wlog-actions .text-btn { padding: 2px 0; font-size: 12px; }
.danger { color: var(--crit); }
.wlog-side { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
@media (max-width: 660px) {
  .wlog { grid-template-columns: 40px minmax(0, 1fr); gap: 10px 12px; padding: 12px; }
  .wlog-icon { width: 40px; height: 40px; }
  .wlog-side { grid-column: 2; align-items: flex-start; }
  .wlog-actions { margin-left: 0; }
}
</style>
