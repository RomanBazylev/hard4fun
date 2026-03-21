# PROJECT_SPEC.md — GlitchRealityAI
## Полная техническая спецификация проекта

> **Версия:** 1.0  
> **Дата:** 2026-03-20  
> **Статус:** Production-ready, free tier  
> **Цель этого документа:** полный контекст проекта — для восстановления работы, онбординга, обновлений, и разговоров с AI-ассистентами.

---

## 1. Суть проекта

Полностью автоматизированный **faceless YouTube Shorts канал** в нише **"Glitch in Reality"** (английский язык).  
Работает на 100% бесплатных инструментах на старте. Архитектура позволяет переключиться на платные API (ElevenLabs, Kling) за 5 минут без рефакторинга.

**Что делает:** каждый день в 00:00 UTC GitHub Actions запускает пайплайн, который:
1. Выбирает 4 промпта из базы (рандомизированные)
2. Генерирует видео через Hugging Face Spaces (Wan2.1 / HunyuanVideo / CogVideoX)
3. Озвучивает текст через Microsoft Edge TTS (edge-tts)
4. Монтирует Short через FFmpeg (glitch-эффект + субтитры + фоновая музыка)
5. Генерирует thumbnail (кадр из видео + текстовый оверлей)
6. Загружает на YouTube через Data API v3

---

## 2. Параметры канала

| Параметр | Значение |
|---|---|
| Название | GlitchRealityAI |
| Ниша | Glitch in Reality / Simulation Glitches / Surreal Absurd Moments |
| Язык | Английский (голос + субтитры + теги) |
| Формат | Вертикальное видео 9:16, 1080×1920px |
| Длина | 5–12 секунд |
| Частота | 4 видео/день (настраивается: 3–5) |
| Запуск CI | 00:00 UTC + 14:00 UTC (cron) + ручной trigger |
| Стиль | mind-bending, WTF, brainrot, simulation error, reality breaking |

---

## 3. Технологический стек

### Free tier (стартовая конфигурация)

| Компонент | Технология | Версия | Почему |
|---|---|---|---|
| Видео-генерация | Hugging Face Gradio Spaces | API | Бесплатно, несколько моделей |
| TTS | edge-tts (Microsoft Edge TTS) | 7.2.7+ | Полностью бесплатно, 4 голоса EN |
| Монтаж | FFmpeg | system | Стандарт, бесплатно, мощный |
| Субтитры | FFmpeg drawtext | — | Встроен в FFmpeg |
| Upload | YouTube Data API v3 | — | Бесплатно (OAuth2) |
| CI/CD | GitHub Actions | ubuntu-latest | 2000 мин/мес бесплатно |
| Идеи (опц.) | Gemini API free tier | 1.5-flash | 15 RPM бесплатно |
| Конфиг | PyYAML + Pydantic | — | Типизация, валидация |

### Платные upgrade-пути (не активны до монетизации)

| Что менять | На что | Где в коде | Где добавить секрет |
|---|---|---|---|
| TTS | ElevenLabs (`eleven_turbo_v2`) | `config.yaml` → `tts.provider: "elevenlabs"` | `ELEVENLABS_API_KEY` |
| TTS | OpenAI TTS (`tts-1`) | `config.yaml` → `tts.provider: "openai"` | `OPENAI_API_KEY` |
| Видео | Kling API (`kling-v1-5`) | `config.yaml` → `video.provider: "kling"` | `KLING_API_KEY` |
| Видео | Runway Gen-3 | `config.yaml` → `video.provider: "runway"` | `RUNWAY_API_KEY` |

**Принцип апгрейда:** одна строка в `config.yaml` + один секрет в GitHub. Никакого рефакторинга.

---

## 4. Архитектура и файловая структура

```
glitch-reality-ai-youtube/
│
├── pipeline.py                    # Главный оркестратор. Точка входа.
│                                  # Запускается GitHub Actions.
│                                  # Вызывает все остальные модули.
│
├── prompts/
│   ├── __init__.py
│   ├── ideas.json                 # База промптов:
│   │                              #   18 базовых идей в 5 темах
│   │                              #   10 style_suffixes (рандомно инжектятся)
│   │                              #   14 random_details (рандомно инжектятся)
│   │                              #   шаблоны voice_line
│   └── daily_prompts.py           # Модуль выборки промптов:
│                                  #   select_daily_prompts(count)
│                                  #   generate_ai_ideas(count) через Gemini
│                                  #   save_ai_ideas() добавляет в ideas.json
│                                  #   трекинг used_prompts.json (последние 100)
│
├── scripts/
│   ├── __init__.py
│   ├── generate_video.py          # Генерация видео:
│   │                              #   generate_video(prompt, output_path, config)
│   │                              #   Диспатчер: huggingface | kling | runway
│   │                              #   HF: перебирает Spaces с retry + fallback
│   │                              #   Kling/Runway: poll jobs до completion
│   │
│   ├── tts.py                     # Text-to-Speech:
│   │                              #   generate_tts(text, output_path, config)
│   │                              #   Диспатчер: edge-tts | elevenlabs | openai
│   │                              #   edge-tts: async, ротация 4 голосов
│   │
│   ├── montage.py                 # FFmpeg сборка финального Short:
│   │                              #   assemble_short(video, audio, text, output)
│   │                              #   Scale → 9:16 (1080×1920)
│   │                              #   Glitch-эффект (RGB shift + grain + scanlines)
│   │                              #   Burn-in subtitles (drawtext, bottom)
│   │                              #   Mix TTS + background music (vol 15%)
│   │                              #   H.264 + AAC, faststart
│   │
│   ├── thumbnail.py               # Генерация thumbnail:
│   │                              #   generate_thumbnail(video, hook, output)
│   │                              #   Извлекает кадр из первых 30% видео
│   │                              #   Оверлей: "REALITY GLITCH ⚠️" + hook
│   │
│   └── upload.py                  # YouTube Upload:
│                                  #   upload_short(video, thumbnail, hook)
│                                  #   OAuth2 refresh token flow (CI-friendly)
│                                  #   Resumable upload
│                                  #   Auto-SEO: title/desc/tags генерация
│                                  #   10 шаблонов заголовков, 20 дефолтных тегов
│
├── config/
│   ├── config.yaml                # Все настройки проекта (НЕ секреты)
│   └── secrets.example.yaml       # Шаблон — показывает нужные переменные
│
├── assets/
│   ├── fonts/
│   │   └── Impact.ttf             # Автоскачивается в GitHub Actions
│   └── music/
│       └── README.txt             # Инструкция + источники бесплатной музыки
│                                  # .mp3 треки класть сюда, gitignored
│
├── .github/workflows/
│   └── daily-shorts.yml           # GitHub Actions workflow:
│                                  #   Triggers: cron 00:00 + 14:00 UTC + manual
│                                  #   Inputs: video_count, dry_run, refresh_ideas
│                                  #   Steps: checkout → python → ffmpeg → validate
│                                  #          → run pipeline → upload artifacts
│                                  #          → commit prompts → notify on fail
│
├── logs/
│   ├── .gitkeep
│   ├── used_prompts.json          # Отслеживание использованных ID (коммитится)
│   └── results_YYYY-MM-DD.json    # Результаты каждого прогона (gitignored)
│
├── requirements.txt               # Python зависимости
├── .gitignore
├── README.md                      # Полная документация пользователя
└── PROJECT_SPEC.md                # Этот файл — техническая спецификация
```

---

## 5. Data Flow — полный поток данных

```
GitHub Actions cron (00:00 UTC)
          │
          ▼
  pipeline.py::run_pipeline(count=4)
          │
          ├─ prompts/daily_prompts.py::select_daily_prompts(4)
          │      ├── loads prompts/ideas.json
          │      ├── excludes IDs in logs/used_prompts.json
          │      ├── injects random style_suffix + random_detail
          │      ├── saves used IDs back to logs/used_prompts.json
          │      └── returns list[dict]: id, hook, visual_prompt, voice_line
          │
          └─ for each prompt → process_one_video()
                │
                ├─[1] scripts/generate_video.py::generate_video(visual_prompt)
                │       ├── provider = config.video.provider ("huggingface")
                │       ├── tries Space[0] Wan2.1 → POST /run/predict
                │       ├── on fail: sleep 30–120s, retry ×3
                │       ├── on Space fail: try Space[1] HunyuanVideo
                │       ├── on fail: try Space[2] CogVideoX
                │       └── saves raw .mp4 to /tmp/workdir/
                │
                ├─[2] scripts/tts.py::generate_tts(voice_line)
                │       ├── provider = config.tts.provider ("edge-tts")
                │       ├── random voice from config.tts.voices
                │       └── saves .mp3 to /tmp/workdir/
                │
                ├─[3] scripts/montage.py::assemble_short(video, audio, text)
                │       ├── scale + pad → 1080×1920 (ffmpeg)
                │       ├── RGB glitch effect (chromatic aberration filter)
                │       ├── grain + scanlines
                │       ├── burn-in subtitles (drawtext, bottom, Impact)
                │       ├── pick random music from assets/music/
                │       ├── mix: voice vol=1.0 + music vol=0.15
                │       └── encode H.264 + AAC, faststart → final .mp4
                │
                ├─[4] scripts/thumbnail.py::generate_thumbnail(video, hook)
                │       ├── extract frame at t = random(0.5, duration*0.3)
                │       ├── drawbox black bar + "REALITY GLITCH ⚠️" yellow
                │       └── hook text at bottom → .jpg
                │
                └─[5] scripts/upload.py::upload_short(video, thumbnail, hook)
                        ├── refresh_access_token(client_id, secret, refresh_token)
                        ├── build_title() — random from 10 templates
                        ├── build_tags() — 20 tags from config + dedup
                        ├── build_description() — template + hashtags
                        ├── resumable upload → YouTube API v3
                        ├── set_thumbnail()
                        └── returns video_id → logs
```

---

## 6. Конфигурационные параметры

Все параметры в `config/config.yaml`. Ключевые:

```yaml
channel.daily_videos: 4          # Видео/день (3–5)
channel.video_duration_max: 12   # Макс длина Short в секундах

video.provider: "huggingface"     # huggingface | kling | runway
video.resolution: "576x1024"      # Нативное разрешение генерации
video.num_frames: 81              # ~5с при 16fps

tts.provider: "edge-tts"          # edge-tts | elevenlabs | openai
tts.voices: [...]                 # 4 EN голоса в ротации
tts.speed: "+10%"                 # Чуть быстрее для brainrot

montage.glitch_overlay: true      # RGB-shift + grain эффект
montage.music_volume: 0.15        # Громкость фоновой музыки (0–1)

youtube.privacy_status: "public"  # public | unlisted | private
youtube.default_tags: [...]       # 20 дефолтных SEO тегов

prompts.daily_count: 10           # Сколько новых идей генерировать через AI
```

---

## 7. GitHub Secrets

| Secret | Обязательный | Откуда взять |
|---|---|---|
| `YOUTUBE_CLIENT_ID` | ✅ | Google Cloud Console → OAuth2 |
| `YOUTUBE_CLIENT_SECRET` | ✅ | Google Cloud Console → OAuth2 |
| `YOUTUBE_REFRESH_TOKEN` | ✅ | Одноразовый OAuth скрипт (см. README) |
| `GEMINI_API_KEY` | Опционально | aistudio.google.com (бесплатно) |
| `HF_TOKEN` | Опционально | huggingface.co/settings/tokens |
| `ELEVENLABS_API_KEY` | Для апгрейда | elevenlabs.io |
| `KLING_API_KEY` | Для апгрейда | klingai.com |
| `RUNWAY_API_KEY` | Для апгрейда | runwayml.com |
| `GITHUB_TOKEN` | Авто | Предоставляется GitHub Actions автоматически |

---

## 8. Антидубликация контента

YouTube может пессимизировать каналы с "reused AI content". Защита:

| Механизм | Как реализован |
|---|---|
| Уникальность промпта | `style_suffixes` (10 вариантов) × `random_details` (14) = 140 комбинаций на каждую идею |
| Уникальность голоса | 4 разных Edge TTS голоса в ротации |
| Уникальность видео | Рандомный seed при каждом вызове Gradio Space |
| Ротация моделей | 3 разные HF Spaces → разный визуальный стиль |
| Трекинг ID | `logs/used_prompts.json` не повторяет последние 100 ID |
| AI-генерация новых идей | Gemini API добавляет свежие промпты в `ideas.json` |

---

## 9. Обработка ошибок

| Уровень | Стратегия |
|---|---|
| HF Space недоступен | Retry ×3 + sleep random(30–120s) → fallback на следующий Space |
| Все Spaces упали | Видео помечается `failed`, остальные продолжаются |
| TTS упал | `RuntimeError` → промпт помечается `failed` |
| FFmpeg ошибка субтитров | Warning + продолжение без субтитров (graceful fallback) |
| FFmpeg ошибка музыки | Warning + продолжение с TTS only |
| YouTube upload упал | Retry ×3 с экспоненциальным sleep |
| YouTube token просрочен | Refresh token flow, автоматически |
| N из 4 видео упали | Остальные успешно загружаются, ошибки логируются |
| Все видео упали | `sys.exit(1)` → GitHub Actions помечает run как failed |
| Любой сбой в run | Создаётся GitHub Issue с меткой `auto-error` |

---

## 10. Мониторинг и логи

- **GitHub Actions logs** — основной мониторинг, доступен в Actions tab
- **Artifacts** — `logs/` архивируется как artifact на 30 дней после каждого run
- **`logs/results_YYYY-MM-DD.json`** — структурированные результаты каждого прогона:
  ```json
  {
    "run_at": "2026-03-20T00:01:23Z",
    "results": [
      {"id": "eg_001", "hook": "...", "status": "success", "video_id": "abc123", "url": "https://youtu.be/abc123"},
      {"id": "ng_002", "hook": "...", "status": "failed", "error": "All HF Spaces exhausted"}
    ]
  }
  ```
- **GitHub Issues** — автоматически открывается при сбоях (если `GITHUB_TOKEN` настроен)
- **`logs/used_prompts.json`** — коммитится в репо, хранит последние 100 использованных ID

---

## 11. SEO-стратегия

### Заголовки (10 шаблонов в ротации)
```
"This Glitch Just Broke Reality 😱 #shorts"
"The Simulation Made an Error 🔴 #shorts"
"Wait… This Shouldn't Be Possible 😳 #shorts"
"Reality.exe Has Stopped Working 💀 #shorts"
"The Matrix Glitched AGAIN 👁️ #shorts"
... (10 всего)
```

### Теги (20 дефолтных)
`shorts, glitch, realityglitch, simulationglitch, glitcheffect, surreal, mindblowing, wtf, brainrot, aiglitch, glitchinthematrix, simulationtheory, realitybreaking, glitchyoutube, aiart, weirdvideo, glitchvideo, surrealart, mindblowingmoments, youtubeshortsglitch`

### Описание
Автогенерация: hook → шаблон + hashtags + призыв к подписке + ссылка на канал

---

## 12. Roadmap и будущие улучшения

### Приоритет 1 — когда есть деньги
- [ ] Upgrade TTS → ElevenLabs (качество голоса)
- [ ] Upgrade video → Kling API (качество видео)
- [ ] A/B тестирование заголовков через YouTube Analytics API

### Приоритет 2 — рост канала
- [ ] Google Sheets интеграция для идей (бесплатно)
- [ ] Автоматический анализ views/retention для лучших идей
- [ ] Серийный контент ("recurring glitch character")
- [ ] Scheduling по пиковым часам аудитории (не только 00:00 UTC)

### Приоритет 3 — масштаб
- [ ] Multi-language версии (испанский, хинди, португальский)
- [ ] Несколько каналов в параллель (другие niches)
- [ ] Собственная fine-tuned модель на glitch-контент

---

## 13. Известные ограничения и решения

| Ограничение | Влияние | Решение |
|---|---|---|
| HF Free Spaces медленные | Видео генерируется 3–10 мин | 3 Spaces в fallback-цепочке |
| HF Spaces offline в пиках | Могут упасть 17–22 UTC | Второй cron в 14:00 UTC |
| edge-tts требует интернет | Нет fallback offline | OK — GitHub Actions всегда онлайн |
| YouTube upload quota | 10,000 units/day (хватает ~5–6 видео) | 4 видео/день = ~1,600 units |
| GitHub Actions free tier | 2,000 мин/мес | ~60 мин/run × 30 дней = 1,800 мин (впритык) |
| Нет фоновой музыки по умолчанию | Видео без музыки | Добавить .mp3 в `assets/music/` вручную |

---

## 14. Восстановление после сбоя

### "YouTube refresh token expired" (раз в 6 месяцев без использования)
```bash
# Запустить локально OAuth скрипт из README.md → обновить YOUTUBE_REFRESH_TOKEN в Secrets
```

### "All HF Spaces exhausted"
1. Добавить новые Spaces в `config.yaml` → `video.huggingface.spaces`
2. Или временно добавить `HF_TOKEN` (приоритетный доступ)

### "Pipeline stopped running" (Actions отключились)
GitHub отключает Actions на репо без активности после 60 дней.  
Решение: Пуш любого коммита раз в 50 дней (или включить в Secrets `KEEP_ALIVE=1` и добавить workflow для еженедельного коммита).

### Полный сброс used-промптов
```bash
echo '[]' > logs/used_prompts.json && git add logs/used_prompts.json && git commit -m "reset: clear used prompts" && git push
```

---

## 15. Инструкции для AI-ассистента

При работе с этим проектом в будущем (Claude, GPT, etc.):

**Контекст:** это репо — автоматизированный YouTube Shorts канал "GlitchRealityAI". Python 3.12, GitHub Actions, бесплатные инструменты.

**Ключевые принципы архитектуры:**
1. **Диспатчеры** — каждый модуль (video, tts) имеет словарь `PROVIDERS` и одну публичную функцию (`generate_video`, `generate_tts`). Добавить новый провайдер = добавить функцию + запись в словарь.
2. **Конфиг-первый** — всё поведение управляется `config/config.yaml`, не хардкодом.
3. **Graceful fallback** — ошибка одного видео не останавливает остальные.
4. **Секреты только в GitHub Secrets** — никогда в коде или config.yaml.

**При добавлении новой фичи:**
- Видео-провайдер: добавить функцию в `scripts/generate_video.py` + запись в `PROVIDERS`
- TTS-провайдер: аналогично в `scripts/tts.py` + `TTS_PROVIDERS`
- Новые промпты: добавить в `prompts/ideas.json` в соответствующую тему
- Новая тема: добавить новый ключ в `ideas["themes"]`

**При отладке:**
```bash
python pipeline.py --dry-run --count 1   # полный прогон без API
python prompts/daily_prompts.py --count 3 --output text  # проверка промптов
python scripts/tts.py --text "test" --output /tmp/t.mp3  # тест TTS
```
