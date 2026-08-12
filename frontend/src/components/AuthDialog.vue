<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

type AuthMode = 'login' | 'register'

const props = defineProps<{
  open: boolean
  mode: AuthMode
  busy: boolean
  serviceUnavailable: boolean
  errorMessage: string
}>()

const emit = defineEmits<{
  close: []
  retrySession: []
  switchMode: [mode: AuthMode]
  submit: [credentials: { email: string; password: string }]
}>()

const form = reactive({
  email: '',
  password: '',
  confirmPassword: ''
})
const localError = ref('')

function clearPasswords() {
  form.password = ''
  form.confirmPassword = ''
}

function switchMode(mode: AuthMode) {
  localError.value = ''
  clearPasswords()
  emit('switchMode', mode)
}

function passwordLength(value: string): number {
  try {
    return Array.from(value.normalize('NFC')).length
  } catch {
    return 0
  }
}

function submit() {
  localError.value = ''
  const email = form.email.trim()
  const password = form.password

  if (!email) {
    localError.value = '请输入邮箱。'
    return
  }

  const normalizedLength = passwordLength(password)
  if (normalizedLength < 15 || normalizedLength > 128) {
    localError.value = '密码规范化后长度需为 15–128 个字符。'
    clearPasswords()
    return
  }

  if (props.mode === 'register' && password !== form.confirmPassword) {
    localError.value = '两次输入的密码不一致。'
    clearPasswords()
    return
  }

  emit('submit', { email, password })
  clearPasswords()
}

watch(
  () => props.open,
  (open) => {
    if (!open) {
      localError.value = ''
      form.email = ''
      clearPasswords()
    }
  }
)

watch(
  () => props.mode,
  () => {
    localError.value = ''
    clearPasswords()
  }
)
</script>

<template>
  <el-dialog
    :model-value="open"
    width="min(92vw, 480px)"
    :close-on-click-modal="!busy"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    destroy-on-close
    align-center
    @close="emit('close')"
  >
    <template #header>
      <div class="auth-heading">
        <p>本地账号演示</p>
        <h2>{{ mode === 'login' ? '登录账号' : '注册新账号' }}</h2>
      </div>
    </template>

    <div class="auth-mode-switch" aria-label="账号操作切换">
      <button
        id="login-mode-button"
        type="button"
        :class="{ active: mode === 'login' }"
        :aria-pressed="mode === 'login'"
        :disabled="busy"
        @click="switchMode('login')"
      >
        登录
      </button>
      <button
        id="register-mode-button"
        type="button"
        :class="{ active: mode === 'register' }"
        :aria-pressed="mode === 'register'"
        :disabled="busy"
        @click="switchMode('register')"
      >
        注册
      </button>
    </div>

    <p v-if="mode === 'register'" class="auth-note">
      邮箱仅作为本地登录标识。本演示不会发送激活邮件，也不会把账号标记为已验证。
    </p>

    <el-alert
      v-if="serviceUnavailable"
      title="账号服务暂时不可用"
      description="请确认后端已启动，或稍后重试会话检查。"
      type="warning"
      :closable="false"
      show-icon
      class="auth-alert"
    >
      <template #default>
        <el-button :disabled="busy" @click="emit('retrySession')">
          重试会话检查
        </el-button>
      </template>
    </el-alert>

    <form class="auth-form" @submit.prevent="submit">
      <div class="auth-field">
        <label for="auth-email">邮箱</label>
        <el-input
          id="auth-email"
          v-model="form.email"
          name="email"
          type="email"
          autocomplete="email"
          inputmode="email"
          :maxlength="1024"
          :disabled="busy"
          placeholder="name@example.com"
        />
      </div>

      <div class="auth-field">
        <label for="auth-password">密码</label>
        <el-input
          id="auth-password"
          v-model="form.password"
          name="password"
          type="password"
          :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
          :maxlength="512"
          :disabled="busy"
          placeholder="规范化后 15–128 个字符"
        />
      </div>

      <div v-if="mode === 'register'" class="auth-field">
        <label for="auth-confirm-password">确认密码</label>
        <el-input
          id="auth-confirm-password"
          v-model="form.confirmPassword"
          name="confirm-password"
          type="password"
          autocomplete="new-password"
          :maxlength="512"
          :disabled="busy"
          placeholder="再次输入密码"
        />
      </div>

      <p class="password-help">
        本站不会把密码写入 URL 或浏览器持久存储；密码仅发送到本地后端用于注册或登录。
        请勿在共享设备保存密码。
      </p>

      <el-alert
        v-if="localError || errorMessage"
        :title="localError || errorMessage"
        type="error"
        :closable="false"
        show-icon
        role="alert"
      />

      <el-button
        native-type="submit"
        type="primary"
        size="large"
        :loading="busy"
        :disabled="busy"
        class="auth-submit"
      >
        {{ mode === 'login' ? '登录' : '注册并登录' }}
      </el-button>
    </form>
  </el-dialog>
</template>

<style scoped>
.auth-heading p {
  margin: 0 0 4px;
  color: #a20f29;
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0.1em;
}

.auth-heading h2 {
  margin: 0;
  color: #241d26;
  font-size: 22px;
}

.auth-mode-switch {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
  padding: 5px;
  border-radius: 12px;
  background: #f5f1f3;
}

.auth-mode-switch button {
  min-height: 44px;
  border: 0;
  border-radius: 9px;
  color: #625a64;
  background: transparent;
  cursor: pointer;
}

.auth-mode-switch button.active {
  color: #fff;
  background: #a70f2a;
  font-weight: 700;
}

.auth-mode-switch button:focus-visible,
.auth-submit:focus-visible {
  outline: 3px solid rgba(167, 15, 42, 0.3);
  outline-offset: 2px;
}

.auth-note,
.password-help {
  color: #665e68;
  font-size: 13px;
  line-height: 1.6;
}

.auth-note {
  margin: 16px 0 0;
  padding: 11px 13px;
  border-left: 3px solid #a70f2a;
  border-radius: 6px;
  background: #fff5f6;
}

.auth-alert {
  margin-top: 16px;
}

.auth-form {
  display: grid;
  gap: 16px;
  margin-top: 20px;
}

.auth-field {
  display: grid;
  gap: 7px;
}

.auth-field label {
  color: #403842;
  font-size: 14px;
  font-weight: 650;
}

.auth-field :deep(.el-input__wrapper) {
  min-height: 44px;
}

.password-help {
  margin: -5px 0 0;
}

.auth-submit {
  width: 100%;
  min-height: 44px;
  margin-top: 2px;
}
</style>
