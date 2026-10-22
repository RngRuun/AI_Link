<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 })
const paths: Record<string, string> = {
  archive:'M3 3h18v5H3zM5 8v13h14V8M9 12h6',
  cloud:'M6 18a5 5 0 0 1-1-9.9 7 7 0 0 1 13.2-1.6A5.8 5.8 0 0 1 19 18H6Z',
  pin:'m16 3 5 5-3 1-4 5-1 4-7-7 4-1 5-4 1-3ZM9 15l-6 6',
  users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  moon:'M20 15.5A8.7 8.7 0 0 1 8.5 4 8.7 8.7 0 1 0 20 15.5Z',
  arrow: 'M7 17 17 7M7 7h10v10', network: 'M4 8c4.4-4 11.6-4 16 0M7 12c2.8-2.5 7.2-2.5 10 0m-7 4c1.1-1 2.9-1 4 0m-2 4h.01',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  shield: 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6',
  pulse: 'M2 12h5l3-8 4 16 3-8h5', settings: 'M4 7h16M4 17h16M8 4v6m8 4v6',
  clock: 'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  power: 'M12 2v10m-5-8a9 9 0 1 0 10 0', refresh: 'M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3',
  chevron: 'm9 5 7 7-7 7', down: 'm6 9 6 6 6-6', check: 'm5 12 4 4L19 6',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5', play: 'm8 4 12 8-12 8V4Z', pause: 'M8 4v16M16 4v16',
  info: 'M12 11v6m0-10h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  folder: 'M3 7V4h6l3 3h9v13H3V7Z', bolt: 'm13 2-9 12h7l-1 8L20 9h-7l0-7Z',
  close: 'm6 6 12 12M6 18 18 6', link: 'm10 14 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 2 2-2a4 4 0 1 1 6 6l-5 5a4 4 0 0 1-6 0',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c4 5 4 13 0 18-4-5-4-13 0-18',
  terminal: 'm4 5 6 6-6 6m8 2h8', sun: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  layers: 'm3 7 9-5 9 5-9 5-9-5Zm0 5 9 5 9-5M3 17l9 5 9-5',
  upload: 'M12 17V3m-5 5 5-5 5 5M4 17v4h16v-4'
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.grid"/></svg></template>
