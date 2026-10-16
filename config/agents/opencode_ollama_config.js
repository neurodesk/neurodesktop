import { execFile } from "node:child_process"

function overlay(config) {
  return new Promise((resolve) => {
    const child = execFile(
      "python3",
      ["/opt/neurodesktop/opencode_update_config.py", "--quiet-overlay"],
      { timeout: 10_000, maxBuffer: 65_536 },
      (error, stdout) => {
        if (error) return resolve({})
        try {
          resolve(JSON.parse(stdout))
        } catch {
          resolve({})
        }
      },
    )
    child.stdin.on("error", () => {})
    child.stdin.end(JSON.stringify({
      model: config.model,
      small_model: config.small_model,
      provider: { ollama: config.provider?.ollama },
    }))
  })
}

export const OllamaConfig = async () => {
  if (!process.env.NEURODESKTOP_OPENCODE_OLLAMA_SNAPSHOT) return {}
  return {
    config: async (config) => {
      const delta = (await overlay(config)).provider?.ollama
      if (!delta) return
      const current = config.provider?.ollama ?? {}
      const models = Object.fromEntries(Object.entries(delta.models).map(([id, model]) => [
        id,
        {
          ...current.models?.[id],
          ...model,
          limit: { ...current.models?.[id]?.limit, ...model.limit },
        },
      ]))
      // Native config merges can share provider maps between directory instances.
      config.provider = {
        ...config.provider,
        ollama: {
          ...current,
          ...delta,
          options: { ...current.options, ...delta.options },
          models: { ...current.models, ...models },
        },
      }
    },
  }
}
