<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ArrowRight,
  CircleCheck,
  Collection,
  DocumentChecked,
  EditPen,
  Lock,
  Minus,
  Picture,
  Plus,
  VideoPause,
  VideoPlay,
  View,
  WarningFilled
} from '@element-plus/icons-vue'

import landingScenery from '../assets/landing-scenery.webp'

const emit = defineEmits<{
  start: []
}>()

const landingRoot = ref<HTMLElement | null>(null)
const generationShowcase = ref<HTMLElement | null>(null)
const scrollProgress = ref(0)
let revealObserver: IntersectionObserver | null = null
let generationDemoObserver: IntersectionObserver | null = null
let landingResizeObserver: ResizeObserver | null = null
let progressFrame: number | null = null
let generationDemoTimer: number | null = null
let generationDemoRunId = 0
let generationMotionMedia: MediaQueryList | null = null

const GENERATION_DEMO_STEPS = [
  {
    icon: Picture,
    shortLabel: '校验',
    label: '示例：图片校验',
    message: '格式 · 尺寸 · 文件特征'
  },
  {
    icon: View,
    shortLabel: '理解',
    label: '示例：画面线索',
    message: '湖面 · 树林 · 山体 · 云层倒影'
  },
  {
    icon: EditPen,
    shortLabel: '生成',
    label: '示例：初稿分区',
    message: '图片理解摘要、标题、正文与话题标签'
  },
  {
    icon: CircleCheck,
    shortLabel: '检查',
    label: '示例：结构检查',
    message: '初稿结构就绪，仍需人工核对'
  }
] as const

const generationDemoIndex = ref(0)
const prefersReducedGenerationMotion = ref(false)
const generationDemoVisible = ref(false)
const generationDemoPaused = ref(false)
const currentGenerationDemo = computed(
  () => GENERATION_DEMO_STEPS[generationDemoIndex.value] ?? GENERATION_DEMO_STEPS[0]
)
const generationDemoProgress = computed(
  () => ((generationDemoIndex.value + 1) / GENERATION_DEMO_STEPS.length) * 100
)

function stopGenerationDemo(): void {
  generationDemoRunId += 1
  if (generationDemoTimer !== null) {
    window.clearTimeout(generationDemoTimer)
    generationDemoTimer = null
  }
}

function startGenerationDemo(
  { reset = false }: { reset?: boolean } = {}
): void {
  stopGenerationDemo()
  const prefersReducedMotion =
    generationMotionMedia?.matches ?? window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (prefersReducedMotion) {
    generationDemoIndex.value = GENERATION_DEMO_STEPS.length - 1
    return
  }

  if (!generationDemoVisible.value || generationDemoPaused.value) {
    return
  }
  if (reset) {
    generationDemoIndex.value = 0
  }
  const runId = generationDemoRunId
  const advance = (): void => {
    generationDemoTimer = window.setTimeout(() => {
      if (runId !== generationDemoRunId) {
        generationDemoTimer = null
        return
      }
      generationDemoIndex.value =
        (generationDemoIndex.value + 1) % GENERATION_DEMO_STEPS.length
      advance()
    }, 1500)
  }
  advance()
}

function toggleGenerationDemo(): void {
  generationDemoPaused.value = !generationDemoPaused.value
  if (generationDemoPaused.value) {
    stopGenerationDemo()
    return
  }
  startGenerationDemo()
}

function handleGenerationMotionChange(event: MediaQueryListEvent): void {
  prefersReducedGenerationMotion.value = event.matches
  if (!event.matches) {
    if (generationDemoVisible.value && !generationDemoPaused.value) {
      startGenerationDemo({ reset: true })
    }
    return
  }
  stopGenerationDemo()
  generationDemoIndex.value = GENERATION_DEMO_STEPS.length - 1
}

function updateScrollProgress(): void {
  progressFrame = null
  const root = landingRoot.value
  if (!root) {
    return
  }

  const rootTop = window.scrollY + root.getBoundingClientRect().top
  const scrollableDistance = Math.max(1, root.scrollHeight - window.innerHeight)
  const rawProgress = (window.scrollY - rootTop) / scrollableDistance
  scrollProgress.value = Math.min(1, Math.max(0, rawProgress))
}

function scheduleProgressUpdate(): void {
  if (progressFrame !== null) {
    return
  }
  progressFrame = window.requestAnimationFrame(updateScrollProgress)
}

onMounted(() => {
  generationMotionMedia = window.matchMedia('(prefers-reduced-motion: reduce)')
  prefersReducedGenerationMotion.value = generationMotionMedia.matches
  generationMotionMedia.addEventListener('change', handleGenerationMotionChange)
  scheduleProgressUpdate()
  window.addEventListener('scroll', scheduleProgressUpdate, { passive: true })
  window.addEventListener('resize', scheduleProgressUpdate)

  const root = landingRoot.value
  const showcase = generationShowcase.value
  if (root && 'ResizeObserver' in window) {
    landingResizeObserver = new ResizeObserver(scheduleProgressUpdate)
    landingResizeObserver.observe(root)
  }

  const reducedMotion = generationMotionMedia.matches
  const supportsIntersectionObserver = 'IntersectionObserver' in window
  if (reducedMotion || !supportsIntersectionObserver || !showcase) {
    generationDemoVisible.value = true
    startGenerationDemo({ reset: true })
  } else {
    const observer = new IntersectionObserver(
      entries => {
        if (generationDemoObserver !== observer) {
          return
        }
        generationDemoVisible.value = entries.some(entry => entry.isIntersecting)
        if (!generationDemoVisible.value) {
          stopGenerationDemo()
          return
        }
        startGenerationDemo()
      },
      { threshold: 0.2 }
    )
    generationDemoObserver = observer
    observer.observe(showcase)
  }

  if (!root || reducedMotion || !supportsIntersectionObserver) {
    return
  }

  revealObserver = new IntersectionObserver(
    entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue
        }
        entry.target.classList.add('is-visible')
        revealObserver?.unobserve(entry.target)
      }
    },
    { threshold: 0.12, rootMargin: '0px 0px -7% 0px' }
  )

  const revealTargets = root.querySelectorAll<HTMLElement>('[data-scroll-reveal]')
  try {
    revealTargets.forEach(target => revealObserver?.observe(target))
    root.classList.add('motion-ready')
  } catch {
    revealObserver.disconnect()
    revealObserver = null
    root.classList.remove('motion-ready')
  }
})

onBeforeUnmount(() => {
  stopGenerationDemo()
  generationMotionMedia?.removeEventListener('change', handleGenerationMotionChange)
  generationMotionMedia = null
  revealObserver?.disconnect()
  revealObserver = null
  generationDemoObserver?.disconnect()
  generationDemoObserver = null
  landingResizeObserver?.disconnect()
  landingResizeObserver = null
  window.removeEventListener('scroll', scheduleProgressUpdate)
  window.removeEventListener('resize', scheduleProgressUpdate)
  if (progressFrame !== null) {
    window.cancelAnimationFrame(progressFrame)
    progressFrame = null
  }
})

const FEATURE_ITEMS = [
  {
    number: '01',
    icon: Picture,
    className: 'feature-card--wide',
    title: '结合图片可见信息',
    description: '尝试识别画面与清晰可见的文字，再组织图片理解摘要、标题、正文和话题标签。'
  },
  {
    number: '02',
    icon: EditPen,
    className: '',
    title: '创作提示可选',
    description: '主题或名称、目标读者和表达风格都可以留空；填写后只用于调整方向，不作为图片事实。'
  },
  {
    number: '03',
    icon: DocumentChecked,
    className: '',
    title: '有限结构检查',
    description: '后端检查字段、标题长度、标签数量，并保守拦截部分预定义高风险表述。'
  },
  {
    number: '04',
    icon: Collection,
    className: 'feature-card--wide',
    title: '结果可复制、按账号保存',
    description: '结果可一键复制；同时启用本地账号与 MySQL 后，成功记录会按当前账号隔离。'
  }
] as const

const PROCESS_ITEMS = [
  {
    number: '01',
    title: '上传并校验图片',
    description: '每次上传一张图片；后端会核对扩展名、MIME、文件特征、尺寸与像素限制。'
  },
  {
    number: '02',
    title: '按需补充提示',
    description: '主题或名称、目标读者和表达风格都是可选项，只用于表达方向。'
  },
  {
    number: '03',
    title: '生成结构化初稿',
    description: '文字识别（OCR）会尽力读取清晰文字，视觉模型结合图片生成四个结果分区。'
  },
  {
    number: '04',
    title: '核对、复制与保存',
    description: '逐项核对图片事实和表达后再复制；账号模式下，成功记录会进入当前账号历史。'
  }
] as const

const BOUNDARY_ITEMS = [
  {
    icon: View,
    title: '图片会发送至第三方模型',
    description: '本地后端预处理后，会把图片和可选提示发送至已配置的硅基流动模型服务。'
  },
  {
    icon: WarningFilled,
    title: '检查不等于事实审核',
    description: '系统只执行有限的结构与高风险表述检查，无法保证识别、事实或合规完全正确。'
  },
  {
    icon: Lock,
    title: '密钥留在后端',
    description: '浏览器只请求本地 FastAPI；模型 API Key 由后端环境变量读取，不进入前端代码。'
  }
] as const

const FAQ_ITEMS = [
  {
    question: '哪些内容必须填写？',
    answer: '只有图片是必填项。主题或名称、目标读者和表达风格都可以留空，填写后会用于调整生成方向。'
  },
  {
    question: 'AI 输出可以直接发布吗？',
    answer: '不建议直接发布。AI 可能误读、遗漏或补全图片无法证实的内容，发布前仍需逐项人工核对。'
  },
  {
    question: '图片会发送到哪里？',
    answer: '图片经本地后端校验和预处理后，会发送至已配置的硅基流动文字识别与视觉模型服务。请勿上传敏感或无权处理的图片。'
  },
  {
    question: '系统会检查哪些问题？',
    answer: '后端会检查结果字段、标题长度、标签数量，并拦截一部分预定义高风险表述；这不是完整的事实、广告或法律审核。'
  },
  {
    question: '上传的 HEIC / HEIF 会直接发送给模型吗？',
    answer: '不会。后端会校验单帧 HEIC / HEIF，并统一转换为去除元数据的 RGB JPEG 后再处理。'
  },
  {
    question: '账号和历史记录可以用于公网吗？',
    answer: '演示版提供本地账号与记录归属隔离，但没有公网部署所需的邮件验证、HTTPS 和完整运维防护，不应直接暴露到公网。'
  },
  {
    question: 'API Key 会出现在浏览器里吗？',
    answer: '不会。浏览器只请求本地 FastAPI；硅基流动 API Key 仅由后端环境变量读取。'
  }
] as const
</script>

<template>
  <div class="landing-progress" aria-hidden="true">
    <span :style="{ transform: `scaleX(${scrollProgress})` }"></span>
  </div>
  <section
    ref="landingRoot"
    id="home-panel"
    class="landing"
  >
    <section class="landing-hero" aria-labelledby="page-title">
      <div class="hero-copy">
        <p class="eyebrow">工具介绍 · 图片驱动的小红书内容助手</p>
        <h1 id="page-title" tabindex="-1">
          <span>从一张图片，</span>
          <span>到一篇有方向的</span>
          <em>小红书内容初稿</em>
        </h1>
        <p class="hero-description">
          结合图片中可见内容与可选创作提示，返回图片理解摘要、标题、正文和 3–5 个话题标签。
          AI 可能误读或补全错误，发布前请人工核对。
        </p>
        <div class="hero-actions">
          <button
            id="hero-generate-cta"
            type="button"
            class="primary-action"
            @click="emit('start')"
          >
            <span>开始创作</span>
            <ArrowRight aria-hidden="true" />
          </button>
          <a class="secondary-action" href="#landing-process">查看使用流程</a>
        </div>

        <section
          ref="generationShowcase"
          class="generation-showcase"
          aria-labelledby="generation-showcase-title"
        >
          <div class="generation-showcase__topline">
            <div>
              <small>SIMULATED FLOW / 模拟流程示意</small>
              <h2 id="generation-showcase-title">一张图片如何变成内容初稿</h2>
            </div>
            <button
              v-if="!prefersReducedGenerationMotion"
              type="button"
              class="generation-control"
              aria-describedby="generation-showcase-description"
              :aria-label="generationDemoPaused ? '继续模拟流程' : '暂停模拟流程'"
              @click="toggleGenerationDemo"
            >
              <component :is="generationDemoPaused ? VideoPlay : VideoPause" aria-hidden="true" />
              <span>{{ generationDemoPaused ? '继续演示' : '暂停演示' }}</span>
            </button>
          </div>
          <p id="generation-showcase-description" class="generation-showcase__note">
            仅展示处理阶段；首页不会调用模型，也不代表真实耗时、识别结果或性能。
          </p>
          <ol class="sr-only">
            <li v-for="step in GENERATION_DEMO_STEPS" :key="`description-${step.shortLabel}`">
              {{ step.label }}：{{ step.message }}
            </li>
          </ol>
          <div class="generation-showcase__visual" aria-hidden="true">
            <div class="generation-progress">
              <span :style="{ width: `${generationDemoProgress}%` }"></span>
            </div>
            <Transition name="generation-swap" mode="out-in">
              <div :key="generationDemoIndex" class="generation-stage">
                <span class="generation-stage__icon">
                  <component :is="currentGenerationDemo.icon" />
                </span>
                <div>
                  <small>{{ currentGenerationDemo.label }}</small>
                  <strong>{{ currentGenerationDemo.message }}</strong>
                </div>
              </div>
            </Transition>
            <ol class="generation-steps">
              <li
                v-for="(step, index) in GENERATION_DEMO_STEPS"
                :key="step.shortLabel"
                :class="{
                  'is-active': index === generationDemoIndex,
                  'is-complete': index < generationDemoIndex
                }"
              >
                <span>{{ index + 1 }}</span>
                <small>{{ step.shortLabel }}</small>
              </li>
            </ol>
          </div>
        </section>
      </div>

      <p class="hero-disclosure" role="note">
        本地账号演示。图片生成时由后端发送至第三方视觉模型；请勿上传敏感或无权处理的图片。API Key 仅保存在后端。
      </p>

      <ul class="hero-facts" role="list" aria-label="输出结构概览">
        <li role="listitem"><strong>1 张</strong><span>图片输入</span></li>
        <li role="listitem"><strong>4 个</strong><span>结果分区</span></li>
        <li role="listitem"><strong>≤ 20 字</strong><span>标题约束</span></li>
        <li role="listitem"><strong>3–5 个</strong><span>去重话题标签</span></li>
      </ul>
    </section>

    <section class="problem-statement" aria-labelledby="problem-title" data-scroll-reveal>
      <p class="section-index">01 / 创作起点</p>
      <h2 id="problem-title">画面已经有内容，<br>表达还差一个开始。</h2>
      <p>
        这个工具不替你确认事实，也不替你发布。它先把散落在画面里的线索，整理成一份可以继续修改的内容草稿。
      </p>
    </section>

    <section
      id="landing-process"
      class="landing-section process-section"
      aria-labelledby="process-title"
    >
      <div class="process-visual">
        <div class="process-visual__content" data-scroll-reveal>
          <figure>
            <img
              :src="landingScenery"
              alt="湖面、树林、山体与云层倒影的示例图片"
              width="800"
              height="533"
            >
            <figcaption>界面示意 · 一张普通风景图片</figcaption>
          </figure>
          <p>JPG · JPEG · PNG · WebP · 单帧 HEIC / HEIF</p>
        </div>
      </div>

      <div class="process-copy">
        <div class="section-heading" data-scroll-reveal>
          <p class="eyebrow">使用流程</p>
          <h2 id="process-title">从图片到初稿，四步完成</h2>
          <p>每一步都有明确输入和边界，方便演示，也方便逐项核对。</p>
        </div>

        <ol class="process-list">
          <li v-for="step in PROCESS_ITEMS" :key="step.number" data-scroll-reveal>
            <span>{{ step.number }}</span>
            <div>
              <h3>{{ step.title }}</h3>
              <p>{{ step.description }}</p>
            </div>
          </li>
        </ol>
      </div>
    </section>

    <section
      id="landing-features"
      class="landing-section features-section"
      aria-labelledby="features-title"
    >
      <div class="section-heading section-heading--wide" data-scroll-reveal>
        <p class="eyebrow">功能亮点</p>
        <h2 id="features-title">把生成步骤与使用边界说清楚</h2>
        <p>支持的是一条清晰、可继续编辑的创作流程，而不是“直接发布”的结果承诺。</p>
      </div>

      <div class="feature-grid">
        <article
          v-for="feature in FEATURE_ITEMS"
          :key="feature.number"
          class="feature-card"
          :class="feature.className"
          data-scroll-reveal
        >
          <div class="feature-card__meta">
            <span>{{ feature.number }}</span>
            <component :is="feature.icon" aria-hidden="true" />
          </div>
          <h3>{{ feature.title }}</h3>
          <p>{{ feature.description }}</p>
        </article>
      </div>
    </section>

    <section class="landing-section demo-section" aria-labelledby="demo-title">
      <div class="section-heading section-heading--centered" data-scroll-reveal>
        <p class="eyebrow">结果示意</p>
        <h2 id="demo-title">同一张图片，被整理成四个可检查的部分</h2>
        <p>以下为静态界面示意，不代表真实识别结果、生成耗时或模型性能。</p>
      </div>

      <div class="demo-board">
        <figure class="demo-input" data-scroll-reveal>
          <img
            :src="landingScenery"
            alt="作为生成输入的山湖风景示例"
            width="800"
            height="533"
          >
          <figcaption>
            <span>输入图片</span>
            <strong>山湖风景 · 无额外提示</strong>
          </figcaption>
        </figure>

        <article class="demo-output" aria-labelledby="demo-output-title">
          <div class="demo-output__topline" data-scroll-reveal>
            <span>OUTPUT / 界面示意</span>
            <small>结构检查后仍需人工核对</small>
          </div>
          <div class="demo-field demo-field--summary" data-scroll-reveal>
            <span>图片理解摘要</span>
            <p>画面中可见湖面、树林、山体与云层倒影。</p>
          </div>
          <div class="demo-field" data-scroll-reveal>
            <span>标题</span>
            <h3 id="demo-output-title">把晚霞留在山湖之间</h3>
          </div>
          <div class="demo-field" data-scroll-reveal>
            <span>正文</span>
            <p>傍晚的光落在山脊与湖面上，树林和云层一起映进水里。把这一刻留作今天的户外记录。</p>
          </div>
          <div class="demo-field" data-scroll-reveal>
            <span>话题标签</span>
            <div class="demo-tags" aria-label="示例话题标签">
              <span>#山湖风景</span>
              <span>#户外记录</span>
              <span>#光影时刻</span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section class="landing-section boundary-section" aria-labelledby="boundary-title">
      <div class="boundary-copy" data-scroll-reveal>
        <p class="eyebrow">数据与能力边界</p>
        <h2 id="boundary-title">先说明数据去向，再开始生成</h2>
        <p>
          这是一套本地演示应用，但模型请求并非完全离线。我们把关键边界放在上传动作之前，也把人工核对留在结果之后。
        </p>
      </div>

      <ul class="boundary-list" role="list">
        <li
          v-for="item in BOUNDARY_ITEMS"
          :key="item.title"
          role="listitem"
          data-scroll-reveal
        >
          <component :is="item.icon" aria-hidden="true" />
          <div>
            <h3>{{ item.title }}</h3>
            <p>{{ item.description }}</p>
          </div>
        </li>
      </ul>
    </section>

    <section
      id="landing-faq"
      class="landing-section faq-section"
      aria-labelledby="faq-title"
    >
      <div class="section-heading section-heading--centered" data-scroll-reveal>
        <p class="eyebrow">常见问题</p>
        <h2 id="faq-title">生成之前，先把这些问题说清楚</h2>
      </div>

      <div class="faq-list">
        <details v-for="item in FAQ_ITEMS" :key="item.question">
          <summary>
            <span>{{ item.question }}</span>
            <Plus class="faq-icon faq-icon--closed" aria-hidden="true" />
            <Minus class="faq-icon faq-icon--open" aria-hidden="true" />
          </summary>
          <p>{{ item.answer }}</p>
        </details>
      </div>
    </section>

    <section class="final-cta" aria-labelledby="cta-title">
      <p class="section-index">READY / 从图片开始</p>
      <h2 id="cta-title">准备好一张图片，<br>就可以开始创作。</h2>
      <p>生成内容是可编辑初稿，发布前请再次核对画面信息、事实与表达要求。</p>
      <button
        id="closing-generate-cta"
        type="button"
        class="primary-action primary-action--light"
        @click="emit('start')"
      >
        <span>开始创作</span>
        <ArrowRight aria-hidden="true" />
      </button>
    </section>
  </section>
</template>

<style scoped>
.landing-progress {
  position: fixed;
  z-index: 90;
  top: 0;
  left: 0;
  width: 100%;
  height: 3px;
  pointer-events: none;
}

.landing-progress span {
  display: block;
  width: 100%;
  height: 100%;
  background: #b51630;
  transform-origin: left center;
  transition: transform 80ms linear;
}

.landing {
  --cream: #f8f2e8;
  --cream-deep: #eee3d3;
  --paper: #fffdf8;
  --paper-muted: #f5eee4;
  --ink: #2a1c18;
  --muted: #74665f;
  --line: #ded1c0;
  --accent: #b51630;
  --accent-dark: #891024;
  --accent-soft: #f7dfe3;
  --forest: #244437;
  width: 100%;
  min-width: 0;
  color: var(--ink);
  text-align: left;
}

.landing,
.landing * {
  box-sizing: border-box;
}

.landing :is(h1, h2, h3, p, strong, span, a, button) {
  overflow-wrap: anywhere;
}

.landing-hero {
  display: flex;
  min-height: min(760px, calc(100svh - 126px));
  padding: clamp(72px, 10vw, 132px) clamp(24px, 7vw, 86px) 36px;
  border: 1px solid var(--line);
  border-radius: 32px;
  background: var(--cream);
  flex-direction: column;
  justify-content: space-between;
}

.hero-copy {
  max-width: 980px;
  margin: 0 auto;
  text-align: center;
}

.eyebrow,
.section-index {
  margin: 0;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  line-height: 1.5;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.hero-copy h1 {
  display: grid;
  gap: 0;
  max-width: 1040px;
  margin: 28px auto 0;
  font-family: Georgia, "Times New Roman", "Songti SC", "STSong", serif;
  font-size: clamp(52px, 8.2vw, 108px);
  font-weight: 500;
  line-height: 0.98;
  letter-spacing: -0.065em;
  text-wrap: balance;
}

.hero-copy h1 em {
  color: var(--accent);
  font-style: normal;
}

#page-title:focus-visible {
  outline: 3px solid var(--accent-dark);
  outline-offset: 10px;
  border-radius: 4px;
}

.hero-description {
  max-width: 700px;
  margin: 32px auto 0;
  color: var(--muted);
  font-size: clamp(16px, 1.7vw, 19px);
  line-height: 1.85;
}

.hero-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 34px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

.generation-showcase {
  max-width: 760px;
  margin: 34px auto 0;
  padding: 18px;
  border: 1px solid #d8c8b4;
  border-radius: 18px;
  background: rgba(255, 253, 248, 0.84);
  box-shadow: 0 18px 42px rgba(63, 43, 32, 0.08);
  text-align: left;
}

.generation-showcase__topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.generation-showcase__topline > div {
  display: grid;
  gap: 3px;
}

.generation-showcase__topline small,
.generation-stage small {
  color: var(--accent);
  font-size: 10px;
  font-weight: 800;
  line-height: 1.4;
  letter-spacing: 0.1em;
}

.generation-showcase__topline h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 750;
  line-height: 1.45;
}

.generation-showcase__note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.generation-control {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  gap: 6px;
  min-height: 44px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--ink);
  background: var(--paper);
  font: inherit;
  font-size: 12px;
  font-weight: 750;
  cursor: pointer;
}

.generation-control svg {
  width: 16px;
  height: 16px;
}

.generation-control:focus-visible {
  outline: 3px solid rgba(167, 15, 42, 0.28);
  outline-offset: 3px;
}

.generation-showcase__visual {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fffdf8;
}

.generation-progress {
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--cream-deep);
}

.generation-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
}

.generation-stage {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: center;
  gap: 14px;
  min-height: 76px;
  padding: 14px 4px 12px;
}

.generation-stage__icon {
  display: grid;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  color: #fff;
  background: var(--accent);
  place-items: center;
}

.generation-stage__icon svg {
  width: 21px;
  height: 21px;
}

.generation-stage > div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.generation-stage strong {
  font-family: Georgia, "Times New Roman", "Songti SC", serif;
  font-size: clamp(16px, 2vw, 20px);
  font-weight: 500;
  line-height: 1.4;
}

.generation-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.generation-steps li {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  padding: 7px 5px;
  border-radius: 9px;
  color: #9b8c82;
  background: var(--paper-muted);
  font-size: 11px;
  font-weight: 700;
}

.generation-steps li > span {
  display: grid;
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  border: 1px solid currentColor;
  border-radius: 50%;
  place-items: center;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 10px;
}

.generation-steps li.is-active {
  color: var(--accent-dark);
  background: var(--accent-soft);
}

.generation-steps li.is-complete {
  color: var(--forest);
  background: #e5eee8;
}

.primary-action,
.secondary-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 48px;
  padding: 13px 24px;
  border: 1px solid var(--ink);
  border-radius: 999px;
  font: inherit;
  font-size: 15px;
  font-weight: 750;
  line-height: 1.4;
  text-decoration: none;
}

.primary-action {
  color: #fff;
  background: var(--accent);
  border-color: var(--accent);
  box-shadow: 0 12px 26px rgba(137, 16, 36, 0.2);
  cursor: pointer;
}

.secondary-action {
  color: var(--ink);
  background: var(--paper);
}

.primary-action svg,
.secondary-action svg {
  width: 17px;
  height: 17px;
}

.hero-disclosure {
  max-width: 840px;
  margin: 48px auto 0;
  padding: 15px 18px;
  border: 1px solid #d8c8b4;
  border-radius: 14px;
  color: #66574f;
  background: rgba(255, 253, 248, 0.66);
  font-size: 14px;
  line-height: 1.7;
  text-align: center;
}

.hero-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 52px 0 0;
  padding: 0;
  border-top: 1px solid var(--line);
  list-style: none;
}

.hero-facts li {
  display: grid;
  gap: 5px;
  padding: 22px 16px 0;
  border-left: 1px solid var(--line);
  text-align: center;
}

.hero-facts li:first-child {
  border-left: 0;
}

.hero-facts strong {
  font-family: Georgia, "Times New Roman", "Songti SC", serif;
  font-size: 24px;
  font-weight: 600;
}

.hero-facts span {
  color: var(--muted);
  font-size: 12px;
}

.problem-statement {
  display: grid;
  max-width: 1040px;
  min-height: 520px;
  margin: 0 auto;
  padding: clamp(96px, 13vw, 168px) 24px;
  place-content: center;
  text-align: center;
}

.problem-statement h2 {
  margin: 30px 0 0;
  font-family: Georgia, "Times New Roman", "Songti SC", "STSong", serif;
  font-size: clamp(46px, 7vw, 84px);
  font-weight: 500;
  line-height: 1.08;
  letter-spacing: -0.05em;
  text-wrap: balance;
}

.problem-statement > p:last-child {
  max-width: 650px;
  margin: 32px auto 0;
  color: var(--muted);
  font-size: 17px;
  line-height: 1.9;
}

.landing-section {
  padding: clamp(88px, 10vw, 136px) clamp(24px, 5vw, 64px);
}

.section-heading {
  max-width: 660px;
}

.section-heading--wide {
  max-width: 820px;
}

.section-heading--centered {
  max-width: 790px;
  margin-inline: auto;
  text-align: center;
}

.section-heading h2,
.boundary-copy h2,
.final-cta h2 {
  margin: 20px 0 0;
  font-family: Georgia, "Times New Roman", "Songti SC", "STSong", serif;
  font-size: clamp(38px, 5.4vw, 64px);
  font-weight: 500;
  line-height: 1.08;
  letter-spacing: -0.045em;
  text-wrap: balance;
}

.section-heading > p:last-child,
.boundary-copy > p:last-child {
  margin: 22px 0 0;
  color: var(--muted);
  font-size: 16px;
  line-height: 1.8;
}

.process-section {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  align-items: start;
  gap: clamp(48px, 8vw, 96px);
  border-radius: 30px;
  background: var(--cream);
}

.process-visual {
  position: sticky;
  top: 112px;
  min-width: 0;
}

.process-visual figure {
  overflow: hidden;
  margin: 0;
  border: 1px solid #cfc0ad;
  border-radius: 22px;
  background: var(--paper);
  box-shadow: 0 24px 60px rgba(63, 43, 32, 0.14);
}

.process-visual img,
.demo-input img {
  display: block;
  width: 100%;
  height: auto;
  object-fit: cover;
}

.process-visual figcaption {
  padding: 16px 18px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  background: var(--paper);
  font-size: 13px;
}

.process-visual__content > p {
  margin: 16px 0 0;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
  letter-spacing: 0.04em;
}

.process-copy {
  min-width: 0;
}

.process-list {
  display: grid;
  gap: 0;
  margin: 42px 0 0;
  padding: 0;
  border-top: 1px solid var(--line);
  list-style: none;
}

.process-list li {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 20px;
  padding: 24px 0;
  border-bottom: 1px solid var(--line);
}

.process-list li > span {
  display: grid;
  width: 48px;
  height: 48px;
  border: 1px solid var(--ink);
  border-radius: 50%;
  place-items: center;
  font-family: Georgia, "Times New Roman", serif;
  font-weight: 600;
}

.process-list h3,
.feature-card h3,
.boundary-list h3 {
  margin: 0;
  font-size: 19px;
  line-height: 1.45;
}

.process-list p,
.feature-card p,
.boundary-list p,
.faq-list p {
  margin: 7px 0 0;
  color: var(--muted);
  line-height: 1.75;
}

.features-section {
  padding-inline: 0;
}

.features-section .section-heading {
  padding-inline: clamp(24px, 5vw, 64px);
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 48px;
}

.feature-card {
  display: flex;
  min-height: 300px;
  padding: 28px;
  border: 1px solid var(--line);
  border-radius: 22px;
  background: var(--paper);
  flex-direction: column;
}

.feature-card--wide {
  grid-column: span 2;
}

.feature-card:nth-child(1) {
  background: var(--accent-soft);
}

.feature-card:nth-child(4) {
  color: #fff;
  background: var(--forest);
  border-color: var(--forest);
}

.feature-card:nth-child(4) p,
.feature-card:nth-child(4) .feature-card__meta {
  color: rgba(255, 255, 255, 0.76);
}

.feature-card__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.1em;
}

.feature-card__meta svg {
  width: 28px;
  height: 28px;
}

.feature-card h3 {
  max-width: 360px;
  margin-top: auto;
  padding-top: 80px;
  font-family: Georgia, "Times New Roman", "Songti SC", serif;
  font-size: clamp(25px, 3vw, 36px);
  font-weight: 500;
  letter-spacing: -0.03em;
}

.feature-card p {
  max-width: 560px;
}

.demo-section {
  margin-top: 12px;
  border-radius: 30px;
  background: #2b211d;
  color: #fff;
}

.demo-section .eyebrow {
  color: #f2a2ae;
}

.demo-section .section-heading > p:last-child {
  color: rgba(255, 255, 255, 0.65);
}

.demo-board {
  display: grid;
  grid-template-columns: minmax(0, 0.88fr) minmax(0, 1.12fr);
  align-items: stretch;
  gap: 22px;
  margin-top: 52px;
}

.demo-input,
.demo-output {
  min-width: 0;
  margin: 0;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 20px;
}

.demo-input {
  overflow: hidden;
  background: #1d1714;
}

.demo-input img {
  height: 100%;
  min-height: 500px;
}

.demo-input figcaption {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

.demo-output {
  padding: clamp(24px, 4vw, 42px);
  background: #372b26;
}

.demo-output__topline {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.16);
  color: #f2a2ae;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.1em;
}

.demo-output__topline small {
  color: rgba(255, 255, 255, 0.56);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
}

.demo-field {
  padding: 22px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
}

.demo-field:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.demo-field > span {
  display: block;
  margin-bottom: 9px;
  color: #f2a2ae;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.demo-field h3,
.demo-field p {
  margin: 0;
}

.demo-field h3 {
  color: #fffdf8;
  font-family: Georgia, "Times New Roman", "Songti SC", serif;
  font-size: 27px;
  font-weight: 500;
}

.demo-field p {
  color: rgba(255, 255, 255, 0.78);
  line-height: 1.8;
}

.demo-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.demo-tags span {
  padding: 7px 10px;
  border: 1px solid rgba(242, 162, 174, 0.5);
  border-radius: 999px;
  color: #ffd6dc;
  font-size: 12px;
}

.boundary-section {
  display: grid;
  grid-template-columns: minmax(0, 0.78fr) minmax(0, 1.22fr);
  gap: clamp(48px, 8vw, 96px);
  margin-top: clamp(90px, 12vw, 150px);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.boundary-copy {
  align-self: start;
}

.boundary-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--line);
  list-style: none;
}

.boundary-list li {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 18px;
  padding: 24px 0;
  border-bottom: 1px solid var(--line);
}

.boundary-list svg {
  width: 28px;
  height: 28px;
  color: var(--accent);
}

.faq-section {
  padding-inline: 0;
}

.faq-list {
  max-width: 900px;
  margin: 50px auto 0;
  padding-inline: 16px;
  border-top: 1px solid var(--line);
}

.faq-list details {
  border-bottom: 1px solid var(--line);
}

.faq-list summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 24px;
  align-items: center;
  gap: 20px;
  min-height: 74px;
  padding: 18px 4px;
  color: var(--ink);
  font-weight: 750;
  cursor: pointer;
  list-style: none;
}

.faq-list summary::-webkit-details-marker {
  display: none;
}

.faq-list summary:focus-visible {
  outline: 3px solid var(--accent-dark);
  outline-offset: 4px;
}

.faq-icon {
  grid-column: 2;
  grid-row: 1;
  width: 22px;
  height: 22px;
  color: var(--accent);
}

.faq-icon--open,
.faq-list details[open] .faq-icon--closed {
  display: none;
}

.faq-list details[open] .faq-icon--open {
  display: block;
}

.faq-list p {
  max-width: 760px;
  margin: 0;
  padding: 0 48px 24px 4px;
}

.final-cta {
  display: grid;
  min-height: 580px;
  margin-top: 72px;
  padding: clamp(72px, 10vw, 128px) clamp(24px, 7vw, 86px);
  border-radius: 32px;
  color: #fff;
  background: var(--accent-dark);
  place-content: center;
  text-align: center;
}

.final-cta .section-index {
  color: #ffc4cc;
}

.final-cta h2 {
  max-width: 880px;
  margin-inline: auto;
  font-size: clamp(50px, 7.5vw, 90px);
}

.final-cta > p:not(.section-index) {
  max-width: 680px;
  margin: 28px auto 0;
  color: rgba(255, 255, 255, 0.78);
  font-size: 16px;
  line-height: 1.8;
}

.primary-action--light {
  width: fit-content;
  margin: 34px auto 0;
  color: var(--accent-dark);
  background: var(--paper);
  border-color: var(--paper);
  box-shadow: none;
}

.primary-action:hover,
.secondary-action:hover {
  transform: translateY(-2px);
}

.primary-action:hover {
  background: var(--accent-dark);
  border-color: var(--accent-dark);
}

.primary-action--light:hover {
  color: var(--ink);
  background: #fff;
  border-color: #fff;
}

.primary-action:focus-visible,
.secondary-action:focus-visible {
  outline: 3px solid #5f0818;
  outline-offset: 4px;
}

.primary-action--light:focus-visible {
  outline-color: #fff;
}

@media (prefers-reduced-motion: no-preference) {
  .hero-copy > .eyebrow,
  .hero-copy h1 > span,
  .hero-copy h1 > em,
  .hero-description,
  .hero-actions,
  .hero-facts {
    animation: landing-rise 720ms cubic-bezier(0.22, 1, 0.36, 1) both;
  }

  .hero-copy h1 > span:first-child {
    animation-delay: 70ms;
  }

  .hero-copy h1 > span:nth-child(2) {
    animation-delay: 130ms;
  }

  .hero-copy h1 > em {
    animation-delay: 190ms;
  }

  .hero-description {
    animation-delay: 260ms;
  }

  .hero-actions {
    animation-delay: 330ms;
  }

  .hero-facts {
    animation-delay: 400ms;
  }

  .motion-ready [data-scroll-reveal] {
    opacity: 0;
    transform: translateY(28px);
    transition:
      opacity 680ms cubic-bezier(0.22, 1, 0.36, 1),
      transform 680ms cubic-bezier(0.22, 1, 0.36, 1);
  }

  .motion-ready [data-scroll-reveal].is-visible {
    opacity: 1;
    transform: none;
  }

  .motion-ready .process-list li:nth-child(2),
  .motion-ready .feature-card:nth-child(2),
  .motion-ready .demo-field:nth-child(3),
  .motion-ready .boundary-list li:nth-child(2) {
    transition-delay: 70ms;
  }

  .motion-ready .process-list li:nth-child(3),
  .motion-ready .feature-card:nth-child(3),
  .motion-ready .demo-field:nth-child(4),
  .motion-ready .boundary-list li:nth-child(3) {
    transition-delay: 140ms;
  }

  .motion-ready .process-list li:nth-child(4),
  .motion-ready .feature-card:nth-child(4),
  .motion-ready .demo-field:nth-child(5) {
    transition-delay: 210ms;
  }

  .primary-action,
  .secondary-action,
  .feature-card {
    transition:
      transform 180ms ease,
      box-shadow 180ms ease,
      background-color 180ms ease,
      border-color 180ms ease;
  }

  .primary-action svg,
  .generation-control svg,
  .faq-icon {
    transition: transform 360ms cubic-bezier(0.22, 1, 0.36, 1);
  }

  .generation-progress span {
    transition: width 520ms cubic-bezier(0.22, 1, 0.36, 1);
  }

  .generation-swap-enter-active,
  .generation-swap-leave-active {
    transition:
      opacity 220ms ease,
      transform 220ms ease;
  }

  .generation-swap-enter-from {
    opacity: 0;
    transform: translateY(8px);
  }

  .generation-swap-leave-to {
    opacity: 0;
    transform: translateY(-8px);
  }

  .generation-stage__icon {
    animation: generation-icon-in 360ms cubic-bezier(0.22, 1, 0.36, 1) both;
  }

  .faq-list details[open] .faq-icon--open {
    animation: faq-pop 220ms cubic-bezier(0.22, 1, 0.36, 1) both;
  }

  .faq-list details[open] > p {
    animation: faq-answer-in 280ms ease-out both;
  }
}

@media (prefers-reduced-motion: no-preference) and (hover: hover) and (pointer: fine) {
  .feature-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 20px 48px rgba(63, 43, 32, 0.1);
  }

  .motion-ready .feature-card[data-scroll-reveal].is-visible:hover {
    transform: translateY(-4px);
    transition-delay: 0ms;
    transition-duration: 180ms;
  }

  .primary-action:hover svg {
    transform: translateX(4px);
  }

  .generation-control:hover svg {
    transform: scale(1.08);
  }

  .feature-card__meta svg,
  .process-visual img,
  .demo-input img {
    transition: transform 360ms cubic-bezier(0.22, 1, 0.36, 1);
  }

  .feature-card:hover .feature-card__meta svg {
    transform: rotate(-6deg) scale(1.06);
  }

  .process-visual figure:hover img,
  .demo-input:hover img {
    transform: scale(1.025);
  }
}

@media (prefers-reduced-motion: reduce) {
  .landing-progress {
    display: none;
  }

  .primary-action,
  .secondary-action,
  .feature-card,
  .generation-progress span,
  .generation-swap-enter-active,
  .generation-swap-leave-active,
  .generation-control svg {
    transition: none;
  }
}

@keyframes landing-rise {
  from {
    opacity: 0;
    transform: translateY(24px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes faq-pop {
  from {
    opacity: 0;
    transform: rotate(-35deg) scale(0.72);
  }

  to {
    opacity: 1;
    transform: rotate(0) scale(1);
  }
}

@keyframes faq-answer-in {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes generation-icon-in {
  from {
    opacity: 0;
    transform: scale(0.82);
  }

  to {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 900px) {
  .landing-hero {
    min-height: auto;
    padding-top: 82px;
  }

  .hero-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hero-facts li:nth-child(3) {
    border-left: 0;
  }

  .hero-facts li:nth-child(n + 3) {
    padding-top: 20px;
    border-top: 1px solid var(--line);
  }

  .process-section,
  .boundary-section,
  .demo-board {
    grid-template-columns: 1fr;
  }

  .process-visual {
    position: static;
    width: min(100%, 660px);
    margin-inline: auto;
  }

  .process-copy {
    max-width: 720px;
    margin-inline: auto;
  }

  .feature-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .feature-card--wide {
    grid-column: span 1;
  }

  .demo-input img {
    min-height: auto;
  }
}

@media (max-width: 640px) {
  .landing-hero,
  .process-section,
  .demo-section,
  .final-cta {
    border-radius: 22px;
  }

  .landing-hero {
    padding: 58px 18px 24px;
  }

  .hero-copy h1 {
    font-size: clamp(44px, 15vw, 68px);
    line-height: 1.02;
  }

  .hero-description {
    font-size: 15px;
  }

  .hero-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .primary-action,
  .secondary-action {
    width: 100%;
  }

  .hero-disclosure {
    margin-top: 38px;
    text-align: left;
  }

  .generation-showcase {
    padding: 14px;
  }

  .generation-showcase__topline {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .hero-facts {
    margin-top: 36px;
  }

  .hero-facts li {
    padding-inline: 8px;
  }

  .problem-statement {
    min-height: 430px;
    padding-inline: 12px;
  }

  .problem-statement h2 {
    font-size: clamp(42px, 13vw, 60px);
  }

  .landing-section {
    padding: 72px 18px;
  }

  .features-section,
  .faq-section {
    padding-inline: 0;
  }

  .features-section .section-heading {
    padding-inline: 18px;
  }

  .feature-grid {
    grid-template-columns: 1fr;
  }

  .feature-card {
    min-height: 270px;
    padding: 24px;
  }

  .feature-card h3 {
    padding-top: 62px;
  }

  .demo-output__topline {
    align-items: flex-start;
    flex-direction: column;
  }

  .boundary-section {
    margin-top: 84px;
  }

  .faq-list {
    margin-top: 36px;
  }

  .final-cta {
    min-height: 520px;
    padding-inline: 22px;
  }

  .final-cta h2 {
    font-size: clamp(46px, 14vw, 64px);
  }
}

@media (max-width: 380px) {
  .landing-hero {
    padding-inline: 14px;
  }

  .hero-facts strong {
    font-size: 20px;
  }

  .generation-showcase__topline {
    flex-direction: column;
  }

  .generation-control {
    width: 100%;
  }

  .generation-showcase__visual {
    padding: 12px;
  }

  .generation-stage {
    grid-template-columns: 36px minmax(0, 1fr);
    gap: 10px;
  }

  .generation-stage__icon {
    width: 36px;
    height: 36px;
  }

  .generation-steps li {
    gap: 3px;
    padding-inline: 3px;
  }

  .generation-steps li > span {
    display: none;
  }

  .process-list li {
    grid-template-columns: 44px minmax(0, 1fr);
    gap: 14px;
  }

  .process-list li > span {
    width: 42px;
    height: 42px;
  }

  .demo-output {
    padding: 22px 18px;
  }
}

@media (prefers-contrast: more) {
  .landing {
    --muted: #4f4039;
    --line: #8c7969;
  }

  .primary-action,
  .secondary-action,
  .feature-card,
  .process-visual figure,
  .demo-input,
  .demo-output {
    border-width: 2px;
  }
}

@media (forced-colors: active) {
  .primary-action,
  .secondary-action,
  .feature-card,
  .final-cta,
  .demo-section {
    forced-color-adjust: auto;
  }
}

@media print {
  .landing-progress {
    display: none;
  }

  .motion-ready [data-scroll-reveal] {
    opacity: 1 !important;
    transform: none !important;
  }
}
</style>
