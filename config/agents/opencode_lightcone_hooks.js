import { spawn } from "node:child_process"
import { resolve } from "node:path"

const DEFAULT_PLUGIN_ROOT =
  "/opt/neurodesktop/agent-skills/plugins/reproduction"

function hookRoot() {
  return process.env.NEURODESKTOP_LIGHTCONE_PLUGIN_ROOT || DEFAULT_PLUGIN_ROOT
}

function additionalContext(stdout) {
  try {
    const payload = JSON.parse(stdout)
    const context = payload?.hookSpecificOutput?.additionalContext
    return typeof context === "string" ? context.trim() : ""
  } catch {
    return ""
  }
}

function runHook(script, payload, cwd, timeoutMs) {
  const root = hookRoot()
  return new Promise((resolve) => {
    const child = spawn("bash", [`${root}/hooks/scripts/${script}`], {
      cwd,
      env: {
        ...process.env,
        CLAUDE_PLUGIN_ROOT: root,
        PLUGIN_ROOT: root,
      },
      stdio: ["pipe", "pipe", "ignore"],
    })
    let stdout = ""
    const timer = setTimeout(() => child.kill(), timeoutMs)

    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk) => {
      stdout += chunk
    })
    child.on("error", () => {
      clearTimeout(timer)
      resolve("")
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      resolve(code === 0 ? additionalContext(stdout) : "")
    })
    child.stdin.end(JSON.stringify(payload))
  })
}

function toolPayload(event, directory) {
  const input = event.input && typeof event.input === "object" ? event.input : {}
  const filePath =
    typeof input.path === "string" ? resolve(directory, input.path) : undefined
  return {
    cwd: directory,
    session_id: event.sessionID,
    tool_name: event.tool,
    tool_input: {
      ...input,
      file_path: filePath,
    },
    tool_response: {
      filePath,
    },
  }
}

function appendContext(event, context) {
  if (!context || event.status !== "completed") return
  const content = event.result.content
  event.result = {
    ...event.result,
    content: Array.isArray(content)
      ? [...content, { type: "text", text: context }]
      : content
        ? `${content}\n\n${context}`
        : context,
  }
}

export default {
  id: "neurodesktop-lightcone-hooks",
  async setup(ctx) {
    const directory = ctx.location.directory
    // OpenCode has no SessionStart context channel. The session context hook
    // is the durable equivalent: the upstream hook runs once when the plugin
    // loads and its result reaches every model request in this location.
    const sessionContext = await runHook(
      "astra-session-start.sh",
      { cwd: directory },
      directory,
      15_000,
    )

    await ctx.session.hook("context", (event) => {
      if (sessionContext) event.system.push({ type: "text", text: sessionContext })
    })
    await ctx.tool.hook("execute.after", async (event) => {
      const script =
        event.tool === "read"
          ? "activate-on-read.sh"
          : event.tool === "write" || event.tool === "edit"
            ? "validate-on-save.sh"
            : undefined
      if (!script) return

      const context = await runHook(
        script,
        toolPayload(event, directory),
        directory,
        script === "activate-on-read.sh" ? 5_000 : 30_000,
      )
      appendContext(event, context)
    })
  },
}
