/** 設計稿用的線性圖示路徑（24x24、stroke 1.8）。 */
export const ICON_PATHS = {
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4M17 3v4M3 10h18M7 14h2M12 14h2M7 18h2"/>',
  people: '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5.7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  print: '<path d="M6 9V3h12v6M6 17H3V9h18v8h-3M6 14h12v7H6zM17 11h1"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  note: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/>',
  leaf: '<path d="M4 20c0-8 4-14 16-16-1 12-7 16-16 16Z"/><path d="M4 20c4-6 8-9 12-11"/>',
  warn: '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v5M12 18h.01"/>',
  check: '<path d="m5 12 4 4L19 7"/>',
  external:
    '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m18 15-6-6-6 6"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  // 病害用孢子狀，蟲害用蟲，作物用葉子 —— 一眼分得出候選是哪一類
  disease:
    '<circle cx="12" cy="12" r="5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  bug: '<path d="M8 8a4 4 0 0 1 8 0v1H8Z"/><rect x="6" y="9" width="12" height="11" rx="6"/><path d="M12 9v11M3 12h3M18 12h3M4 19l3-2M20 19l-3-2M4 6l3 2M20 6l-3 2"/>',
  right: '<path d="m9 6 6 6-6 6"/>',
  file: '<path d="M6 3h8l5 5v13H6V3Z"/><path d="M14 3v5h5M9 13h7M9 17h7"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  flask:
    '<path d="M9 3h6M10 3v6L4.5 19a1 1 0 0 0 .9 1.5h13.2a1 1 0 0 0 .9-1.5L14 9V3"/><path d="M7 15h10"/>',
  minus: '<path d="M6 12h12"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  // 農事日誌的作業類型與照片操作
  spray: '<path d="M9 9h6v11a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V9ZM10 9V6h4v3M14 6h2l3-3M17 8h3M17 11h2"/>',
  sack: '<path d="M8.5 4h7L14 7.5c3 1.8 5 5 5 8.5a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4c0-3.5 2-6.7 5-8.5L8.5 4ZM10 7.5h4"/>',
  drop: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11Z"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.2 8.2 20 20M8.2 15.8 20 4"/>',
  basket: '<path d="M3 10h18l-2 10H5L3 10ZM7.5 10l4-6M16.5 10l-4-6M9 14v3M15 14v3"/>',
  shovel: '<path d="m14 4 6 6M17 7l-6.5 6.5M8 11l5 5-3 3a2.8 2.8 0 0 1-4 0l-1-1a2.8 2.8 0 0 1 0-4l3-3Z"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13.5" r="3.5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.8"/><path d="m21 16-5-5-9 9"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4ZM13 7l4 4"/>',
  // 精準模式：感測小卡、圖層切換
  thermometer: '<path d="M12 3a2 2 0 0 0-2 2v9.3a4 4 0 1 0 4 0V5a2 2 0 0 0-2-2Z"/><path d="M12 15v-6"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5M3 8l9 5 9-5"/>',
} as const

export type IconName = keyof typeof ICON_PATHS
