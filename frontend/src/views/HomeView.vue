<script setup lang="ts">
import { useRouter } from 'vue-router'

import LandingPage from '../components/LandingPage.vue'

const router = useRouter()

function startGeneration(): void {
  void router.push({ name: 'generate' })
}
</script>

<template>
  <LandingPage @start="startGeneration" />
</template>
