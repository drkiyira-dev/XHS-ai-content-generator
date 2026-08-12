import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw
} from 'vue-router'

import HomeView from './views/HomeView.vue'
import { AUTH_ENABLED } from './services/api'
import { authSession } from './state/auth'

export type ProtectedRouteName = 'generate' | 'history'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
  }
}

export function readSafeNext(value: unknown): ProtectedRouteName | null {
  return value === 'generate' || value === 'history' ? value : null
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: HomeView
  },
  {
    path: '/app/generate',
    name: 'generate',
    component: () => import('./views/GenerateView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/app/history',
    name: 'history',
    component: () => import('./views/HistoryView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/login',
    name: 'login',
    component: HomeView
  },
  {
    path: '/register',
    name: 'register',
    component: HomeView
  },
  {
    path: '/:pathMatch(.*)*',
    // 使用绝对 path，避免把 catch-all 的 pathMatch 参数继承给命名路由并
    // 触发 Vue Router 的“discarded invalid params”控制台警告。
    redirect: '/'
  }
]

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(to, _from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    }
    if (to.hash) {
      return { el: to.hash }
    }
    return { top: 0 }
  }
})

router.beforeEach(async (to) => {
  const isAuthRoute = to.name === 'login' || to.name === 'register'

  if (!AUTH_ENABLED) {
    return isAuthRoute ? { name: 'home', replace: true } : true
  }

  if (to.meta.requiresAuth) {
    await authSession.ensureRestored()
    if (authSession.status.value === 'authenticated') {
      return true
    }
    const next: ProtectedRouteName = to.name === 'history' ? 'history' : 'generate'
    return {
      name: 'login',
      query: { next },
      replace: true
    }
  }

  if (isAuthRoute) {
    await authSession.ensureRestored()
    if (authSession.status.value === 'authenticated') {
      return {
        name: readSafeNext(to.query.next) ?? 'generate',
        replace: true
      }
    }
  }

  // The public landing page must not wait for a slow account probe, while the
  // shared header still needs the eventual session state. A later protected
  // navigation will join this same single-flight task.
  void authSession.ensureRestored()

  return true
})

export default router
