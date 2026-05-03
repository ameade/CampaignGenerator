import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch, apiPut } from '../api/client'

export const useConfigStore = defineStore('config', () => {
  const values = ref<Record<string, any>>({})
  const models = ref<string[]>([])
  const defaultModel = ref('claude-sonnet-4-6')
  const model = ref('claude-sonnet-4-6')
  const apiKeyPresent = ref(false)
  const cwd = ref('')
  const loaded = ref(false)
  let loadPromise: Promise<void> | null = null

  async function load() {
    if (loadPromise) return loadPromise
    loadPromise = (async () => {
      const [cfg, modelsData, status] = await Promise.all([
        apiFetch('/api/config/'),
        apiFetch('/api/config/models'),
        apiFetch('/api/config/status'),
      ])
      values.value = cfg
      models.value = modelsData.models
      defaultModel.value = modelsData.default
      model.value = cfg.global_model || modelsData.default
      apiKeyPresent.value = status.api_key_present
      cwd.value = status.cwd
      loaded.value = true
    })()
    return loadPromise
  }

  async function save() {
    if (!loaded.value) return
    await apiPut('/api/config/', { values: { ...values.value, global_model: model.value } })
  }

  return { values, models, defaultModel, model, apiKeyPresent, cwd, loaded, load, save }
})
