export const PLATFORM_URLS = {
  youtube_shorts_1: 'https://www.youtube.com/shorts/tAJpDB7wlsE',
  youtube_shorts_2: 'https://youtube.com/shorts/FCZ-KiUOz5Q?si=kt_sVIX2eg5GWJUF',
  youtube_shorts_3: 'https://www.youtube.com/shorts/rc1WEQALcJo',
  instagram_reel_1: 'https://www.instagram.com/reel/DR4fyQij0yR/?igsh=M3doOHJuYTcyOWN2',
  instagram_reel_2: 'https://www.instagram.com/reel/DS5mdoQiHlu/?igsh=MXFyeWxuMDRvajdjeQ==',
  tiktok_1:         'https://www.tiktok.com/@philazee2/video/7635371771327286535?_r=1&_t=ZS-9651SvX717d',
  tiktok_short:     'https://vt.tiktok.com/ZSaCoRAL1/',
  facebook_1:       'https://www.facebook.com/share/r/1AUkRgmAof/',
} as const

// Inner-smoke subset (1 per platform)
export const SMOKE_URLS = {
  youtube: PLATFORM_URLS.youtube_shorts_1,
  instagram: PLATFORM_URLS.instagram_reel_1,
  tiktok: PLATFORM_URLS.tiktok_1,
  facebook: PLATFORM_URLS.facebook_1,
} as const
