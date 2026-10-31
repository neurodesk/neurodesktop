// The quiet launcher observes Ollama once and passes the bounded snapshot in
// NEURODESKTOP_OPENCODE_OLLAMA_SNAPSHOT. OpenCode applies configured providers
// after provider transforms, so the overlay runs as a model transform: it sees
// each location's resolved Ollama provider, including project configuration
// opened after the server started, and never writes user configuration.
const OPENAI_COMPATIBLE = new Set([
  "",
  "@opencode/ai/providers/openai-compatible",
  "aisdk:@ai-sdk/openai-compatible",
])
const FALLBACK_OUTPUT_LIMIT = 8192

function readSnapshot() {
  try {
    const snapshot = JSON.parse(process.env.NEURODESKTOP_OPENCODE_OLLAMA_SNAPSHOT ?? "")
    if (
      typeof snapshot?.base_url !== "string" ||
      !snapshot.contexts ||
      !Array.isArray(snapshot.managed)
    ) {
      return undefined
    }
    return snapshot
  } catch {
    return undefined
  }
}

function positive(value) {
  return Number.isInteger(value) && value > 0 ? value : undefined
}

function isUnchangedSeed(seed, model) {
  return (
    seed !== undefined &&
    model !== undefined &&
    model.name === seed.name &&
    model.limit?.context === seed.limit?.context &&
    model.limit?.output === seed.limit?.output
  )
}

function isManaged(provider, snapshot) {
  const baseURL = provider.settings?.baseURL
  return (
    OPENAI_COMPATIBLE.has(provider.package ?? "") &&
    (!baseURL || snapshot.managed.includes(baseURL) || baseURL === snapshot.base_url)
  )
}

function applyOllamaSnapshot(editor, snapshot) {
  const record = editor.provider.get("ollama")
  if (!record || !isManaged(record.provider, snapshot)) return
  const seeds = new Map(Object.entries(snapshot.seeds ?? {}))
  const discovered = new Map(Object.entries(snapshot.contexts))

  for (const id of new Set([...record.models.keys(), ...discovered.keys()])) {
    const current = record.models.get(id)
    const seeded = isUnchangedSeed(seeds.get(id), current)
    const discoveredContext = discovered.get(id)
    if (discoveredContext === undefined && seeded) {
      // Removing a configured model has no effect; disabling hides it.
      editor.update("ollama", id, (model) => {
        model.enabled = false
      })
      continue
    }
    editor.update("ollama", id, (model) => {
      // Configured provider settings win over transforms, so the observed
      // endpoint travels on each model instead of on the provider.
      model.settings = { ...model.settings, baseURL: snapshot.base_url }
      if (discoveredContext === undefined) return
      const saved = seeded ? {} : current?.limit ?? {}
      const context = positive(saved.context) ?? discoveredContext
      model.name ||= id
      model.limit = {
        ...model.limit,
        context,
        output: Math.min(positive(saved.output) ?? FALLBACK_OUTPUT_LIMIT, context),
      }
    })
  }
}

export default {
  id: "neurodesktop-ollama-config",
  async setup(ctx) {
    const snapshot = readSnapshot()
    if (!snapshot) return
    await ctx.model.transform((editor) => applyOllamaSnapshot(editor, snapshot))
  },
}
