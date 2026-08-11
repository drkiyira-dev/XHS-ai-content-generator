<script setup lang="ts">
const emit = defineEmits<{
  start: []
}>()

const FEATURE_ITEMS = [
  {
    number: '01',
    title: '从图片事实出发',
    description: '先理解画面与包装文字，再生成标题、正文和 3–5 个话题标签。'
  },
  {
    number: '02',
    title: '参数真实参与生成',
    description: '商品名、目标人群和语气都是可选提示，用于调整表达角度与文案风格。'
  },
  {
    number: '03',
    title: '结构与事实双重校验',
    description: '后端检查标题长度、标签数量和高风险事实描述，异常时给出明确提示。'
  },
  {
    number: '04',
    title: '结果可复制、可回溯',
    description: '结果可以一键复制；显式启用本地 MySQL 后，还能查看最近成功记录。'
  }
] as const

const PROCESS_ITEMS = [
  {
    number: '1',
    title: '上传一张图片',
    description: '支持 JPG、PNG、WebP，以及由后端转换的单帧 HEIC / HEIF。'
  },
  {
    number: '2',
    title: '补充可选要求',
    description: '按需填写商品名、目标人群和语气；无法确认的事实仍以图片为准。'
  },
  {
    number: '3',
    title: '等待识图与生成',
    description: 'OCR 尝试读取包装文字，视觉模型结合图片生成固定结构的文案初稿。'
  },
  {
    number: '4',
    title: '复制或再次调整',
    description: '检查结果后直接复制，也可以修改可选参数，再生成一个新的版本。'
  }
] as const

const FAQ_ITEMS = [
  {
    question: '必须填写商品名、目标人群和语气吗？',
    answer: '不需要。只有图片是必填项；三个文本字段都可以留空，填写后会用于调整生成方向。'
  },
  {
    question: '上传的 HEIC / HEIF 会直接发送给模型吗？',
    answer: '不会。后端会校验单帧 HEIC / HEIF，并统一转换为去除元数据的 RGB JPEG 后再处理。'
  },
  {
    question: 'API Key 会出现在浏览器里吗？',
    answer: '不会。浏览器只请求本地 FastAPI；硅基流动 Key 仅由后端环境变量读取。'
  },
  {
    question: '历史记录适合多人或公网使用吗？',
    answer: '当前版本只面向本地单用户演示，没有登录鉴权或用户隔离，不应直接暴露到公网。'
  }
] as const
</script>

<template>
  <section
    id="home-panel"
    class="landing"
    role="region"
    aria-labelledby="home-nav-button"
  >
    <div class="landing-hero">
      <div class="hero-copy">
        <p class="eyebrow">产品介绍 · 图片驱动的内容初稿工具</p>
        <h1>把一张商品图，变成一篇可继续编辑的小红书初稿</h1>
        <p class="hero-description">
          上传图片，补充可选的商品名、目标人群与语气，系统会完成图片理解、
          结构化生成和事实安全校验，再交付标题、正文与标签。
        </p>
        <div class="hero-actions">
          <button
            id="hero-generate-cta"
            type="button"
            class="primary-action"
            @click="emit('start')"
          >
            开始生成
          </button>
          <a class="secondary-action" href="#landing-process">查看使用流程</a>
        </div>
        <p class="local-note">
          本地单用户演示 · 图片生成时由后端发送至第三方视觉模型 · API Key 仅保存在后端
        </p>
      </div>

      <div class="hero-preview" aria-label="生成结果结构示意">
        <div class="preview-window">
          <div class="preview-topbar" aria-hidden="true">
            <span></span><span></span><span></span>
          </div>
          <div class="preview-image" aria-hidden="true">
            <div class="preview-product"></div>
            <div class="preview-label"></div>
          </div>
          <div class="preview-copy">
            <span class="preview-kicker">AI 生成初稿</span>
            <strong>画面信息先确认，种草表达再发生</strong>
            <p>图片摘要、标题、正文和标签分区输出，方便检查与复制。</p>
            <div class="preview-tags" aria-hidden="true">
              <span>#图片理解</span>
              <span>#内容初稿</span>
              <span>#结构化输出</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="trust-strip" aria-label="产品能力概览">
      <div><strong>6 种</strong><span>图片扩展名支持</span></div>
      <div><strong>3–5 个</strong><span>结构化话题标签</span></div>
      <div><strong>≤ 20 字</strong><span>标题硬校验</span></div>
      <div><strong>本地 MySQL</strong><span>显式启用后持久化</span></div>
    </div>

    <section id="landing-features" class="landing-section" aria-labelledby="features-title">
      <div class="section-heading">
        <p class="eyebrow">功能亮点</p>
        <h2 id="features-title">主链稳定，也把边界说清楚</h2>
        <p>把识图、生成、校验与记录放在同一条可验证的流程中。</p>
      </div>

      <div class="feature-grid">
        <article
          v-for="feature in FEATURE_ITEMS"
          :key="feature.number"
          class="feature-card"
        >
          <span>{{ feature.number }}</span>
          <h3>{{ feature.title }}</h3>
          <p>{{ feature.description }}</p>
        </article>
      </div>
    </section>

    <section
      id="landing-process"
      class="landing-section process-section"
      aria-labelledby="process-title"
    >
      <div class="section-heading">
        <p class="eyebrow">使用流程</p>
        <h2 id="process-title">从图片到初稿，只需要四步</h2>
      </div>

      <ol class="process-list">
        <li v-for="step in PROCESS_ITEMS" :key="step.number">
          <span>{{ step.number }}</span>
          <div>
            <h3>{{ step.title }}</h3>
            <p>{{ step.description }}</p>
          </div>
        </li>
      </ol>
    </section>

    <section id="landing-faq" class="landing-section faq-section" aria-labelledby="faq-title">
      <div class="section-heading">
        <p class="eyebrow">FAQ</p>
        <h2 id="faq-title">常见问题</h2>
      </div>

      <div class="faq-list">
        <details v-for="item in FAQ_ITEMS" :key="item.question">
          <summary>{{ item.question }}</summary>
          <p>{{ item.answer }}</p>
        </details>
      </div>
    </section>

    <section class="final-cta" aria-labelledby="cta-title">
      <div>
        <p class="eyebrow">开始制作</p>
        <h2 id="cta-title">准备好一张图片，就可以开始</h2>
        <p>生成内容是可编辑初稿，发布前请再次核对图片事实与品牌要求。</p>
      </div>
      <button
        id="closing-generate-cta"
        type="button"
        class="primary-action"
        @click="emit('start')"
      >
        进入生成工作台
      </button>
    </section>
  </section>
</template>

<style scoped>
.landing {
  --landing-accent: #ff2442;
  --landing-accent-dark: #d81532;
  --landing-ink: #201a24;
  --landing-muted: #726b75;
  --landing-line: #eee7ea;
  color: var(--landing-ink);
  text-align: left;
}

.landing-hero {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
  align-items: center;
  gap: 56px;
  padding: 72px 64px;
  border: 1px solid #f1e5e8;
  border-radius: 28px;
  background:
    radial-gradient(circle at 92% 8%, rgba(255, 36, 66, 0.17), transparent 34%),
    linear-gradient(135deg, #fffdfd 0%, #fff4f6 100%);
  box-shadow: 0 24px 70px rgba(73, 26, 38, 0.09);
}

.landing-hero::before {
  content: '';
  position: absolute;
  width: 210px;
  height: 210px;
  left: -85px;
  bottom: -120px;
  border: 42px solid rgba(255, 36, 66, 0.08);
  border-radius: 50%;
}

.hero-copy,
.hero-preview {
  position: relative;
  z-index: 1;
}

.eyebrow {
  margin: 0 0 14px;
  color: var(--landing-accent-dark);
  font-size: 13px;
  font-weight: 750;
  letter-spacing: 0.13em;
}

.hero-copy h1 {
  max-width: 650px;
  margin: 0;
  color: var(--landing-ink);
  font-size: clamp(38px, 5vw, 62px);
  line-height: 1.1;
  letter-spacing: -0.04em;
}

.hero-description {
  max-width: 620px;
  margin: 24px 0 0;
  color: var(--landing-muted);
  font-size: 17px;
  line-height: 1.8;
}

.hero-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 30px;
}

.primary-action,
.secondary-action {
  box-sizing: border-box;
  min-height: 48px;
  padding: 13px 22px;
  border-radius: 999px;
  font: inherit;
  font-size: 15px;
  font-weight: 700;
  text-decoration: none;
  transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.primary-action {
  border: 1px solid var(--landing-accent);
  color: #fff;
  background: var(--landing-accent);
  box-shadow: 0 12px 26px rgba(255, 36, 66, 0.22);
  cursor: pointer;
}

.secondary-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #ded4d8;
  color: var(--landing-ink);
  background: rgba(255, 255, 255, 0.76);
}

.primary-action:hover,
.secondary-action:hover {
  transform: translateY(-2px);
}

.primary-action:hover {
  background: var(--landing-accent-dark);
  box-shadow: 0 16px 32px rgba(255, 36, 66, 0.27);
}

.primary-action:focus-visible,
.secondary-action:focus-visible {
  outline: 3px solid rgba(255, 36, 66, 0.28);
  outline-offset: 3px;
}

.local-note {
  margin: 22px 0 0;
  color: #817981;
  font-size: 13px;
  line-height: 1.6;
}

.preview-window {
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 26px 65px rgba(67, 22, 34, 0.17);
  transform: rotate(2deg);
}

.preview-topbar {
  display: flex;
  gap: 6px;
  padding: 14px 16px;
  border-bottom: 1px solid #f1e8eb;
}

.preview-topbar span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #e8dce0;
}

.preview-topbar span:first-child {
  background: var(--landing-accent);
}

.preview-image {
  position: relative;
  min-height: 190px;
  overflow: hidden;
  background: linear-gradient(145deg, #f8dedf 0%, #f4ece9 55%, #ead7d7 100%);
}

.preview-product {
  position: absolute;
  width: 102px;
  height: 152px;
  left: 50%;
  bottom: -12px;
  border-radius: 44px 44px 22px 22px;
  background: linear-gradient(180deg, #ffc0c9, #d97888);
  box-shadow: inset 10px 0 22px rgba(255, 255, 255, 0.35), 0 18px 28px rgba(105, 40, 50, 0.18);
  transform: translateX(-50%);
}

.preview-product::before {
  content: '';
  position: absolute;
  width: 62px;
  height: 26px;
  left: 20px;
  top: -18px;
  border-radius: 8px 8px 3px 3px;
  background: linear-gradient(180deg, #ddd5d2, #aaa19e);
}

.preview-label {
  position: absolute;
  width: 230px;
  height: 74px;
  left: 50%;
  top: 74px;
  border-radius: 8px;
  background: rgba(255, 253, 248, 0.93);
  box-shadow: 0 10px 30px rgba(97, 55, 57, 0.12);
  transform: translateX(-50%) rotate(-2deg);
}

.preview-label::before,
.preview-label::after {
  content: '';
  position: absolute;
  left: 50%;
  height: 3px;
  border-radius: 3px;
  background: #553f45;
  transform: translateX(-50%);
}

.preview-label::before {
  width: 92px;
  top: 24px;
}

.preview-label::after {
  width: 56px;
  top: 37px;
  opacity: 0.35;
}

.preview-copy {
  padding: 24px;
}

.preview-kicker {
  display: inline-block;
  margin-bottom: 10px;
  color: var(--landing-accent-dark);
  font-size: 12px;
  font-weight: 750;
}

.preview-copy strong {
  display: block;
  font-size: 19px;
  line-height: 1.45;
}

.preview-copy p {
  margin: 10px 0 0;
  color: var(--landing-muted);
  font-size: 14px;
  line-height: 1.65;
}

.preview-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 18px;
}

.preview-tags span {
  padding: 6px 9px;
  border-radius: 999px;
  color: var(--landing-accent-dark);
  background: #fff1f3;
  font-size: 11px;
}

.trust-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin-top: 20px;
  overflow: hidden;
  border: 1px solid var(--landing-line);
  border-radius: 18px;
  background: var(--landing-line);
}

.trust-strip div {
  display: grid;
  gap: 4px;
  padding: 20px;
  background: #fff;
  text-align: center;
}

.trust-strip strong {
  color: var(--landing-ink);
  font-size: 18px;
}

.trust-strip span {
  color: var(--landing-muted);
  font-size: 12px;
}

.landing-section {
  padding: 88px 24px 0;
}

.section-heading {
  max-width: 650px;
  margin-bottom: 32px;
}

.section-heading h2,
.final-cta h2 {
  margin: 0;
  color: var(--landing-ink);
  font-size: clamp(28px, 4vw, 42px);
  line-height: 1.2;
  letter-spacing: -0.03em;
}

.section-heading > p:last-child,
.final-cta div > p:last-child {
  margin: 14px 0 0;
  color: var(--landing-muted);
  line-height: 1.7;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.feature-card {
  min-height: 190px;
  padding: 28px;
  border: 1px solid var(--landing-line);
  border-radius: 20px;
  background: #fff;
  box-shadow: 0 14px 40px rgba(44, 30, 35, 0.04);
}

.feature-card > span {
  color: var(--landing-accent);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.feature-card h3 {
  margin: 28px 0 10px;
  color: var(--landing-ink);
  font-size: 20px;
}

.feature-card p,
.process-list p,
.faq-list p {
  margin: 0;
  color: var(--landing-muted);
  line-height: 1.75;
}

.process-section {
  display: grid;
  grid-template-columns: minmax(0, 0.72fr) minmax(0, 1.28fr);
  gap: 56px;
}

.process-list {
  display: grid;
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.process-list li {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 18px;
  padding: 20px;
  border: 1px solid var(--landing-line);
  border-radius: 16px;
  background: #fff;
}

.process-list li > span {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: var(--landing-ink);
  font-weight: 800;
}

.process-list h3 {
  margin: 1px 0 6px;
  color: var(--landing-ink);
  font-size: 17px;
}

.faq-section .section-heading {
  margin-inline: auto;
  text-align: center;
}

.faq-list {
  max-width: 820px;
  margin: 0 auto;
  border-top: 1px solid var(--landing-line);
}

.faq-list details {
  border-bottom: 1px solid var(--landing-line);
}

.faq-list summary {
  position: relative;
  padding: 22px 44px 22px 4px;
  color: var(--landing-ink);
  font-weight: 700;
  cursor: pointer;
  list-style: none;
}

.faq-list summary::-webkit-details-marker {
  display: none;
}

.faq-list summary::after {
  content: '+';
  position: absolute;
  right: 8px;
  top: 19px;
  color: var(--landing-accent);
  font-size: 24px;
  font-weight: 500;
}

.faq-list details[open] summary::after {
  content: '−';
}

.faq-list summary:focus-visible {
  outline: 3px solid rgba(255, 36, 66, 0.25);
  outline-offset: 2px;
}

.faq-list p {
  max-width: 760px;
  padding: 0 44px 22px 4px;
}

.final-cta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  margin-top: 88px;
  padding: 42px 48px;
  border-radius: 24px;
  background: linear-gradient(130deg, #fff0f2 0%, #f9e9ef 50%, #f2e8ff 100%);
}

.final-cta div {
  max-width: 680px;
}

.final-cta .primary-action {
  flex: 0 0 auto;
}

@media (prefers-reduced-motion: reduce) {
  .primary-action,
  .secondary-action {
    transition: none;
  }
}

@media (max-width: 900px) {
  .landing-hero {
    grid-template-columns: 1fr;
    padding: 54px 38px;
  }

  .hero-preview {
    width: min(100%, 520px);
    margin: 0 auto;
  }

  .process-section {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .trust-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .landing-hero {
    gap: 38px;
    padding: 42px 22px;
    border-radius: 20px;
  }

  .hero-copy h1 {
    font-size: 38px;
  }

  .hero-description {
    font-size: 15px;
  }

  .hero-actions,
  .final-cta {
    align-items: stretch;
    flex-direction: column;
  }

  .primary-action,
  .secondary-action {
    width: 100%;
    text-align: center;
  }

  .preview-window {
    transform: none;
  }

  .trust-strip,
  .feature-grid {
    grid-template-columns: 1fr;
  }

  .landing-section {
    padding: 64px 4px 0;
  }

  .feature-card {
    min-height: auto;
    padding: 24px;
  }

  .final-cta {
    margin-top: 64px;
    padding: 32px 22px;
  }
}
</style>
