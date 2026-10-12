<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { SourceKey } from '@/api/types'
import Icon from '@/components/Icon.vue'
import { matchNav, navItems, type NavItem } from '@/composables/navigation'
import { toast } from '@/composables/toast'
import { role, session, signOut } from '@/stores/session'

/**
 * 側邊欄：依角色只列這個人用得到的項目。收合狀態記在 localStorage，
 * 窄螢幕（<1024px）預設收合並改成抽屜——手機上 236px 會吃掉半個畫面。
 */
const props = defineProps<{ freshness: { key: SourceKey; label: string; when: string }[] }>()

const route = useRoute()
const router = useRouter()

const NARROW = 1024
const narrow = ref(false)
const collapsed = ref(false)
/** 窄螢幕時側邊欄是蓋在內容上的抽屜，要有一個獨立的開關，不能沿用 collapsed。 */
const drawerOpen = ref(false)

function readStored(): boolean {
  try {
    return localStorage.getItem('sidebar') === 'mini'
  } catch {
    return false // 無痕視窗讀 localStorage 會丟例外，當成預設展開即可
  }
}

function syncWidth() {
  const wasNarrow = narrow.value
  narrow.value = window.innerWidth < NARROW
  if (narrow.value) {
    collapsed.value = true
    if (!wasNarrow) drawerOpen.value = false
  } else if (wasNarrow) {
    collapsed.value = readStored()
  }
}

const items = computed(() => navItems(role.value))
const mini = computed(() => collapsed.value && !drawerOpen.value)

/** 當前路由落在哪個父項／子項；規則是純函式，這裡只負責畫。 */
const current = computed(() => matchNav(items.value, route.path))

/* ---- 父項展開狀態 ----
 * 跟「收合成 64px」分開存：收合是整條側邊欄的形狀，展開是各群組的狀態，
 * 兩件事混在同一個鍵裡的話，收合再展開就會把使用者原本關掉的群組全部打開。
 */
const GROUPS_KEY = 'sidebar.groups'
/**
 * `null` = 使用者還沒表達過意見，預設只展開「現在所在的那一組」。
 *
 * 不要在 onMounted 就把它算成陣列存起來：側邊欄掛載時 `ensureSession()` 可能還沒回來，
 * 那時的 `role` 是 null、清單是未登入版本，算出來的當前群組會是空的，
 * 使用者第一次按收合鍵會變成「把它加進展開清單」，看起來像沒反應。
 */
const userGroups = ref<string[] | null>(readGroups())

function readGroups(): string[] | null {
  try {
    const raw = localStorage.getItem(GROUPS_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : null
  } catch {
    return null // 無痕視窗或存了壞資料：當成沒設定過，用預設值
  }
}

function saveGroups() {
  try {
    localStorage.setItem(GROUPS_KEY, JSON.stringify(userGroups.value ?? []))
  } catch {
    /* 存不了就只是這次不記得，不影響操作 */
  }
}

/** 現在實際展開的是哪幾組。 */
const openKeys = computed(() =>
  userGroups.value ?? (current.value.key ? [current.value.key] : []),
)

function toggleGroup(item: NavItem) {
  const base = openKeys.value
  userGroups.value = base.includes(item.key)
    ? base.filter((k) => k !== item.key)
    : [...base, item.key]
  saveGroups()
}

// 走到某一組底下時把它打開；使用者站在那裡再手動關掉的話就維持關著（這個 watch 只在換頁時跑）。
watch(
  () => current.value.key,
  (key) => {
    if (key && userGroups.value && !userGroups.value.includes(key)) {
      userGroups.value = [...userGroups.value, key]
      saveGroups()
    }
  },
)

const isOpen = (item: NavItem) => openKeys.value.includes(item.key)

/**
 * 收合時的 flyout 必須用 `position: fixed` 擺，不能只靠 `left: 100%`。
 *
 * `.snav` 是捲動容器（`overflow-y: auto`，CSS 會把 `overflow-x` 一起算成 auto），
 * 子元素往右伸出去只會被裁掉並長出一條橫向捲軸——量過 scrollWidth 227 / clientWidth 63。
 * 所以跳出容器定位，top 在滑進來（或 focus 進來）時照父項的位置算。
 */
function placeFlyout(event: Event) {
  if (!mini.value) return
  const group = event.currentTarget as HTMLElement
  const fly = group.querySelector<HTMLElement>('.flyout')
  if (!fly) return
  const rect = group.getBoundingClientRect()
  fly.style.top = `${Math.min(rect.top, window.innerHeight - fly.offsetHeight - 8)}px`
}

/** 子項亮起要精確比對；父項在子頁面（如 /farmers/:id/log）也要亮。 */
const childActive = (to: string) => current.value.child === to
const parentActive = (item: NavItem) => current.value.key === item.key

onMounted(() => {
  collapsed.value = readStored()
  syncWidth()
  window.addEventListener('resize', syncWidth)
})
onBeforeUnmount(() => window.removeEventListener('resize', syncWidth))

// 換頁時把抽屜收起來，否則點完項目選單還蓋在內容上。
watch(() => route.fullPath, () => (drawerOpen.value = false))

function toggle() {
  if (narrow.value) {
    drawerOpen.value = !drawerOpen.value
    return
  }
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem('sidebar', collapsed.value ? 'mini' : 'open')
  } catch {
    /* 存不了就只是這次不記得，不影響操作 */
  }
}

const roleLabel = computed(() => {
  const value = role.value
  if (value === 'admin') return '管理者'
  if (value === 'doctor') return '植物醫生'
  if (value === 'agency') return '主管機關／改良場'
  if (value === 'farmer') return session.me?.farm_name || '農民'
  return ''
})

const busy = ref(false)
async function leave() {
  busy.value = true
  try {
    await signOut()
    await router.push('/farm') // 未登入進 /farm 會看到登入表單
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <button v-if="narrow" class="drawer-btn" :aria-expanded="drawerOpen" aria-label="開啟導覽選單" @click="toggle">
    <Icon :name="drawerOpen ? 'close' : 'menu'" :size="20" />
  </button>
  <div v-if="narrow && drawerOpen" class="scrim" @click="drawerOpen = false" />

  <aside class="side" :class="{ mini, drawer: narrow, open: drawerOpen }">
    <div class="sbrand">
      <router-link to="/" class="mark" aria-label="回首頁"><Icon name="leaf" :size="17" style="color: #fff" /></router-link>
      <span class="lbl">植物醫生用藥決策平台</span>
      <button class="fold" :title="mini ? '展開側邊欄' : '收合側邊欄'" :aria-label="mini ? '展開側邊欄' : '收合側邊欄'" :aria-expanded="!mini" @click="toggle">
        <Icon name="right" :size="15" :class="{ flip: !mini }" />
      </button>
    </div>

    <nav class="snav" aria-label="主導覽">
      <template v-for="item in items" :key="item.key">
        <span v-if="item.group !== undefined" class="ssep" />
        <span v-if="item.group" class="sgrp">{{ item.group }}</span>

        <!-- 平面項目 -->
        <router-link
          v-if="!item.children"
          :to="item.to"
          :title="item.label"
          :class="{ active: parentActive(item) }"
        >
          <Icon :name="item.icon" :size="18" />
          <span class="lbl">{{ item.label }}</span>
        </router-link>

        <!-- 可展開的父項＋子項 -->
        <div
          v-else
          class="sgroup"
          :class="{ open: isOpen(item) }"
          @mouseenter="placeFlyout"
          @focusin="placeFlyout"
        >
          <div class="parent" :class="{ active: parentActive(item) }">
            <!-- 父項本身可點（到第一個子項），箭頭另外是展開鈕：
                 兩個動作分開，想展開的人不會被帶走，想直接去的人不用先展開。 -->
            <router-link :to="item.to" :title="item.label">
              <Icon :name="item.icon" :size="18" />
              <span class="lbl">{{ item.label }}</span>
            </router-link>
            <button
              class="car lbl"
              :aria-expanded="isOpen(item)"
              :aria-label="`${isOpen(item) ? '收合' : '展開'}${item.label}`"
              @click="toggleGroup(item)"
            >
              <Icon :name="isOpen(item) ? 'down' : 'right'" :size="14" />
            </button>
          </div>
          <div class="kids">
            <router-link
              v-for="kid in item.children"
              :key="kid.to"
              :to="kid.to"
              class="sub"
              :class="{ active: childActive(kid.to) }"
            >
              <span class="lbl">{{ kid.label }}</span>
            </router-link>
          </div>
          <!-- 收合成 64px 時子項沒地方擺，改成 hover 浮出來；鍵盤 focus 也要出得來。 -->
          <div class="flyout">
            <span class="fly-title">{{ item.label }}</span>
            <router-link
              v-for="kid in item.children"
              :key="kid.to"
              :to="kid.to"
              :class="{ active: childActive(kid.to) }"
            >
              {{ kid.label }}
            </router-link>
          </div>
        </div>
      </template>
    </nav>

    <div class="sfoot">
      <div class="sfresh">
        <span class="lbl xs muted">資料更新</span>
        <span v-for="f in props.freshness" :key="f.key" :title="`${f.label} ${f.when}`">
          <i class="dot" :class="f.key" /><span class="lbl">{{ f.label }} {{ f.when }}</span>
        </span>
      </div>

      <div v-if="session.me" class="suser">
        <span class="avatar">{{ session.me.name.slice(0, 1) }}</span>
        <span class="lbl">
          <b>{{ session.me.name }}</b>
          <small>{{ roleLabel }}</small>
        </span>
        <button class="out" title="登出" :disabled="busy" @click="leave"><Icon name="undo" :size="16" /></button>
      </div>
      <router-link v-else to="/farm" class="suser signin" title="登入">
        <span class="avatar"><Icon name="people" :size="16" /></span>
        <span class="lbl"><b>登入</b><small>農民／植物醫生</small></span>
      </router-link>
    </div>
  </aside>
</template>

<style scoped>
.side {
  width: 236px;
  flex: none;
  background: #fff;
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}
.side.mini { width: 64px; }
.side.mini .lbl { display: none; }

.sbrand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 14px;
  border-bottom: 1px solid var(--line);
  font-weight: 700;
  font-size: 15px;
  white-space: nowrap;
}
.sbrand .mark {
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 9px;
  background: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
}
.sbrand .fold {
  margin-left: auto;
  width: 28px;
  height: 28px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: var(--muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex: none;
}
.sbrand .lbl { overflow: hidden; text-overflow: ellipsis; }
.side.mini .sbrand { padding: 8px 0; height: auto; min-height: 84px; flex-direction: column; gap: 6px; }
.side.mini .sbrand .fold { margin: 0; }
.side.mini .sbrand .mark { cursor: pointer; }
.sbrand .fold .flip { transform: rotate(180deg); }

.snav { display: flex; flex-direction: column; gap: 2px; padding: 10px; flex: 1; overflow-y: auto; }
.snav :deep(a) {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  padding: 0 10px;
  border-radius: 9px;
  color: var(--ink-2);
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}
.snav :deep(a:hover) { background: var(--bg); text-decoration: none; }
.snav :deep(a.active) { background: var(--brand-soft); color: var(--brand); font-weight: 600; }
.side.mini .snav :deep(a) { justify-content: center; padding: 0; }

/* ---- 兩層：父項＋縮排的子項 ---- */
.sgroup { position: relative; display: flex; flex-direction: column; }
.parent { display: flex; align-items: center; border-radius: 9px; }
.parent > :deep(a) { flex: 1; min-width: 0; }
.parent.active > :deep(a) { color: var(--brand); font-weight: 600; }
/* 父項只在「當前位置就是父項本身或其子頁」時上底色；展開與否不代表在那裡。 */
.parent.active { background: var(--brand-soft); }
.car {
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0 8px;
  height: 40px;
  display: flex;
  align-items: center;
  flex: none;
}
.kids { display: none; flex-direction: column; gap: 1px; margin: 2px 0 4px; padding-left: 26px; }
.sgroup.open .kids { display: flex; }
.snav :deep(a.sub) { height: 34px; font-size: 13px; padding-left: 12px; position: relative; }
.snav :deep(a.sub)::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--line);
}
.snav :deep(a.sub.active)::before { background: var(--brand); }

/* 收合（64px）：子項改成 hover／focus 浮出 */
.flyout { display: none; }
.side.mini .kids { display: none; }
.side.mini .parent .car { display: none; }
.side.mini .flyout {
  position: fixed;
  left: 70px; /* 64px 的軌道再留 6px 間距；top 由 placeFlyout() 設 */
  margin-left: 0;
  min-width: 168px;
  padding: 6px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 10px 28px rgba(20, 30, 25, 0.14);
  z-index: 60;
  flex-direction: column;
  gap: 1px;
}
.side.mini .sgroup:hover .flyout,
.side.mini .sgroup:focus-within .flyout { display: flex; }
.flyout .fly-title {
  font-size: 11px;
  color: var(--muted);
  padding: 4px 10px 6px;
  letter-spacing: 0.04em;
}
.side.mini .flyout :deep(a) { height: 34px; font-size: 13px; justify-content: flex-start; padding: 0 10px; }

.ssep { height: 1px; background: var(--line); margin: 8px 4px; }
.sgrp { font-size: 11px; color: var(--muted); padding: 4px 10px 2px; letter-spacing: 0.04em; }
.side.mini .sgrp { display: none; }

.sfoot { border-top: 1px solid var(--line); padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.sfresh { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); padding: 4px 6px; }
.sfresh > span { display: flex; align-items: center; gap: 6px; }
.sfresh .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.sfresh .dot.ppm { background: var(--ppm); }
.sfresh .dot.fda { background: var(--fda); }
.sfresh .dot.kdais { background: var(--kdais); }
.side.mini .sfresh { flex-direction: row; justify-content: center; gap: 4px; padding: 4px 0; }

.suser { display: flex; align-items: center; gap: 10px; padding: 6px; border-radius: 9px; }
.suser:hover { background: var(--bg); text-decoration: none; }
.suser .avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--brand-soft);
  color: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  flex: none;
}
.suser .lbl { display: flex; flex-direction: column; line-height: 1.3; font-size: 13px; min-width: 0; }
.suser .lbl b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.suser small { color: var(--muted); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.suser .out { margin-left: auto; color: var(--muted); background: none; border: none; cursor: pointer; flex: none; }
.side.mini .suser { justify-content: center; padding: 6px 0; }
.side.mini .suser .out { display: none; }

/* 窄螢幕：抽屜蓋在內容上 */
.drawer-btn {
  position: fixed;
  left: 12px;
  top: 12px;
  z-index: 60;
  width: 40px;
  height: 40px;
  border: 1px solid var(--line-strong);
  background: #fff;
  border-radius: var(--r-ctl);
  color: var(--ink-2);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.scrim { position: fixed; inset: 0; background: rgba(20, 30, 25, 0.35); z-index: 55; }
.side.drawer { position: fixed; left: 0; top: 0; z-index: 58; transform: translateX(-100%); transition: transform 0.18s ease; }
.side.drawer.open .sbrand { padding-left: 60px; }
.side.drawer.open { width: 236px; transform: translateX(0); box-shadow: 0 12px 32px rgba(20, 30, 25, 0.18); }
.side.drawer:not(.open) { pointer-events: none; }
</style>
