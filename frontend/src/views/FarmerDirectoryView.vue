<script setup lang="ts">
import { computed, ref } from 'vue'

import { type Farmer, planning } from '@/api/planning'
import CreateFarmerModal from '@/components/CreateFarmerModal.vue'
import Icon from '@/components/Icon.vue'
import FarmModal from '@/components/FarmModal.vue'
import PageBar from '@/components/PageBar.vue'
import { toast } from '@/composables/toast'
import { refreshLinks, session } from '@/stores/session'

/**
 * 植物醫生的落地頁：已連結農友清單。
 * 農民進到這裡會被 `router.beforeEach` 導回 `/farm`，所以這頁不再自己判斷角色。
 */
const links = computed(() => session.links)
const filter = ref('')
const shareOpen = ref(false)
const unlinkTarget = ref<Farmer | null>(null)
const busy = ref(false)

const filteredFarmers = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return links.value
  return links.value.filter((f) =>
    [f.name, f.farm_name, f.location, ...(f.crops ?? [])].some((v) => (v ?? '').toLowerCase().includes(q)),
  )
})

function openLinkDialog() {
  shareOpen.value = true
}

async function unlink() {
  if (!unlinkTarget.value) return
  busy.value = true
  try {
    await planning.unlink(unlinkTarget.value.id)
    await refreshLinks()
    unlinkTarget.value = null
    toast.ok('已解除連結')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageBar title="農民管理" :note="`${filteredFarmers.length} 位${session.me?.role === 'admin' ? '農友（管理者）' : '已連結農友'}`">
    <button v-if="session.me?.role !== 'admin'" class="btn sm primary" @click="openLinkDialog"><Icon name="plus" :size="14" />新增農民</button>
  </PageBar>

  <div class="wrap farm-page">
    <div class="directory-toolbar">
          <label class="farmer-search"
            ><Icon name="search" /><input
              v-model="filter"
              type="search"
              placeholder="搜尋農民、農場、地區或作物"
              aria-label="搜尋農民" /></label
          ><span>{{ filteredFarmers.length }} 位農友</span>
        </div>
        <div v-if="links.length && filteredFarmers.length" class="farmer-grid">
          <article
            v-for="farmer in filteredFarmers"
            :key="farmer.id"
            class="card farmer-card"
          >
            <router-link :to="`/farmers/${farmer.id}`"
              ><div class="farmer-card-head">
                <span class="avatar">{{ farmer.name.slice(0, 1) }}</span>
                <div>
                  <h2>{{ farmer.name }}</h2>
                  <p>{{ farmer.farm_name }}</p>
                </div>
                <Icon name="right" />
              </div>
              <p class="farmer-location">
                <Icon name="pin" :size="14" />{{
                  farmer.location || '尚未填寫地區'
                }}
              </p>
              <div v-if="farmer.link_source === 'created'" class="chips">
                <span class="tag brand">你建立的</span>
                <span v-if="farmer.invite?.state === 'pending'" class="tag">等待農民首次登入</span>
                <span v-else-if="farmer.invite?.state === 'expired'" class="tag warn">一次性密碼已過期</span>
              </div>
              <div class="chips">
                <span
                  v-for="(crop, i) in farmer.crops"
                  :key="i"
                  class="tag brand"
                  >{{ crop }}</span
                ><span v-if="!farmer.crops?.length" class="form-hint"
                  >尚未建立作物規劃</span
                >
              </div>
              <div class="farmer-card-footer">
                <span>{{ farmer.plan_count }} 期規劃</span
                ><strong>查看作物與複合圖 →</strong>
              </div></router-link
            ><button v-if="session.me?.role !== 'admin'" class="text-btn unlink-btn" @click="unlinkTarget = farmer">
              解除連結
            </button>
          </article>
        </div>
        <div v-else class="card farm-empty">
          <Icon name="people" :size="38" />
          <h2>{{ links.length ? '找不到相符的農友' : '先連結第一位農友' }}</h2>
          <p>
            {{
              links.length
                ? '試試其他姓名、地區或作物名稱。'
                : '可以直接替農民建立帳號，或請他從「我的農場 → 與植物醫生協作」產生連結碼。'
            }}
          </p>
          <button
            v-if="!links.length"
            class="btn primary"
            @click="shareOpen = true"
          >
            新增第一位農友
          </button>
        </div>

    <CreateFarmerModal
      v-if="shareOpen"
      :links="links"
      @close="shareOpen = false"
      @created="refreshLinks"
      @linked="refreshLinks"
    />
    <FarmModal
      v-if="unlinkTarget"
      title="解除農場連結"
      @close="!busy && (unlinkTarget = null)"
      ><div class="farm-form">
        <p>
          確定解除與「{{
            unlinkTarget.name
          }}」的連結？解除後植物醫生將無法查看或修改農場，既有紀錄仍會保留。
        </p>
        <div class="form-actions">
          <button class="btn" :disabled="busy" @click="unlinkTarget = null">
            取消</button
          ><button class="btn primary" :disabled="busy" @click="unlink">
            確認解除
          </button>
        </div>
      </div></FarmModal
    >
  </div>
</template>
